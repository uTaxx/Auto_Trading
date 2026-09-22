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

**익절·손절 후보는 전략별로 따로 받는다**(2026-09-21에 바꿈). 처음에는
검색 전체에 공통으로 적용할 값 하나(또는 후보 목록 하나)를 만들었는데,
주인이 "전략별로 익절선·손절선 옵션을 넣으라고, 이 기본 설정 말고"라고
다시 지적했다. 매수 방식마다 어울리는 익절·손절 폭이 다를 수 있으니,
`take_profit_pct`/`stop_loss_pct`를 다른 변수(회당 매수 금액, 매수
간격 같은 것)와 똑같이 각 전략의 params 목록 안에 둔다. 다만 이 둘은
`optional`이다. 후보를 비워 두면(빈 목록 또는 안 줌) 그 조건 자체를
안 쓴 조합 하나로 본다. 다른 params는 비우면 오류다.

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

# 매수 방식과 상관없이 build_strategy가 항상 받는 값이라, 전략마다 같은
# 두 항목을 params 끝에 붙인다. 값은 비율(0.1 = 10%)로 받는다.
_EXIT_PARAMS: list[dict[str, Any]] = [
    {
        "name": "take_profit_pct",
        "label": "익절선 후보(매수평균가 대비 비율. 예: 0.1 = 10%. 비워 두면 안 씀)",
        "type": "float",
        "optional": True,
        "suggested": [],
    },
    {
        "name": "stop_loss_pct",
        "label": "손절선 후보(매수평균가 대비 비율. 비워 두면 안 씀)",
        "type": "float",
        "optional": True,
        "suggested": [],
    },
]

STRATEGY_SEARCH_SCHEMAS: dict[str, dict[str, Any]] = {
    "lump_sum": {
        "label": "일회 매수",
        "params": [*_EXIT_PARAMS],
    },
    "dca": {
        "label": "적립식 매수",
        "params": [
            {"name": "amount", "label": "회당 매수 금액 후보(원)", "type": "int", "suggested": [50000, 100000, 200000]},
            *_EXIT_PARAMS,
        ],
    },
    "dca_ma": {
        "label": "적립식 매수 + 이동평균선 조건",
        "params": [
            {"name": "ma_window", "label": "이동평균 기간 후보(거래일)", "type": "int", "suggested": [20, 60, 120]},
            {
                "name": "below_amount",
                "label": "이동평균선 아래일 때 매수금액 후보(원) — 비워 두면 이 구간엔 안 삼",
                "type": "int",
                "optional": True,
                "suggested": [50000, 100000, 200000],
            },
            {
                "name": "above_amount",
                "label": "이동평균선 위일 때 매수금액 후보(원) — 비워 두면 이 구간엔 안 삼",
                "type": "int",
                "optional": True,
                "suggested": [],
            },
            *_EXIT_PARAMS,
        ],
    },
    "drop_based": {
        "label": "등락률 기준 비중 조절 매수(구간 하나로 단순화)",
        "params": [
            {"name": "lookback_days", "label": "평가 기준일 후보(몇일전 시세대비)", "type": "int", "suggested": [1, 5, 10]},
            {"name": "threshold_pct", "label": "등락률 임계값 후보(%)", "type": "float", "suggested": [-3, -5, -10]},
            {"name": "amount", "label": "그 구간 매수 금액 후보(원)", "type": "int", "suggested": [100000, 200000, 300000]},
            *_EXIT_PARAMS,
        ],
    },
}


def _combinations(params: list[dict], values: dict[str, list]) -> list[dict]:
    """params 순서대로 후보값을 곱해서 조합 딕셔너리 목록을 만든다.
    optional로 표시된 변수는 후보가 없으면(빈 목록 또는 안 줌) None
    하나짜리 후보로 보고, 조합에도 값 None으로 들어간다(나중에
    build_strategy_configs가 없앤다)."""
    if not params:
        return [{}]
    names = [p["name"] for p in params]
    lists = []
    for p in params:
        candidates = values.get(p["name"])
        if p.get("optional"):
            lists.append(list(candidates) if candidates else [None])
        else:
            lists.append(values[p["name"]])
    return [dict(zip(names, combo)) for combo in product(*lists)]


def _check_pairs(schema: dict, combo: dict, key: str) -> None:
    """같은 "pair" 이름으로 묶인 선택값은 둘 다 있거나 둘 다 없어야
    한다. 하나만 있으면 build_strategy가 나중에 막긴 하지만, 계산을
    시작하기 전에 바로 알려주는 편이 낫다(지금 등록된 전략 중에는
    "pair"를 쓰는 것이 없어서 항상 통과한다. 나중에 짝을 이루는 선택값이
    생기면 쓴다)."""
    pairs: dict[str, list[str]] = {}
    for p in schema["params"]:
        pair_name = p.get("pair")
        if pair_name:
            pairs.setdefault(pair_name, []).append(p["name"])
    for pair_name, names in pairs.items():
        filled = [combo.get(name) is not None for name in names]
        if any(filled) and not all(filled):
            raise ValueError(
                f"'{key}' 전략의 '{pair_name}' 쪽 값({', '.join(names)})은 전부 채우거나 전부 비워야 합니다."
            )


def _label_for(key: str, combo: dict) -> str:
    base = STRATEGY_SEARCH_SCHEMAS[key]["label"]
    active = {k: v for k, v in combo.items() if v is not None}
    if not active:
        return base
    parts = ", ".join(f"{k}={v}" for k, v in active.items())
    return f"{base} ({parts})"


def build_strategy_configs(search: dict[str, dict[str, list]]) -> list[dict]:
    """검색 설정(전략 키 -> {변수명: 후보값 목록})을 build_strategy가 바로
    쓸 수 있는 설정 딕셔너리 목록으로 편다. 익절·손절도 전략별로 다른
    변수(회당 매수 금액 등)와 똑같이 후보값 목록으로 받는다."""
    configs: list[dict] = []
    for key, values in search.items():
        schema = STRATEGY_SEARCH_SCHEMAS.get(key)
        if schema is None:
            raise ValueError(f"모르는 전략 키: {key}")
        for combo in _combinations(schema["params"], values):
            _check_pairs(schema, combo, key)
            active = {k: v for k, v in combo.items() if v is not None}
            config: dict[str, Any] = {"key": key, "label": _label_for(key, combo), **active}
            if key == "drop_based":
                threshold = config.pop("threshold_pct")
                amount = config.pop("amount")
                config["tiers"] = [[threshold, amount]]
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
