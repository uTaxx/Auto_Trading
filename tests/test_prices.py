from datetime import date

import pandas as pd

from auto_trading.prices import merge_price_data


def _df(rows: list[tuple[date, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [r[0] for r in rows],
            "open": [r[1] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[1] for r in rows],
            "close": [r[1] for r in rows],
            "volume": [1000 for _ in rows],
        }
    )


def test_새_날짜를_뒤에_붙인다():
    existing = _df([(date(2026, 1, 2), 100.0), (date(2026, 1, 3), 101.0)])
    new = _df([(date(2026, 1, 4), 102.0)])
    merged = merge_price_data(existing, new)
    assert list(merged["trade_date"]) == [date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)]


def test_겹치는_날짜는_새_값이_이긴다():
    existing = _df([(date(2026, 1, 2), 100.0)])
    new = _df([(date(2026, 1, 2), 999.0)])
    merged = merge_price_data(existing, new)
    assert len(merged) == 1
    assert merged.iloc[0]["close"] == 999.0


def test_기존이_비었으면_새_것만_남는다():
    existing = pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "volume"])
    new = _df([(date(2026, 1, 2), 100.0)])
    merged = merge_price_data(existing, new)
    assert len(merged) == 1


def test_새로_받은_것이_없으면_기존_그대로다():
    existing = _df([(date(2026, 1, 2), 100.0)])
    new = pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "volume"])
    merged = merge_price_data(existing, new)
    assert len(merged) == 1
