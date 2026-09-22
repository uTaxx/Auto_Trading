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


def test_적립식_매수_후보는_금액마다_나온다():
    configs = build_strategy_configs({"dca": {"amount": [100_000, 200_000]}})
    assert len(configs) == 2
    assert {c["amount"] for c in configs} == {100_000, 200_000}
    assert all(c["key"] == "dca" for c in configs)
    assert all("label" in c for c in configs)


def test_이동평균_전략은_아래_위_후보를_따로_받는다():
    configs = build_strategy_configs(
        {
            "dca_ma": {
                "ma_window": [60],
                "below_amount": [100_000, 200_000],
            }
        }
    )
    # ma_window 1개 * below_amount 2개 = 2 (above는 후보가 없어서 배수 1)
    assert len(configs) == 2
    assert {c["below_amount"] for c in configs} == {100_000, 200_000}
    assert all("above_amount" not in c for c in configs)


def test_익절_손절_후보는_전략마다_따로_받고_다른_변수처럼_조합에_곱해진다():
    configs = build_strategy_configs(
        {"dca": {"amount": [100_000, 200_000], "take_profit_pct": [0.1, 0.2], "stop_loss_pct": [0.05]}}
    )
    # 금액 2개 * 익절 2개 * 손절 1개 = 4
    assert len(configs) == 4
    assert {c["take_profit_pct"] for c in configs} == {0.1, 0.2}
    assert all(c["stop_loss_pct"] == 0.05 for c in configs)


def test_전략마다_다른_익절_후보를_줄_수_있다():
    configs = build_strategy_configs(
        {
            "lump_sum": {"take_profit_pct": [0.3]},
            "dca": {"amount": [100_000], "take_profit_pct": [0.1, 0.2]},
        }
    )
    lump_sum_configs = [c for c in configs if c["key"] == "lump_sum"]
    dca_configs = [c for c in configs if c["key"] == "dca"]
    assert {c["take_profit_pct"] for c in lump_sum_configs} == {0.3}
    assert {c["take_profit_pct"] for c in dca_configs} == {0.1, 0.2}


def test_익절_손절을_안_주면_설정에_안_들어가고_조합도_안_늘어난다():
    configs = build_strategy_configs({"dca": {"amount": [100_000]}})
    assert len(configs) == 1
    assert "take_profit_pct" not in configs[0]
    assert "stop_loss_pct" not in configs[0]


def test_익절_손절을_빈_목록으로_줘도_안_주는_것과_같다():
    configs = build_strategy_configs(
        {"dca": {"amount": [100_000], "take_profit_pct": [], "stop_loss_pct": []}}
    )
    assert len(configs) == 1
    assert "take_profit_pct" not in configs[0]
    assert "stop_loss_pct" not in configs[0]


def test_등락반영_후보는_구간_하나짜리_tiers로_바뀐다():
    configs = build_strategy_configs(
        {
            "drop_based": {
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
    big_list = list(range(1, 202))  # 201개, MAX_COMBINATIONS(200) 초과
    with pytest.raises(ValueError, match="너무 많습니다"):
        build_strategy_configs({"dca": {"amount": big_list}})


def test_조합이_상한_이하면_통과한다():
    values = list(range(1, 201))  # 200개, 상한과 같다
    configs = build_strategy_configs({"dca": {"amount": values}})
    assert len(configs) == 200
    assert len(configs) <= MAX_COMBINATIONS


def test_검색_결과는_수익률_내림차순으로_정렬된다():
    # 폭락(-50%)하는 하루짜리 구간. 회당 매수 금액이 작아서 현금을 다
    # 못 쓰고 남긴 쪽이, 전액을 첫날에 다 써버린 쪽보다 덜 물려서 손실이
    # 작아야 한다.
    prices = _prices([100.0, 50.0])
    configs = build_strategy_configs({"dca": {"amount": [100_000, 300_000]}})
    rows = run_search("TEST", prices, capital=300_000, configs=configs)

    assert len(rows) == 2
    returns = [r["수익률"] for r in rows]
    assert returns == sorted(returns, reverse=True)
    # 100,000원씩 사는 쪽은 첫날 현금을 다 못 써서 둘째 날에도 사고,
    # 300,000원씩 사는 쪽은 첫날 전액을 다 써서 폭락 전 가격에 전부 물린다.
    assert "amount=100000" in rows[0]["strategy_name"]
