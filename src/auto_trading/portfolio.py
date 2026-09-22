"""여러 종목이 현금 하나를 나눠 쓰면서 같이 매매하는 백테스트.

`backtest.run_backtest`는 종목 하나가 총자본을 전부 쓴다고 가정한다.
실제 계좌는 종목을 여러 개 들고 가면 현금을 나눠 써야 하는데, 종목마다
따로 `run_backtest`를 돌려 비교하면 모든 종목이 각자 총자본을 통째로
가진 것처럼 계산돼서, 그 결과를 그대로 이어붙여도 실제 계좌에서 같은
성적이 나온다는 보장이 없다(2026-09-22에 사용자가 지적했다. "총투자금"이
총자본보다 훨씬 큰 줄을 보고, 총자본을 무시하고 계속 사는 것 아니냐고
물었다).

이 모듈은 현금 하나를 모든 종목이 공유하게 해서, 한 종목이 현금을 많이
쓰면 같은 날 다른 종목은 그만큼 못 사는 것까지 반영한다. 매수 방식·
익절·손절 규칙은 종목마다 다르게 줄 수 있다(각 종목의 `Strategy`는
`backtest.build_strategy`로 그대로 만든다).

**종목 순서가 결과에 영향을 준다.** 같은 날 여러 종목이 동시에 사려고
하면 `strategy_by_symbol`에 준 순서대로 현금을 먼저 쓴다. 뒤 종목일수록
현금이 부족해서 못 살 가능성이 크다. 이 순서 의존성을 없애는 방법
(예: 요청 비율대로 나눠 주기)은 아직 만들지 않았다.

**같은 날 판 돈을 그날 바로 다른 종목을 사는 데 쓸 수 있다.** 매도
판정을 전부 처리한 뒤에 매수 판정을 하기 때문이다. 실제 계좌에서
매도 대금이 같은 날 바로 재사용 가능한지는 증권사·결제 방식에 따라
다르다(국내 주식은 보통 T+2). 이 백테스트는 그 지연을 반영하지 않는다.
"""

from __future__ import annotations

import pandas as pd

from .backtest import Strategy, max_drawdown_pct


