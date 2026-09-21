from datetime import date, timedelta

import pandas as pd
import pytest

from auto_trading.optimize import MAX_COMBINATIONS, build_strategy_configs, run_search


def _prices(closes: list[float]) -> pd.DataFrame:
    start = date(2024, 1, 2)
    return pd.DataFrame(
        {
            "trade_date": [start + timedelta(days=i) for i in range(len(closes))],
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1000] * len(closes),
        }
    )


def test_적립식_매수_후보는_금액과_간격의_곱만큼_나온다():
    configs = build_strategy_configs({"dca": {"amount": [100_000, 200_000], "interval_days": [1, 5]}})
    assert len(configs) == 4
    assert {(c["amount"], c["interval_days"]) for c in configs} == {
        (100_000, 1), (100_000, 5), (200_000, 1), (200_000, 5),
    }
    assert all(c["key"] == "dca" for c in configs)
    assert all("label" in c for c in configs)


def test_이동평균_전략은_아래_위_후보를_따로_받는다():
    configs = build_strategy_configs(
        {
            "dca_ma": {
                "ma_window": [60],
                "below_amount": [100_000, 200_000],
                "below_interval_days": [1],
            }
        }
    )
    # ma_window 1개 * below_amount 2개 = 2 (above는 후보가 없어서 배수 1)
    assert len(configs) == 2
    assert {c["below_amount"] for c in configs} == {100_000, 200_000}
    assert all("above_amount" not in c for c in configs)


def test_이동평균_전략은_금액만_주고_빈도를_안_주면_오류를_낸다():
    with pytest.raises(ValueError, match="전부 채우거나 전부 비워야"):
        build_strategy_configs({"dca_ma": {"ma_window": [60], "below_amount": [100_000]}})


def test_익절_손절_후보는_전략마다_따로_받고_다른_변수처럼_조합에_곱해진다():
    configs = build_strategy_configs(
        {"dca": {"amount": [100_000], "interval_days": [1, 5], "take_profit_pct": [0.1, 0.2], "stop_loss_pct": [0.05]}}
    )
    # 간격 2개 * 익절 2개 * 손절 1개 = 4
    assert len(configs) == 4
    assert {c["take_profit_pct"] for c in configs} == {0.1, 0.2}
    assert all(c["stop_loss_pct"] == 0.05 for c in configs)


def test_전략마다_다른_익절_후보를_줄_수_있다():
    configs = build_strategy_configs(
        {
            "lump_sum": {"take_profit_pct": [0.3]},
            "dca": {"amount": [100_000], "interval_days": [1], "take_profit_pct": [0.1, 0.2]},
        }
    )
    lump_sum_configs = [c for c in configs if c["key"] == "lump_sum"]
    dca_configs = [c for c in configs if c["key"] == "dca"]
    assert {c["take_profit_pct"] for c in lump_sum_configs} == {0.3}
    assert {c["take_profit_pct"] for c in dca_configs} == {0.1, 0.2}


def test_익절_손절을_안_주면_설정에_안_들어가고_조합도_안_늘어난다():
    configs = build_strategy_configs({"dca": {"amount": [100_000], "interval_days": [1]}})
    assert len(configs) == 1
    assert "take_profit_pct" not in configs[0]
    assert "stop_loss_pct" not in configs[0]


def test_익절_손절을_빈_목록으로_줘도_안_주는_것과_같다():
    configs = build_strategy_configs(
        {"dca": {"amount": [100_000], "interval_days": [1], "take_profit_pct": [], "stop_loss_pct": []}}
    )
    assert len(configs) == 1
    assert "take_profit_pct" not in configs[0]
    assert "stop_loss_pct" not in configs[0]


def test_등락반영_후보는_구간_하나짜리_tiers로_바뀐다():
    configs = build_strategy_configs(
        {
            "drop_based": {
                "interval_days": [1],
                "lookback_days": [1],
                "threshold_pct": [-5],
                "amount": [200_000],
            }
        }
    )
    assert len(configs) == 1
    assert configs[0]["tiers"] == [[-5, 200_000]]
    assert "threshold_pct" not in configs[0]
    assert "amount" not in configs[0]


def test_일회매수는_변수가_없어서_조합이_하나뿐이다():
    configs = build_strategy_configs({"lump_sum": {}})
    assert len(configs) == 1
    assert configs[0]["key"] == "lump_sum"


def test_조합이_너무_많으면_오류를_낸다():
    big_list = list(range(1, 20))  # 19개
    with pytest.raises(ValueError, match="너무 많습니다"):
        build_strategy_configs({"dca": {"amount": big_list, "interval_days": big_list}})  # 19*19=361 > 200


def test_조합이_상한_이하면_통과한다():
    values = list(range(1, 11))  # 10개
    configs = build_strategy_configs({"dca": {"amount": values, "interval_days": values}})  # 100 <= 200
    assert len(configs) == 100
    assert len(configs) <= MAX_COMBINATIONS


def test_검색_결과는_수익률_내림차순으로_정렬된다():
    # 계속 오르기만 하는 시세: 매수 간격이 짧을수록(매일 살수록) 더 일찍,
    # 더 많이 사게 되어 수익률이 높아야 한다.
    prices = _prices([100.0 + i for i in range(30)])
    configs = build_strategy_configs({"dca": {"amount": [100_000], "interval_days": [1, 3, 10]}})
    rows = run_search("TEST", prices, capital=3_000_000, configs=configs)

    assert len(rows) == 3
    returns = [r["수익률"] for r in rows]
    assert returns == sorted(returns, reverse=True)
    # 매일 사는 쪽(interval_days=1)이 가장 많이/일찍 사서 1등이어야 한다
    assert "interval_days=1" in rows[0]["strategy_name"]
