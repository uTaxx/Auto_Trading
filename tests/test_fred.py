from auto_trading.fred import _parse_graph_csv


def test_정상_응답을_표로_바꾼다():
    csv_text = "observation_date,DGS10\n2024-01-02,3.95\n2024-01-03,4.02\n"
    df = _parse_graph_csv(csv_text)
    assert len(df) == 2
    assert df.iloc[0]["close"] == 3.95
    assert df.iloc[0]["open"] == 3.95
    assert df.iloc[0]["volume"] == 0


def test_결측값_점은_뺀다():
    csv_text = "observation_date,DGS10\n2024-01-01,.\n2024-01-02,3.95\n"
    df = _parse_graph_csv(csv_text)
    assert len(df) == 1
    assert str(df.iloc[0]["trade_date"]) == "2024-01-02"


def test_날짜_열_이름이_달라도_첫째_열을_날짜로_본다():
    csv_text = "DATE,FEDFUNDS\n2024-01-01,5.33\n"
    df = _parse_graph_csv(csv_text)
    assert len(df) == 1
    assert df.iloc[0]["close"] == 5.33
