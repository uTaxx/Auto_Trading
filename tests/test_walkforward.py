from datetime import date, timedelta

import pandas as pd
import pytest

from auto_trading.optimize import build_strategy_configs, run_search
from auto_trading.walkforward import (
    WalkForwardConfig,
    generate_folds,
    run_walk_forward,
)


def _prices(years: float, start: date = date(2015, 1, 1)) -> pd.DataFrame:
    """실제 거래일이 아니라 매일 값이 있는 합성 시세. 완만하게 오르내리며
    자잘한 파동을 준다(전략마다 성적이 갈리게 하려고)."""
    days = round(years * 365.25)
    dates = [start + timedelta(days=i) for i in range(days)]
    closes = [100.0 * (1 + 0.0004 * i) * (1 + 0.05 * ((i % 60) - 30) / 30) for i in range(days)]
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1_000_000] * days,
        }
    )


def test_기본_설정으로_3년_1년_1년_폴드가_만들어진다():
    config = WalkForwardConfig()  # 3/1/1 기본값
    min_date = date(2015, 1, 1)
    max_date = date(2020, 1, 1)  # 정확히 5년치
    folds = generate_folds(min_date, max_date, config)

    # 1번째 폴드는 3+1=4년치(2019-01-01까지)만 있으면 되고, 1년 이동한
    # 2번째 폴드는 5년치(2020-01-01까지)가 있어야 들어간다. 정확히
    # 5년을 줬으니 2개까지 들어가고 3번째(6년 필요)는 못 들어간다.
    assert len(folds) == 2
    assert folds[0].index == 1
    assert folds[0].train_start == min_date
    assert folds[1].train_start == min_date + timedelta(days=round(365.25))


def test_데이터가_모자라면_폴드가_하나도_안_생긴다():
    config = WalkForwardConfig()
    folds = generate_folds(date(2015, 1, 1), date(2016, 1, 1), config)
    assert folds == []


def test_설정값은_하드코딩이_아니라_파라미터다():
    # 학습 2년·검증 6개월·이동 6개월로 바꿔도 그대로 동작해야 한다.
    config = WalkForwardConfig(in_sample_years=2.0, out_sample_years=0.5, step_years=0.5)
    folds = generate_folds(date(2015, 1, 1), date(2018, 1, 1), config)
    assert len(folds) >= 2
    for f in folds:
        assert (f.train_end - f.train_start).days == pytest.approx(round(2 * 365.25), abs=1)
        assert (f.test_end - f.test_start).days == pytest.approx(round(0.5 * 365.25), abs=1)


def test_잘못된_설정값은_바로_오류():
    with pytest.raises(ValueError):
        WalkForwardConfig(in_sample_years=0)


def test_run_walk_forward_기본_구조():
    prices = _prices(years=6.0)
    search = {
        "lump_sum": {"take_profit_pct": [0.2]},
        "dca": {"amount": [50_000, 100_000], "interval_days": [5]},
    }
    config = WalkForwardConfig()  # 3/1/1
    result = run_walk_forward("TEST", prices, capital=1_000_000, search=search, config=config)

    assert len(result["폴드별_결과"]) >= 2
    first = result["폴드별_결과"][0]
    assert first["폴드"] == 1
    assert first["학습기간"]["시작"] == "2015-01-01"
    assert set(first["학습기간_성과"].keys()) == {
        "누적수익률", "CAGR", "최대낙폭", "최대낙폭회복일수", "Calmar", "거래횟수",
    }
    assert set(first["검증기간_성과"].keys()) == set(first["학습기간_성과"].keys())
    assert "전략키" in first["선정조건"]

    assert set(result["전체_OOS_성과"].keys()) == set(first["학습기간_성과"].keys())
    assert len(result["전체_OOS_시계열"]) > 0


def test_검증기간_조건은_그_폴드의_학습기간에서만_고른_것과_같다():
    """학습기간에서 직접 최적화를 돌린 1위와, walk_forward가 그 폴드에서
    고른 조건이 같아야 한다(검증기간 데이터를 몰래 보고 고르지 않았다는
    확인)."""
    prices = _prices(years=4.5)
    search = {"dca": {"amount": [50_000, 100_000, 200_000], "interval_days": [3, 10]}}
    config = WalkForwardConfig()
    result = run_walk_forward("TEST", prices, capital=1_000_000, search=search, config=config)
    fold = result["폴드별_결과"][0]

    train_start = date.fromisoformat(fold["학습기간"]["시작"])
    train_end = date.fromisoformat(fold["학습기간"]["종료"])
    train_slice = prices[(prices["trade_date"] >= train_start) & (prices["trade_date"] < train_end)]

    configs = build_strategy_configs(search)
    rows = run_search("TEST", train_slice, 1_000_000, configs)
    expected_best = rows[0]

    assert fold["선정조건"]["설명"] == expected_best["strategy_name"]
    assert fold["학습기간_성과"]["누적수익률"] == pytest.approx(expected_best["수익률"], abs=0.5)


def test_시세가_모자라면_이유를_설명하는_오류():
    prices = _prices(years=2.0)
    search = {"lump_sum": {}}
    config = WalkForwardConfig()  # 3/1/1인데 시세가 2년뿐
    with pytest.raises(ValueError, match="시세 기간이 부족"):
        run_walk_forward("TEST", prices, capital=1_000_000, search=search, config=config)
