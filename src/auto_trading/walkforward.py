"""Walk-forward(학습·검증 롤링) 검증.

과거 전체 기간에서 가장 수익률이 높았던 조건 하나를 뽑는 것이 목적이
아니다. 시세를 학습기간(In-Sample)과 검증기간(Out-of-Sample)으로
나누고, 학습기간에서 찾은 조건을 검증기간의 데이터는 전혀 보지 않은
채로 그대로 적용해서, 그 조건이 처음 보는 기간에서도 반복해서
작동하는지를 본다. 검증기간이 끝나면 창을 한 칸(`step_years`) 밀어서
다음 학습·검증 쌍을 만든다(rolling).

```
[3년 학습] → [1년 검증]
    ↓ 1년 이동
  [3년 학습] → [1년 검증]
```

**학습·검증 기간 길이와 이동 간격은 전부 파라미터다.** 3년·1년·1년은
`WalkForwardConfig`의 기본값일 뿐이고 코드에 박아 두지 않았다. 나중에
2년/4년, 6개월 같은 값으로 쉽게 다시 시험할 수 있어야 한다는 요청을
따른 것이다.

**폴드마다 검증기간에 적용하는 조건은 그 폴드의 학습기간에서만
고른다.** 두 번째 폴드의 검증기간이 첫 번째 폴드의 학습기간과 날짜가
겹치더라도, 조건을 고르는 데는 그 폴드 자신의 학습기간만 쓴다.

**전체 OOS 성과는 검증기간들을 이어 붙여서 계산한다.** 폴드마다 그
폴드가 끝난 시점의 총자산을 다음 폴드의 시작 자본으로 넘겨서
(`chain_capital`), 실제로 쭉 이어서 운용했다면 어땠을지에 가깝게
만든다. 이와 별도로, 폴드끼리 서로 비교할 수 있도록 폴드마다 같은
시작 자본(`capital`)으로 독립적으로 계산한 성과도 남긴다. 둘은
목적이 다르다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .backtest import build_strategy, run_backtest, summarize_result
from .metrics import extended_metrics
from .optimize import build_strategy_configs

#: 사람이 읽는 날짜 형식으로 통일해서 JSON에 남긴다.
_DATE_FMT = "%Y-%m-%d"


@dataclass
class WalkForwardConfig:
    in_sample_years: float = 3.0
    out_sample_years: float = 1.0
    step_years: float = 1.0

    def __post_init__(self) -> None:
        if self.in_sample_years <= 0 or self.out_sample_years <= 0 or self.step_years <= 0:
            raise ValueError("학습기간·검증기간·이동간격은 전부 0보다 커야 합니다.")


@dataclass
class Fold:
    index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def _add_years(d: date, years: float) -> date:
    """달력 월 단위가 아니라 날수(365.25일 × 연수)로 더한다. 6개월처럼
    1년 미만 구간도 다뤄야 해서(요청에 나온 예), 개월 단위로는 못
    나눈다."""
    return d + timedelta(days=round(years * 365.25))


def generate_folds(min_date: date, max_date: date, config: WalkForwardConfig) -> list[Fold]:
    """실제로 있는 시세 날짜 범위 안에서만 폴드를 만든다. 학습+검증
    기간을 다 채울 수 없으면 거기서 멈춘다(모자란 폴드를 억지로 만들지
    않는다)."""
    folds: list[Fold] = []
    train_start = min_date
    idx = 1
    while True:
        train_end = _add_years(train_start, config.in_sample_years)
        test_end = _add_years(train_end, config.out_sample_years)
        if test_end > max_date:
            break
        folds.append(Fold(idx, train_start, train_end, train_end, test_end))
        train_start = _add_years(train_start, config.step_years)
        idx += 1
    return folds


def _slice(prices: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """[start, end) 반개구간. 학습기간의 끝날과 검증기간의 시작날이
    같은 날(`train_end == test_start`)이라, 이렇게 나눠야 그 하루를
    두 구간에 겹쳐 쓰거나 아예 빠뜨리지 않는다."""
    mask = (prices["trade_date"] >= start) & (prices["trade_date"] < end)
    return prices[mask].reset_index(drop=True)


def _period_dict(start: date, end: date) -> dict:
    return {"시작": start.strftime(_DATE_FMT), "종료": end.strftime(_DATE_FMT)}


def run_walk_forward(
    symbol: str,
    prices: pd.DataFrame,
    capital: float,
    search: dict,
    config: WalkForwardConfig,
    select_metric: str = "수익률",
) -> dict:
    """폴드마다 학습기간에서 `select_metric` 기준 1위 조건을 고르고,
    그 조건을 검증기간에 그대로 적용한다. 반환값은 그대로 결과 JSON에
    들어갈 수 있는 모양이다."""
    prices = prices.sort_values("trade_date").reset_index(drop=True)
    min_date = prices["trade_date"].min()
    max_date = prices["trade_date"].max()

    folds = generate_folds(min_date, max_date, config)
    if not folds:
        raise ValueError(
            f"시세 기간이 부족합니다({min_date} ~ {max_date}). "
            f"학습 {config.in_sample_years}년 + 검증 {config.out_sample_years}년짜리 "
            "구간을 하나도 만들 수 없습니다. 기간을 줄이거나 시세를 더 받으세요."
        )

    configs = build_strategy_configs(search)

    fold_rows: list[dict] = []
    chain_capital = capital
    chained_series: list[pd.DataFrame] = []

    for fold in folds:
        train_prices = _slice(prices, fold.train_start, fold.train_end)
        test_prices = _slice(prices, fold.test_start, fold.test_end)
        if train_prices.empty or test_prices.empty:
            continue

        # 학습기간: 이 구간의 데이터만으로 조합 전부를 계산해서 1위를 고른다.
        best = None
        for cfg in configs:
            strategy = build_strategy(cfg, capital)
            result = run_backtest(train_prices, capital=capital, strategy=strategy)
            summary = summarize_result(symbol, strategy, capital, result)
            if best is None or summary[select_metric] > best["summary"][select_metric]:
                best = {"config": cfg, "summary": summary, "result": result}

        train_metrics = extended_metrics(best["result"], capital)

        # 검증기간: 학습기간에서 고른 조건을 손대지 않고 그대로 적용한다.
        # 이 데이터는 조건을 고르는 데 전혀 쓰지 않았다.
        test_strategy = build_strategy(best["config"], capital)
        test_result = run_backtest(test_prices, capital=capital, strategy=test_strategy)
        test_metrics = extended_metrics(test_result, capital)

        # 전체 OOS 연결용: 이전 폴드가 끝난 자산을 이번 폴드의 시작 자본으로 쓴다.
        chained_strategy = build_strategy(best["config"], chain_capital)
        chained_result = run_backtest(test_prices, capital=chain_capital, strategy=chained_strategy)
        if not chained_result.empty:
            chain_capital = float(chained_result.iloc[-1]["total_value"])
            chained_series.append(
                chained_result[["trade_date", "total_value", "buy_amount", "sell_amount", "sell_type"]]
            )

        fold_rows.append(
            {
                "폴드": fold.index,
                "학습기간": _period_dict(fold.train_start, fold.train_end),
                "검증기간": _period_dict(fold.test_start, fold.test_end),
                "선정조건": {
                    "전략키": best["config"]["key"],
                    "설명": best["summary"]["strategy_name"],
                    "설정": {k: v for k, v in best["config"].items() if k not in ("key", "label")},
                },
                "학습기간_성과": train_metrics,
                "검증기간_성과": test_metrics,
            }
        )

    if not fold_rows:
        raise ValueError("폴드는 만들어졌지만 학습·검증 구간 어디에도 시세가 없습니다.")

    combined = pd.concat(chained_series, ignore_index=True) if chained_series else pd.DataFrame()
    combined_metrics = extended_metrics(combined, capital)
    combined_series = [
        {
            "trade_date": str(row["trade_date"]),
            "total_value": round(row["total_value"]),
            "buy_amount": round(row["buy_amount"]) if row["buy_amount"] else 0,
            "sell_amount": round(row["sell_amount"]) if row["sell_amount"] else 0,
            "sell_type": row["sell_type"] if pd.notna(row["sell_type"]) else None,
        }
        for _, row in combined.iterrows()
    ]

    return {
        "폴드별_결과": fold_rows,
        "전체_OOS_성과": combined_metrics,
        "전체_OOS_시계열": combined_series,
    }


# ── 결과 화면이 계산 없이 답할 수 있어야 하는 것들 ──────────
# 위 run_walk_forward()는 폴드별 계산만 한다. 아래 함수들은 그 결과를
# 사람이 눈으로 판단하기 쉽게 요약만 할 뿐 새로 백테스트를 돌리지
# 않는다. "좋다/나쁘다"를 판정하지 않고, 세는 것과 견주는 것만 한다
# (2026-09-22에 추가).


def _configs_equal(a: dict, b: dict) -> bool:
    """전략 파라미터가 실제로 같은지 비교한다. 설명 문자열이 아니라
    전략키와 설정값 자체로 비교해야, 서식만 다른 두 문자열을 다른
    조건으로 잘못 세지 않는다."""
    return a.get("전략키") == b.get("전략키") and a.get("설정") == b.get("설정")


def count_strategy_changes(folds: list[dict]) -> int:
    """이전 폴드와 선정 조건(전략키+파라미터)이 달라진 횟수. 폴드가
    하나뿐이거나 없으면 0이다."""
    changes = 0
    for i in range(1, len(folds)):
        if not _configs_equal(folds[i]["선정조건"], folds[i - 1]["선정조건"]):
            changes += 1
    return changes


def count_oos_sign(folds: list[dict]) -> dict:
    """검증기간 누적수익률이 양수·음수였던 폴드 수. 0%인 폴드는 둘 다
    아니다(있는 그대로 뺀다)."""
    positive = sum(
        1 for f in folds if (r := f["검증기간_성과"]["누적수익률"]) is not None and r > 0
    )
    negative = sum(
        1 for f in folds if (r := f["검증기간_성과"]["누적수익률"]) is not None and r < 0
    )
    return {"양수": positive, "음수": negative, "전체": len(folds)}


def summarize_strategy_selections(folds: list[dict]) -> list[dict]:
    """같은 조건(전략키+파라미터)이 여러 폴드에서 뽑혔으면 하나로 묶어서
    선정 횟수·비율·평균 성과를 낸다. 선정 횟수가 많은 순으로 정렬한다.
    "Walk-forward를 돌렸을 때 어떤 전략이 반복해서 선택됐는가"를 한눈에
    보기 위한 표다."""
    groups: list[dict] = []
    for fold in folds:
        sel = fold["선정조건"]
        match = next((g for g in groups if _configs_equal(g["선정조건"], sel)), None)
        if match is None:
            match = {"선정조건": sel, "폴드들": []}
            groups.append(match)
        match["폴드들"].append(fold)

    total = len(folds)
    rows = []
    for g in groups:
        folds_here = g["폴드들"]
        oos_returns = [f["검증기간_성과"]["누적수익률"] for f in folds_here if f["검증기간_성과"]["누적수익률"] is not None]
        train_returns = [f["학습기간_성과"]["누적수익률"] for f in folds_here if f["학습기간_성과"]["누적수익률"] is not None]
        rows.append(
            {
                "전략": g["선정조건"]["설명"],
                "선정횟수": len(folds_here),
                "선정비율": round(len(folds_here) / total * 100, 1) if total else None,
                "평균_검증_수익률": round(sum(oos_returns) / len(oos_returns), 2) if oos_returns else None,
                "OOS_양수_횟수": sum(1 for r in oos_returns if r > 0),
                "OOS_음수_횟수": sum(1 for r in oos_returns if r < 0),
                "평균_학습_수익률": round(sum(train_returns) / len(train_returns), 2) if train_returns else None,
            }
        )
    rows.sort(key=lambda r: r["선정횟수"], reverse=True)
    return rows


def build_strategy_change_history(folds: list[dict]) -> list[dict]:
    """시간 순서대로 각 폴드의 검증기간·선정 조건과, 바로 앞 폴드 대비
    상태('최초'/'동일'/'변경')를 낸다."""
    history = []
    prev = None
    for fold in folds:
        sel = fold["선정조건"]
        if prev is None:
            status = "최초"
        elif _configs_equal(sel, prev):
            status = "동일"
        else:
            status = "변경"
        history.append({"검증기간": fold["검증기간"], "선정조건": sel["설명"], "상태": status})
        prev = sel
    return history


#: 폴드가 이보다 적으면 화면에 표본이 적다는 안내를 띄운다. "이 숫자면
#: 통계적으로 충분하다"는 주장이 아니라, 그 아래에서는 안내가 필요하다고
#: 정한 값일 뿐이다.
MIN_RECOMMENDED_FOLDS = 3


def fold_count_warning(fold_count: int) -> str | None:
    """폴드 수가 적으면 안내 문구를, 충분하면 None을 돌려준다. "통계적으로
    충분하다"처럼 단정하는 말은 쓰지 않는다."""
    if fold_count >= MIN_RECOMMENDED_FOLDS:
        return None
    return (
        f"검증 폴드가 {fold_count}개입니다. 현재 기간에서는 Walk-forward 검증 표본이 적어 "
        "전략의 안정성을 판단하기에 제한적일 수 있습니다."
    )


def build_summary_stats(folds: list[dict]) -> dict:
    """위 함수들을 한 번에 묶어서 결과 JSON에 그대로 실을 수 있는
    모양으로 낸다."""
    return {
        "전략변경횟수": count_strategy_changes(folds),
        "OOS_폴드수": count_oos_sign(folds),
        "전략반복선정": summarize_strategy_selections(folds),
        "전략변경이력": build_strategy_change_history(folds),
        "폴드수경고": fold_count_warning(len(folds)),
    }