def run_portfolio_backtest(
    prices_by_symbol: dict[str, pd.DataFrame],
    capital: float,
    strategy_by_symbol: dict[str, Strategy],
) -> tuple[pd.DataFrame, dict[str, dict]]:
    """포트폴리오 전체의 하루 단위 결과와, 종목별 최종 성과를 돌려준다.

    거래일은 `prices_by_symbol`에 있는 모든 종목의 거래일을 합친 것을
    쓴다. 한 종목만 상장 전이거나 이미 상장폐지된 날은 그 종목만 그날
    매매 판정에서 빠지고(평가금액은 마지막으로 알려진 가격으로 그대로
    들고 있는 것처럼 계산한다), 다른 종목은 정상적으로 매매한다.
    """
    if not prices_by_symbol:
        raise ValueError("종목이 하나도 없습니다.")
    if set(prices_by_symbol) != set(strategy_by_symbol):
        raise ValueError("prices_by_symbol과 strategy_by_symbol의 종목 구성이 서로 다릅니다.")

    symbols = list(strategy_by_symbol.keys())
    prices_sorted = {s: prices_by_symbol[s].sort_values("trade_date").reset_index(drop=True) for s in symbols}

    all_dates = sorted(set().union(*(set(df["trade_date"]) for df in prices_sorted.values())))
    if not all_dates:
        raise ValueError("계산할 시세가 없습니다.")

    # 종목마다 "그 종목 안에서 몇 번째 거래일인가"를 따로 센다. LumpSum
    # 같은 전략은 day_index == 0인 날에만 사는데, 종목마다 상장일이
    # 다르면 그 "0일째"가 종목마다 다른 달력 날짜여야 한다.
    day_index_by_symbol = {s: {d: i for i, d in enumerate(prices_sorted[s]["trade_date"])} for s in symbols}
    row_by_date_symbol = {s: prices_sorted[s].set_index("trade_date") for s in symbols}

    cash = capital
    shares = dict.fromkeys(symbols, 0.0)
    avg_cost = dict.fromkeys(symbols, 0.0)
    invested_cumulative = dict.fromkeys(symbols, 0.0)
    realized_pnl = dict.fromkeys(symbols, 0.0)
    last_close = dict.fromkeys(symbols, 0.0)

    rows = []
    total_realized_pnl = 0.0

    for d in all_dates:
        day_buy_total = 0.0
        day_shortfall_total = 0.0
        day_sell_total = 0.0

        # 1) 매도 판정을 먼저 한다. 그래야 오늘 판 돈을 오늘 다른 종목을
        #    사는 데 바로 쓸 수 있다.
        for s in symbols:
            if d not in row_by_date_symbol[s].index:
                continue
            close = float(row_by_date_symbol[s].loc[d, "close"])
            last_close[s] = close
            if shares[s] > 0 and avg_cost[s] > 0:
                strategy = strategy_by_symbol[s]
                ret = close / avg_cost[s] - 1
                hit_take_profit = strategy.take_profit_pct is not None and ret >= strategy.take_profit_pct
                hit_stop_loss = strategy.stop_loss_pct is not None and ret <= -strategy.stop_loss_pct
                if hit_take_profit or hit_stop_loss:
                    proceeds = shares[s] * close
                    realized_pnl[s] += proceeds - shares[s] * avg_cost[s]
                    total_realized_pnl += proceeds - shares[s] * avg_cost[s]
                    cash += proceeds
                    day_sell_total += proceeds
                    shares[s] = 0.0
                    avg_cost[s] = 0.0

        # 2) 매수 판정. 종목 순서대로 공유 현금을 먼저 쓴다.
        for s in symbols:
            if d not in row_by_date_symbol[s].index:
                continue
            close = last_close[s]
            i = day_index_by_symbol[s][d]
            history = prices_sorted[s].iloc[: i + 1]
            row = row_by_date_symbol[s].loc[d]
            requested = strategy_by_symbol[s].buy_plan.decide(row, history, i, cash)
            buy_amount = max(0.0, min(requested, cash))
            shortfall = max(0.0, requested - buy_amount)
            day_shortfall_total += shortfall
            if buy_amount > 0 and close > 0:
                bought_shares = buy_amount / close
                new_shares = shares[s] + bought_shares
                avg_cost[s] = (avg_cost[s] * shares[s] + buy_amount) / new_shares
                shares[s] = new_shares
                cash -= buy_amount
                invested_cumulative[s] += buy_amount
                day_buy_total += buy_amount

        market_value_total = sum(shares[s] * last_close[s] for s in symbols)
        unrealized_pnl_total = sum(
            shares[s] * last_close[s] - shares[s] * avg_cost[s] for s in symbols
        )
        invested_total = sum(invested_cumulative.values())

        rows.append(
            {
                "trade_date": d,
                "cash": cash,
                "market_value": market_value_total,
                "invested_cumulative": invested_total,
                "realized_pnl": total_realized_pnl,
                "unrealized_pnl": unrealized_pnl_total,
                "total_pnl": total_realized_pnl + unrealized_pnl_total,
                "total_value": cash + market_value_total,
                "buy_amount": day_buy_total,
                "buy_shortfall": day_shortfall_total,
                "sell_amount": day_sell_total,
            }
        )

    daily = pd.DataFrame(rows)

    symbol_breakdown = {}
    for s in symbols:
        unrealized = shares[s] * last_close[s] - shares[s] * avg_cost[s]
        symbol_breakdown[s] = {
            "총투자금": round(invested_cumulative[s]),
            "실현손익": round(realized_pnl[s]),
            "평가손익": round(unrealized),
            "합계": round(realized_pnl[s] + unrealized),
            "최종보유수량": shares[s],
            "최종평단가": avg_cost[s],
        }

    return daily, symbol_breakdown


def summarize_portfolio_result(daily: pd.DataFrame, capital: float) -> dict:
    """포트폴리오 전체의 요약 한 줄. `backtest.summarize_result`와 같은
    칸 이름을 써서, 화면·엑셀 코드가 종목 하나짜리 결과와 같은 방식으로
    보여줄 수 있게 한다."""
    last = daily.iloc[-1]
    shortfall_days = int((daily["buy_shortfall"] > 0).sum())
    return {
        "총투자금": round(last["invested_cumulative"]),
        "실현손익": round(last["realized_pnl"]),
        "평가손익": round(last["unrealized_pnl"]),
        "합계": round(last["total_pnl"]),
        "수익률": round(last["total_pnl"] / capital * 100, 2),
        "최대낙폭": max_drawdown_pct(daily["total_value"]),
        "현금부족일수": shortfall_days,
        "현금부족금액": round(float(daily["buy_shortfall"].sum())),
    }
