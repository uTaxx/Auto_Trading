"""수익률 하나만으로 조건을 고르지 않기 위한 성과지표들.

`backtest.max_drawdown_pct`는 이미 있어서 그대로 가져다 쓴다. 여기서는
CAGR(연평균 복리수익률), 최대낙폭 회복일수, Calmar Ratio(CAGR를
최대낙폭 크기로 나눈 값), 거래횟수를 더한다. Walk-forward 검증
(`walkforward.py`)이 학습기간·검증기간 성과를 견줄 때 이 전부를 쓴다.

**기존 백테스트·최적 조건 찾기 결과에는 아직 안 넣는다.** 이번 작업은
Walk-forward 검증 하나만 추가하는 것이고, 다른 화면을 같이 크게
바꾸지 않기로 했다(2026-09-22).
"""

from __future__ import annotations

import pandas as pd

from .backtest import max_drawdown_pct


def cagr_pct(total_value: pd.Series, dates: pd.Series, capital: float) -> float | None:
    """연평균 복리수익률(%). 기간이 1년이 안 되면(또는 자본이나 마지막
    값이 0 이하면) 의미가 없어서 None을 돌려준다."""
    if capital <= 0 or len(total_value) == 0:
        return None
    final = float(total_value.iloc[-1])
    if final <= 0:
        return None
    years = (pd.Timestamp(dates.iloc[-1]) - pd.Timestamp(dates.iloc[0])).days / 365.25
    if years <= 0:
        return None
    return round(((final / capital) ** (1 / years) - 1) * 100, 2)


def mdd_recovery_days(total_value: pd.Series, dates: pd.Series) -> int | None:
    """가장 크게 빠졌던 지점(최대낙폭)이 그 전 최고점을 다시 넘어서기까지
    걸린 날수. 한 번도 안 빠졌으면 0, 이 구간이 끝날 때까지 못
    넘어섰으면 None(회복 못 함)을 돌려준다."""
    if len(total_value) == 0:
        return None
    running_max = total_value.cummax()
    drawdown = total_value - running_max
    trough_pos = drawdown.reset_index(drop=True).idxmin()
    if drawdown.iloc[trough_pos] == 0:
        return 0
    peak_before_trough = running_max.iloc[trough_pos]
    after = total_value.iloc[trough_pos:]
    recovered = after[after >= peak_before_trough]
    if recovered.empty:
        return None
    recovery_date = pd.Timestamp(dates.iloc[recovered.index[0]])
    trough_date = pd.Timestamp(dates.iloc[trough_pos])
    return (recovery_date - trough_date).days


def calmar_ratio(cagr_value: float | None, mdd_pct_value: float | None) -> float | None:
    """CAGR을 최대낙폭 크기(절댓값)로 나눈 값. 낙폭이 없거나(0)
    CAGR을 못 구했으면 None이다. 높을수록 "같은 낙폭을 견딘 값어치"가
    크다는 뜻이다."""
    if cagr_value is None or mdd_pct_value in (None, 0):
        return None
    return round(cagr_value / abs(mdd_pct_value), 2)


def trade_count(daily: pd.DataFrame) -> int:
    """매수 또는 매도(익절·손절)가 있었던 날수를 합한 것. 한 날에 매도
    후 매수가 같이 일어나도 이 저장소의 엔진은 매도 당일에는 다시
    안 사므로(전량 매도 뒤 같은 날 매수 로직이 없다) 이중 계산 걱정은
    없다."""
    buys = int((daily["buy_amount"] > 0).sum())
    sells = int((daily["sell_amount"] > 0).sum())
    return buys + sells


def extended_metrics(daily: pd.DataFrame, capital: float) -> dict:
    """구간 하나(학습기간, 검증기간, 또는 이어붙인 전체 OOS)의 성과를
    한 번에 낸다. Walk-forward 결과 화면과 저장 JSON이 이 모양을
    그대로 쓴다."""
    if daily.empty:
        return {
            "누적수익률": None,
            "CAGR": None,
            "최대낙폭": None,
            "최대낙폭회복일수": None,
            "Calmar": None,
            "거래횟수": 0,
        }
    dates = daily["trade_date"]
    total_value = daily["total_value"]
    final = float(total_value.iloc[-1])
    mdd = max_drawdown_pct(total_value)
    cagr_value = cagr_pct(total_value, dates, capital)
    return {
        "누적수익률": round((final / capital - 1) * 100, 2) if capital else None,
        "CAGR": cagr_value,
        "최대낙폭": mdd,
        "최대낙폭회복일수": mdd_recovery_days(total_value, dates),
        "Calmar": calmar_ratio(cagr_value, mdd),
        "거래횟수": trade_count(daily),
    }
