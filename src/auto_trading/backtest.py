"""매수 방식을 바꿔 가며 과거 시세에 적용해 보는 백테스트 엔진.

매수 방식(`BuyPlan`)과 계산 엔진(`run_backtest`)을 나눴다. 매수 방식은
"오늘 얼마를 살 것인가"만 답하는 순수 함수에 가깝게 만들어서, 새 방식을
추가할 때 엔진을 건드리지 않아도 된다.

**단순화한 것 하나.** 주식 수를 정수로 끊지 않는다(소수 단위 매수를
허용한다). 적립식 매수는 "이 금액만큼 산다"는 것이 핵심이라, 정수
주수로 끊으면 매번 남는 잔돈을 어떻게 할지가 계산을 복잡하게 만든다.
실제 매매로 연결할 때는 이 가정을 다시 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd


class BuyPlan(Protocol):
    def decide(self, today: pd.Series, history: pd.DataFrame, day_index: int, cash: float) -> float:
        """오늘 살 금액(원)을 돌려준다. 0이면 안 산다.

        `history`는 오늘까지 포함한 시세다(이동평균 등을 이 안에서
        직접 계산한다). `cash`는 오늘 시점에 남은 현금이다."""
        ...


@dataclass
class LumpSum:
    """일회성 매수. 첫날에 정해 둔 금액을 한 번에 산다."""

    amount: float

    def decide(self, today: pd.Series, history: pd.DataFrame, day_index: int, cash: float) -> float:
        return self.amount if day_index == 0 else 0.0


@dataclass
class PeriodicDCA:
    """적립식 매수. 일정 거래일 간격으로 같은 금액을 산다."""

    amount: float
    interval_days: int = 21  # 기본은 한 달(약 21거래일)마다

    def decide(self, today: pd.Series, history: pd.DataFrame, day_index: int, cash: float) -> float:
        return self.amount if day_index % self.interval_days == 0 else 0.0


@dataclass
class MovingAverageDCA:
    """적립식 매수 + 이동평균선 조건. 정해진 날에도 조건을 만족해야 산다."""

    amount: float
    ma_window: int
    interval_days: int = 21
    buy_when: str = "below"  # "below"면 이평선 아래일 때만, "above"면 위일 때만

    def decide(self, today: pd.Series, history: pd.DataFrame, day_index: int, cash: float) -> float:
        if day_index % self.interval_days != 0:
            return 0.0
        if len(history) < self.ma_window:
            return 0.0
        ma = history["close"].tail(self.ma_window).mean()
        close = today["close"]
        matched = close < ma if self.buy_when == "below" else close > ma
        return self.amount if matched else 0.0


@dataclass
class ConditionalDCA:
    """최근 평균 주가 대비 등락률에 따라 매수 금액을 조절한다.

    `tiers`는 (기준 등락률, 매수 금액) 쌍의 목록이다. 등락률이 음수면
    "이만큼 떨어지면"이고, 양수면 "이만큼 오르면"이다. 예:
    `[(-0.03, 100_000), (-0.05, 200_000)]`은 -3% 하락에 10만원,
    -5% 하락에 20만원을 뜻한다. 여러 기준을 동시에 만족하면(더 크게
    떨어지면 작은 기준도 같이 만족한다) 그중 매수 금액이 가장 큰 것을
    쓴다."""

    tiers: list[tuple[float, float]]
    lookback_days: int
    interval_days: int = 21

    def decide(self, today: pd.Series, history: pd.DataFrame, day_index: int, cash: float) -> float:
        if day_index % self.interval_days != 0:
            return 0.0
        if len(history) <= self.lookback_days:
            return 0.0
        reference = history["close"].iloc[-(self.lookback_days + 1)]
        if reference == 0:
            return 0.0
        change = today["close"] / reference - 1

        matched_amounts = [
            amount
            for threshold, amount in self.tiers
            if (threshold < 0 and change <= threshold) or (threshold > 0 and change >= threshold)
        ]
        return max(matched_amounts) if matched_amounts else 0.0


@dataclass
class Strategy:
    key: str
    name: str
    buy_plan: BuyPlan
    take_profit_pct: float | None = None  # 예: 0.2는 +20%에서 전량 매도


def run_backtest(prices: pd.DataFrame, capital: float, strategy: Strategy) -> pd.DataFrame:
    """하루 단위로 계산해서 한 줄씩 쌓는다. 반환값의 각 줄이 그날의 상태다."""
    prices = prices.sort_values("trade_date").reset_index(drop=True)
    cash = capital
    shares = 0.0
    avg_cost = 0.0
    invested_cumulative = 0.0
    realized_pnl = 0.0
    rows = []

    for i, row in prices.iterrows():
        close = float(row["close"])

        if shares > 0 and strategy.take_profit_pct is not None and avg_cost > 0:
            ret = close / avg_cost - 1
            if ret >= strategy.take_profit_pct:
                proceeds = shares * close
                realized_pnl += proceeds - shares * avg_cost
                cash += proceeds
                shares = 0.0
                avg_cost = 0.0

        history = prices.iloc[: i + 1]
        buy_amount = strategy.buy_plan.decide(row, history, i, cash)
        buy_amount = max(0.0, min(buy_amount, cash))
        if buy_amount > 0 and close > 0:
            bought_shares = buy_amount / close
            new_shares = shares + bought_shares
            avg_cost = (avg_cost * shares + buy_amount) / new_shares
            shares = new_shares
            cash -= buy_amount
            invested_cumulative += buy_amount

        market_value = shares * close
        unrealized_pnl = market_value - shares * avg_cost
        rows.append(
            {
                "trade_date": row["trade_date"],
                "close": close,
                "cash": cash,
                "shares": shares,
                "avg_cost": avg_cost,
                "invested_cumulative": invested_cumulative,
                "market_value": market_value,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "total_pnl": realized_pnl + unrealized_pnl,
                "total_value": cash + market_value,
            }
        )

    return pd.DataFrame(rows)


def make_lump_sum(capital: float, take_profit_pct: float | None = None) -> Strategy:
    return Strategy(key="lump_sum", name="일회 매수", buy_plan=LumpSum(capital), take_profit_pct=take_profit_pct)


def make_dca(capital: float, periods: int = 12, interval_days: int = 21, take_profit_pct: float | None = None) -> Strategy:
    amount = capital / periods
    return Strategy(
        key="dca",
        name="적립식 매수",
        buy_plan=PeriodicDCA(amount=amount, interval_days=interval_days),
        take_profit_pct=take_profit_pct,
    )


def make_dca_ma(
    capital: float,
    periods: int = 12,
    interval_days: int = 21,
    ma_window: int = 60,
    buy_when: str = "below",
    take_profit_pct: float | None = None,
) -> Strategy:
    amount = capital / periods
    return Strategy(
        key="dca_ma",
        name=f"적립식 매수 + {ma_window}일 이동평균선 {'아래' if buy_when == 'below' else '위'}",
        buy_plan=MovingAverageDCA(amount=amount, ma_window=ma_window, interval_days=interval_days, buy_when=buy_when),
        take_profit_pct=take_profit_pct,
    )


def make_drop_based(
    capital: float,
    periods: int = 12,
    interval_days: int = 21,
    lookback_days: int = 20,
    take_profit_pct: float | None = None,
) -> Strategy:
    """하락폭이 클수록 더 많이 사는 기본 조합. 기준은 1배·1.5배·2배 매수다."""
    base = capital / periods
    tiers = [(-0.03, base), (-0.05, base * 1.5), (-0.08, base * 2)]
    return Strategy(
        key="drop_based",
        name="하락률 기준 비중 조절 매수",
        buy_plan=ConditionalDCA(tiers=tiers, lookback_days=lookback_days, interval_days=interval_days),
        take_profit_pct=take_profit_pct,
    )


#: 대시보드나 스크립트가 이름으로 골라 쓰는 자리. 최대 5개까지 비교하는
#: 것이 원래 요청이라, 지금 다섯 개를 기본으로 둔다.
STRATEGY_FACTORIES = {
    "lump_sum": make_lump_sum,
    "dca": make_dca,
    "dca_ma_below": lambda capital, **kw: make_dca_ma(capital, buy_when="below", **kw),
    "dca_ma_above": lambda capital, **kw: make_dca_ma(capital, buy_when="above", **kw),
    "drop_based": make_drop_based,
}
