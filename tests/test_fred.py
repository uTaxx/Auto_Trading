from auto_trading.fred import _parse_observations


def test_정상_응답을_표로_바꾼다():
    payload = {"observations": [{"date": "2024-01-02", "value": "3.95"}, {"date": "2024-01-03", "value": "4.02"}]}
    df = _parse_observations(payload)
    assert len(df) == 2
    assert df.iloc[0]["close"] == 3.95
    assert df.iloc[0]["open"] == 3.95
    assert df.iloc[0]["volume"] == 0


def test_결측값_점은_뺀다():
    payload = {"observations": [{"date": "2024-01-01", "value": "."}, {"date": "2024-01-02", "value": "3.95"}]}
    df = _parse_observations(payload)
    assert len(df) == 1
    assert str(df.iloc[0]["trade_date"]) == "2024-01-02"


def test_관측이_없으면_빈_표다():
    df = _parse_observations({"observations": []})
    assert df.empty
