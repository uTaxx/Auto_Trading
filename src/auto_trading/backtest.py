"""매수 방식을 바꿔 가며 과거 시세에 적용해 보는 백테스트 엔진.

매수 방식(`BuyPlan`)과 계산 엔진(`run_backtest`)을 나눴다. 매수 방식은
"오늘 얼마를 살 것인가"만 답하는 순수 함수에 가깝게 만들어서, 새 방식을
추가할 때 엔진을 건드리지 않아도 된다.

**전략의 세부 조건은 전부 사용자가 입력한다.** 회당 매수 금액, 매수 간격,
이동평균 기간, 등락률 구간 같은 값을 이 파일이 몰래 정해서 쓰지 않는다
(2026-09-21에 기본값을 임의로 넣고 계산해서 지적받았다). `build_strategy`가
빠진 값을 오류로 알린다.

**적립식 매수는 총자본을 횟수로 나누지 않고, 회당 금액을 직접 받는다**
(2026-09-21에 바꿨다). "나눠 살 횟수"는 총자본이 얼마인지 먼저 알아야
감이 잡히는 값이라 직관적이지 않다는 지적을 받았다. 회당 금액을 직접
입력하면 총자본을 다 못 쓰고 남길 수도 있고, 기간이 끝나기 전에 현금이
바닥날 수도 있다. 둘 다 계산 결과에 그대로 반영된다.

**손절선(stop_loss_pct)을 익절선과 같은 자리에 추가했다.** 둘 다 선택
값이고 기본값이 없다. 비워 두면 그 조건은 아예 안 쓴다. 두 조건을 같은
날 동시에 만족하는 일은 현실적으로 없지만, 계산은 둘 중 하나라도
만족하면 전량 매도한다.

**단순화한 것 하나.** 주식 수를 정수로 끊지 않는다(소수 단위 매수를
허용한다). 적립식 매수는 "이 금액만큼 산다"는 것이 핵심이라, 정수
주수로 끊으면 매번 남는 잔돈을 어떻게 할지가 계산을 복잡하게 만든다.
실제 매매로 연결할 때는 이 가정을 다시 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

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
    interval_days: int

    def decide(self, today: pd.Series, history: pd.DataFrame, day_index: int, cash: float) -> float:
        return self.amount if day_index % self.interval_days == 0 else 0.0


@dataclass
class MovingAverageDCA:
    """적립식 매수 + 이동평균선 조건.

    이동평균선 아래일 때와 위일 때 매수금액·매수빈도를 각각 따로 둔다
    (2026-09-21에 한쪽만 고르던 것에서 바꿨다. 아래·위 둘 다 사되 금액과
    빈도를 다르게 두고 싶다는 요청이었다). 한쪽 값을 비워 두면(None) 그
    구간에서는 안 산다. 최소 한쪽은 채워져 있어야 한다(안 그러면 아무
    날도 안 사는 전략이 된다. `build_strategy`가 이것을 막는다)."""

    ma_window: int
    below_amount: float | None = None
    below_interval_days: int | None = None
    above_amount: float | None = None
    above_interval_days: int | None = None

    def decide(self, today: pd.Series, history: pd.DataFrame, day_index: int, cash: float) -> float:
        if len(history) < self.ma_window:
            return 0.0
        ma = history["close"].tail(self.ma_window).mean()
        close = today["close"]
        if close < ma and self.below_amount is not None:
            return self.below_amount if day_index % self.below_interval_days == 0 else 0.0
        if close > ma and self.above_amount is not None:
            return self.above_amount if day_index % self.above_interval_days == 0 else 0.0
        return 0.0


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
    interval_days: int

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
    stop_loss_pct: float | None = None  # 예: 0.1은 -10%에서 전량 매도


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
        sell_amount = 0.0
        sell_type = None  # "익절" | "손절" | None

        if shares > 0 and avg_cost > 0:
            ret = close / avg_cost - 1
            hit_take_profit = strategy.take_profit_pct is not None and ret >= strategy.take_profit_pct
            hit_stop_loss = strategy.stop_loss_pct is not None and ret <= -strategy.stop_loss_pct
            if hit_take_profit or hit_stop_loss:
                proceeds = shares * close
                realized_pnl += proceeds - shares * avg_cost
                cash += proceeds
                sell_amount = proceeds
                sell_type = "익절" if hit_take_profit else "손절"
                shares = 0.0
                avg_cost = 0.0

        history = prices.iloc[: i + 1]
        requested_amount = strategy.buy_plan.decide(row, history, i, cash)
        buy_amount = max(0.0, min(requested_amount, cash))
        # 사려던 금액보다 실제로 산 금액이 적으면 그만큼 현금이 모자랐던
        # 것이다(2026-09-22에 추가. 전에는 표에 나오는 "매수금액"이 설정값일
        # 뿐이라, 현금이 부족해서 그보다 적게 샀던 날을 알아볼 길이 없었다).
        shortfall = max(0.0, requested_amount - buy_amount)
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
                "buy_amount": buy_amount,
                "buy_shortfall": shortfall,
                "sell_amount": sell_amount,
                "sell_type": sell_type,
            }
        )

    return pd.DataFrame(rows)


def max_drawdown_pct(total_value: pd.Series) -> float:
    """총자산이 그때까지의 최고점 대비 가장 많이 빠졌던 폭(%). 항상 0
    이하다(안 빠졌으면 0)."""
    running_max = total_value.cummax()
    drawdown = (total_value - running_max) / running_max
    return round(float(drawdown.min()) * 100, 2)


def describe_strategy(strategy: Strategy) -> dict:
    """전략 설정을 화면 표·엑셀에 바로 쓸 수 있는 사람이 읽는 칸으로
    편다(2026-09-21에 더함). `strategy_name`은 조합을 구분하는 원래
    문자열(예: "적립식 매수 (amount=100000, interval_days=5)")이라
    코드를 모르면 읽기 어렵다는 지적을 받았다. 여기서 만드는 값은
    보여주기 전용이고, 계산에는 안 쓴다."""
    plan = strategy.buy_plan
    row: dict = {
        "매수방식": STRATEGY_SCHEMAS.get(strategy.key, {}).get("label", strategy.key),
        "매수금액": None,
        "매수빈도": None,
        "이동평균조건": None,
        "등락구간": None,
    }
    if isinstance(plan, LumpSum):
        row["매수금액"] = round(plan.amount)
    elif isinstance(plan, PeriodicDCA):
        row["매수금액"] = round(plan.amount)
        row["매수빈도"] = plan.interval_days
    elif isinstance(plan, MovingAverageDCA):
        parts = []
        if plan.below_amount is not None:
            parts.append(f"아래 {round(plan.below_amount):,}원/{plan.below_interval_days}일")
        if plan.above_amount is not None:
            parts.append(f"위 {round(plan.above_amount):,}원/{plan.above_interval_days}일")
        row["이동평균조건"] = f"{plan.ma_window}일선, " + ", ".join(parts)
    elif isinstance(plan, ConditionalDCA):
        row["등락구간"] = ", ".join(f"{t * 100:+.1f}%: {round(a):,}원" for t, a in plan.tiers)
    row["익절선"] = f"{strategy.take_profit_pct * 100:.1f}%" if strategy.take_profit_pct is not None else None
    row["손절선"] = f"{strategy.stop_loss_pct * 100:.1f}%" if strategy.stop_loss_pct is not None else None
    return row


def summarize_result(symbol: str, strategy: Strategy, capital: float, result: pd.DataFrame) -> dict:
    """`run_backtest` 결과 한 줄(요약)을 만든다. 화면 표와 엑셀 보고서,
    최적 조건 찾기가 전부 이 함수를 거쳐서, 숫자를 내는 방식이 한 곳에만
    있게 한다."""
    last = result.iloc[-1]
    shortfall_days = int((result["buy_shortfall"] > 0).sum())
    row = {
        "symbol": symbol,
        "strategy_key": strategy.key,
        "strategy_name": strategy.name,
        "총투자금": round(last["invested_cumulative"]),
        "실현손익": round(last["realized_pnl"]),
        "평가손익": round(last["unrealized_pnl"]),
        "합계": round(last["total_pnl"]),
        "수익률": round(last["total_pnl"] / capital * 100, 2),
        "최대낙폭": max_drawdown_pct(result["total_value"]),
        # 사려던 금액보다 현금이 모자라서 설정값대로 못 산 날 수와 그
        # 부족했던 금액의 합. 0이면 기간 내내 설정값 그대로 샀다는 뜻이다.
        "현금부족일수": shortfall_days,
        "현금부족금액": round(float(result["buy_shortfall"].sum())),
    }
    row.update(describe_strategy(strategy))
    return row


#: 화면이 전략마다 어떤 입력칸을 보여 줘야 하는지 적어 둔 자리다.
#: "suggested"는 칸에 미리 채워 두는 시작값일 뿐, 사용자가 직접 눌러서
#: 바꾸지 않으면 그 값 그대로 계산에 쓰인다. 코드가 몰래 다른 값으로
#: 바꿔치기하지 않는다.
STRATEGY_SCHEMAS: dict[str, dict[str, Any]] = {
    "lump_sum": {
        "label": "일회 매수",
        "description": "첫날 전체 자본으로 한 번에 산다.",
        "params": [],
    },
    "dca": {
        "label": "적립식 매수",
        "description": "정해진 금액을 일정 간격마다 산다.",
        "params": [
            {"name": "amount", "label": "회당 매수 금액(원)", "type": "int", "suggested": 100000},
            {"name": "interval_days", "label": "매수빈도(일수, 1이면 매일)", "type": "int", "suggested": 1},
        ],
    },
    "dca_ma": {
        "label": "적립식 매수 + 이동평균선 조건",
        "description": "정해진 날이 와도 이동평균선 조건을 만족해야 산다.",
        "params": [
            {"name": "amount", "label": "회당 매수 금액(원)", "type": "int", "suggested": 100000},
            {"name": "interval_days", "label": "매수 간격(거래일, 1이면 매일)", "type": "int", "suggested": 1},
            {"name": "ma_window", "label": "이동평균 기간(거래일)", "type": "int", "suggested": 60},
            {
                "name": "buy_when",
                "label": "조건",
                "type": "choice",
                "options": [
                    {"value": "below", "label": "이동평균선 아래일 때만"},
                    {"value": "above", "label": "이동평균선 위일 때만"},
                ],
                "suggested": "below",
            },
        ],
    },
    "drop_based": {
        "label": "등락률 기준 비중 조절 매수",
        "description": "최근 평균 주가 대비 등락률 구간마다 매수 금액을 다르게 정한다. 하락 구간뿐 아니라 상승 구간도 넣을 수 있다.",
        "params": [
            {"name": "interval_days", "label": "평가 빈도(거래일, 1이면 매일)", "type": "int", "suggested": 1},
            {"name": "lookback_days", "label": "평가 기준일(몇일전 시세대비, 1이면 전일 대비)", "type": "int", "suggested": 1},
            {
                "name": "tiers",
                "label": "등락률 구간별 매수 금액(등락률%, 금액. 예: -5, 120000 / +5, 80000)",
                "type": "tiers",
                "suggested": [[-5, 120000], [-10, 150000]],
            },
        ],
    },
}


def _require(config: dict, field: str):
    if config.get(field) in (None, ""):
        raise ValueError(f"'{config.get('key')}' 전략에 '{field}' 값이 없습니다. 사용자가 입력해야 합니다.")
    return config[field]


def build_strategy(config: dict, capital: float) -> Strategy:
    """사람이 입력한 값으로 전략을 만든다. 빠진 값은 기본값을 채우지
    않고 오류로 알린다."""
    key = _require(config, "key")
    label = config.get("label") or STRATEGY_SCHEMAS.get(key, {}).get("label", key)
    take_profit_pct = config.get("take_profit_pct")
    stop_loss_pct = config.get("stop_loss_pct")

    if key == "lump_sum":
        return Strategy(
            key=key,
            name=label,
            buy_plan=LumpSum(capital),
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
        )

    if key == "dca":
        amount = _require(config, "amount")
        interval_days = _require(config, "interval_days")
        return Strategy(
            key=key,
            name=label,
            buy_plan=PeriodicDCA(amount=amount, interval_days=interval_days),
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
        )

    if key == "dca_ma":
        ma_window = _require(config, "ma_window")
        below_amount = config.get("below_amount")
        below_interval_days = config.get("below_interval_days")
        above_amount = config.get("above_amount")
        above_interval_days = config.get("above_interval_days")
        if (below_amount in (None, "")) != (below_interval_days in (None, "")):
            raise ValueError("'dca_ma' 전략의 이동평균선 아래 매수금액과 매수빈도는 같이 넣어야 합니다.")
        if (above_amount in (None, "")) != (above_interval_days in (None, "")):
            raise ValueError("'dca_ma' 전략의 이동평균선 위 매수금액과 매수빈도는 같이 넣어야 합니다.")
        if below_amount in (None, "") and above_amount in (None, ""):
            raise ValueError("'dca_ma' 전략은 이동평균선 아래·위 중 최소 한쪽은 매수금액과 매수빈도를 넣어야 합니다.")
        return Strategy(
            key=key,
            name=label,
            buy_plan=MovingAverageDCA(
                ma_window=ma_window,
                below_amount=below_amount or None,
                below_interval_days=below_interval_days or None,
                above_amount=above_amount or None,
                above_interval_days=above_interval_days or None,
            ),
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
        )

    if key == "drop_based":
        interval_days = _require(config, "interval_days")
        lookback_days = _require(config, "lookback_days")
        raw_tiers = _require(config, "tiers")
        # 화면에서는 등락률을 %로 받는다(-3 = -3%, 5 = +5%). 계산은 비율로 한다.
        tiers = [(float(threshold) / 100, float(amount)) for threshold, amount in raw_tiers]
        return Strategy(
            key=key,
            name=label,
            buy_plan=ConditionalDCA(tiers=tiers, lookback_days=lookback_days, interval_days=interval_days),
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
        )

    raise ValueError(f"모르는 전략 키: {key}")
