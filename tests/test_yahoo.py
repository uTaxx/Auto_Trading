from auto_trading.yahoo import _parse_chart_response


def test_정상_응답을_표로_바꾼다():
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1735689600, 1735776000],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0, 101.0],
                                "high": [102.0, 103.0],
                                "low": [99.0, 100.0],
                                "close": [101.5, 102.5],
                                "volume": [1000, 1100],
                            }
                        ]
                    },
                }
            ]
        }
    }
    df = _parse_chart_response(payload)
    assert len(df) == 2
    assert df.iloc[0]["close"] == 101.5


def test_결과가_없으면_빈_표다():
    df = _parse_chart_response({"chart": {"result": []}})
    assert df.empty


def test_종가가_없는_날은_뺀다():
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1735689600, 1735776000],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0, None],
                                "high": [102.0, None],
                                "low": [99.0, None],
                                "close": [101.5, None],
                                "volume": [1000, None],
                            }
                        ]
                    },
                }
            ]
        }
    }
    df = _parse_chart_response(payload)
    assert len(df) == 1
