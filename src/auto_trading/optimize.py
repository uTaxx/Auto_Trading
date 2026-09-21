"""네 가지 매수 방식을 여러 변수 조합으로 한꺼번에 계산해서, 수익률이
가장 좋은 조합을 찾는다.

**후보값은 화면에 미리 채워 두지만, 완전히 숨겨 두지는 않는다.** 여러
값을 한꺼번에 시험하는 것 자체가 "사용자가 값을 하나씩 입력해야
계산된다"는 backtest.py의 원래 규칙과는 다른, 새로 허락받은 방식이다
(2026-09-21). 후보 목록은 화면에 그대로 보이고 사용자가 바꿀 수 있다
(`STRATEGY_SEARCH_SCHEMAS`의 "suggested"가 그 시작값이다). 계산 결과도
1등만 보여 주지 않고 전부 남긴다.

**조합 수를 제한한다.** 변수를 곱하면 순식간에 수천 개가 될 수 있다.
`MAX_COMBINATIONS`을 넘으면 계산을 시작하지 않고 오류로 막는다.

**등락률 기준 비중 조절은 단순화한다.** 원래는 등락률 구간을 여러 개
넣을 수 있는데(tiers), 후보 조합을 자동으로 만들려면 구간 개수 자체가
변수가 돼서 복잡해진다. 그래서 여기서는 구간 하나짜리로만 시험한다.
여러 구간을 쓰고 싶으면 화면 3번(전략 비교)에서 직접 넣는다.

**익절·손절도 후보값 목록으로 받는다**(2026-09-21에 바꿈). 처음에는
값 하나(또는 안 씀)를 모든 조합에 똑같이 적용했는데, 주인이 "익절선은
각 전략별로 복수로 테스트해야지"라고 지적해서 다른 변수와 같은 방식으로
바꿨다. 후보를 안 주면(빈 목록) 그 조건 자체를 안 쓴 조합 하나만
나온다. 후보를 여러 개 주면 전략마다, 그리고 다른 변수 조합마다 그
후보 수만큼 곱해져서 늘어난다.

**수익률만 보고 고르지 않는다.** 결과마다 최대낙폭도 같이 계산해서
남긴다(`backtest.summarize_result`). 기본 정렬은 수익률 내림차순이지만,
최대낙폭이 큰데도 수익률이 좋다는 이유만으로 1등이 될 수 있으니 숫자를
같이 보고 판단해야 한다.
"""

from __future__ import annotations

from itertools import product
from typing import Any

import pandas as pd

from .backtest import build_strategy, run_backtest, summarize_result

MAX_COMBINATIONS = 200

STRATEGY_SEARCH_SCHEMAS: dict[str, dict[str, Any]] = {
    "lump_sum": {
        "label": "일회 매수",
        "params": [],
    },
    "dca": {
        "label": "적립식 매수",
        "params": [
            {"name": "amount", "label": "회당 매수 금액 후보(원)", "type": "int", "suggested": [50000, 100000, 200000]},
            {"name": "interval_days", "label": "매수 간격 후보(거래일)", "type": "int", "suggested": [1, 5, 10]},
        ],
    },
    "dca_ma": {
        "label": "적립식 매수 + 이동평균선 조건",
        "params": [
            {"name": "amount", "label": "회당 매수 금액 후보(원)", "type": "int", "suggested": [50000, 100000, 200000]},
            {"name": "interval_days", "label": "매수 간격 후보(거래일)", "type": "int", "suggested": [1, 5, 10]},
            {"name": "ma_window", "label": "이동평균 기간 후보(거래일)", "type": "int", "suggested": [20, 60, 120]},
            {"name": "buy_when", "label": "조건 후보", "type": "choice_multi", "suggested": ["below", "above"]},
        ],
    },
    "drop_based": {
        "label": "등락률 기준 비중 조절 매수(구간 하나로 단순화)",
        "params": [
            {"name": "interval_days", "label": "판단 간격 후보(거래일)", "type": "int", "suggested": [1, 5, 10]},
            {"name": "lookback_days", "label": "등락률 기준 기간 후보(거래일)", "type": "int", "suggested": [1, 5, 10]},
            {"name": "threshold_pct", "label": "등락률 임계값 후보(%)", "type": "float", "suggested": [-3, -5, -10]},
            {"name": "amount", "label": "그 구간 매수 금액 후보(원)", "type": "int", "suggested": [100000, 200000, 300000]},
        ],
    },
}


def _combinations(params: list[dict], values: dict[str, list]) -> list[dict]:
    """params 순서대로 후보값을 곱해서 조합 딕셔너리 목록을 만든다."""
    if not params:
        return [{}]
    names = [p["name"] for p in params]
    lists = [values[name] for name in names]
    return [dict(zip(names, combo)) for combo in product(*lists)]


def _label_for(key: str, combo: dict) -> str:
    base = STRATEGY_SEARCH_SCHEMAS[key]["label"]
    if not combo:
        return base
    parts = ", ".join(f"{k}={v}" for k, v in combo.items())
    return f"{base} ({parts})"


def build_strategy_configs(
    search: dict[str, dict[str, list]],
    take_profit_candidates: list[float] | None,
    stop_loss_candidates: list[float] | None,
) -> list[dict]:
    """검색 설정(전략 키 -> {변수명: 후보값 목록})을 build_strategy가 바로
    쓸 수 있는 설정 딕셔너리 목록으로 편다. 익절·손절 후보도 다른 변수와
    똑같이 조합에 곱해진다. 후보를 안 주면(None 또는 빈 목록) 그 조건을
    안 쓴 조합 하나만 나온다."""
    tp_list: list[float | None] = list(take_profit_candidates) if take_profit_candidates else [None]
    sl_list: list[float | None] = list(stop_loss_candidates) if stop_loss_candidates else [None]

    configs: list[dict] = []
    for key, values in search.items():
        schema = STRATEGY_SEARCH_SCHEMAS.get(key)
        if schema is None:
            raise ValueError(f"모르는 전략 키: {key}")
        for combo in _combinations(schema["params"], values):
            for take_profit_pct in tp_list:
                for stop_loss_pct in sl_list:
                    label_parts = dict(combo)
                    if take_profit_pct is not None:
                        label_parts["take_profit_pct"] = take_profit_pct
                    if stop_loss_pct is not None:
                        label_parts["stop_loss_pct"] = stop_loss_pct

                    config: dict[str, Any] = {"key": key, "label": _label_for(key, label_parts), **combo}
                    if key == "drop_based":
                        threshold = config.pop("threshold_pct")
                        amount = config.pop("amount")
                        config["tiers"] = [[threshold, amount]]
                    if take_profit_pct is not None:
                        config["take_profit_pct"] = take_profit_pct
                    if stop_loss_pct is not None:
                        config["stop_loss_pct"] = stop_loss_pct
                    configs.append(config)

    if len(configs) > MAX_COMBINATIONS:
        raise ValueError(
            f"조합이 {len(configs)}개라 너무 많습니다(최대 {MAX_COMBINATIONS}개). "
            "후보값 개수를 줄여 주세요."
        )
    return configs


def run_search(symbol: str, prices: pd.DataFrame, capital: float, configs: list[dict]) -> list[dict]:
    """설정마다 백테스트를 돌려서 요약 한 줄씩 모은다. 수익률 내림차순으로
    정렬해서 돌려준다(1등이 맨 위). 화면(4번 결과 조회)이 그대로 읽을 수
    있도록 summarize_result와 같은 모양의 줄을 쓴다."""
    rows = []
    for config in configs:
        strategy = build_strategy(config, capital)
        result = run_backtest(prices, capital=capital, strategy=strategy)
        rows.append(summarize_result(symbol, strategy, capital, result))
    rows.sort(key=lambda r: r["수익률"], reverse=True)
    return rows
