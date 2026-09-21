from datetime import date, timedelta

import pandas as pd
import pytest

from auto_trading.optimize import build_strategy_configs, run_search
from auto_trading.walkforward import (
    MIN_RECOMMENDED_FOLDS,
    WalkForwardConfig,
    build_strategy_change_history,
    build_summary_stats,
    count_oos_sign,
    count_strategy_changes,
    fold_count_warning,
    generate_folds,
    run_walk_forward,
    summarize_strategy_selections,
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


# ── 결과 화면용 요약 함수들 ──────────────────────────────
def _fold(idx, 전략키, 설정, 검증_수익률, 학습_수익률=10.0, 검증_시작="2020-01-01", 검증_종료="2021-01-01"):
    """테스트용 폴드 하나. run_walk_forward가 실제로 내는 모양만 흉내 낸다."""
    설명 = f"{전략키}({설정})"
    return {
        "폴드": idx,
        "학습기간": {"시작": "2017-01-01", "종료": 검증_시작},
        "검증기간": {"시작": 검증_시작, "종료": 검증_종료},
        "선정조건": {"전략키": 전략키, "설명": 설명, "설정": 설정},
        "학습기간_성과": {"누적수익률": 학습_수익률, "CAGR": None, "최대낙폭": -5.0, "최대낙폭회복일수": 0, "Calmar": None, "거래횟수": 3},
        "검증기간_성과": {"누적수익률": 검증_수익률, "CAGR": None, "최대낙폭": -5.0, "최대낙폭회복일수": 0, "Calmar": None, "거래횟수": 3},
    }


def test_전략_변경_횟수는_이전_폴드와_설정이_다를_때만_센다():
    folds = [
        _fold(1, "dca", {"amount": 100000, "interval_days": 5}, 1.0),
        _fold(2, "dca", {"amount": 100000, "interval_days": 5}, 2.0),  # 동일
        _fold(3, "dca", {"amount": 200000, "interval_days": 5}, 3.0),  # 변경(금액)
        _fold(4, "lump_sum", {"take_profit_pct": 0.2}, 4.0),  # 변경(전략키)
    ]
    assert count_strategy_changes(folds) == 2


def test_전략_변경_횟수는_폴드가_하나면_0():
    assert count_strategy_changes([_fold(1, "dca", {"amount": 1}, 1.0)]) == 0
    assert count_strategy_changes([]) == 0


def test_양수_음수_OOS_폴드를_센다():
    folds = [
        _fold(1, "dca", {"amount": 1}, 5.0),
        _fold(2, "dca", {"amount": 1}, -3.0),
        _fold(3, "dca", {"amount": 1}, 0.0),  # 0은 양수도 음수도 아니다
        _fold(4, "dca", {"amount": 1}, 1.5),
    ]
    result = count_oos_sign(folds)
    assert result == {"양수": 2, "음수": 1, "전체": 4}


def test_전략_반복_선정_현황은_같은_조건을_하나로_묶는다():
    folds = [
        _fold(1, "dca", {"amount": 100000, "interval_days": 5}, 10.0, 학습_수익률=30.0),
        _fold(2, "dca", {"amount": 100000, "interval_days": 5}, -4.0, 학습_수익률=40.0),
        _fold(3, "lump_sum", {"take_profit_pct": 0.2}, 8.0, 학습_수익률=15.0),
    ]
    rows = summarize_strategy_selections(folds)

    assert len(rows) == 2
    top = rows[0]  # 선정횟수가 많은 순
    assert top["선정횟수"] == 2
    assert top["선정비율"] == pytest.approx(66.7, abs=0.1)
    assert top["평균_검증_수익률"] == pytest.approx((10.0 + -4.0) / 2)
    assert top["OOS_양수_횟수"] == 1
    assert top["OOS_음수_횟수"] == 1
    assert top["평균_학습_수익률"] == pytest.approx((30.0 + 40.0) / 2)

    second = rows[1]
    assert second["선정횟수"] == 1


def test_전략_변경_이력은_최초_동일_변경으로_나눈다():
    folds = [
        _fold(1, "dca", {"amount": 1}, 1.0, 검증_시작="2018-01-01", 검증_종료="2019-01-01"),
        _fold(2, "dca", {"amount": 1}, 2.0, 검증_시작="2019-01-01", 검증_종료="2020-01-01"),
        _fold(3, "lump_sum", {"take_profit_pct": 0.2}, 3.0, 검증_시작="2020-01-01", 검증_종료="2021-01-01"),
    ]
    history = build_strategy_change_history(folds)

    assert [h["상태"] for h in history] == ["최초", "동일", "변경"]
    assert history[0]["검증기간"] == folds[0]["검증기간"]


def test_폴드_수_부족_경고():
    assert fold_count_warning(MIN_RECOMMENDED_FOLDS - 1) is not None
    assert "검증 폴드가" in fold_count_warning(1)
    assert fold_count_warning(MIN_RECOMMENDED_FOLDS) is None
    assert fold_count_warning(MIN_RECOMMENDED_FOLDS + 5) is None


def test_폴드_수_부족_경고는_통계적으로_충분하다고_단정하지_않는다():
    for n in range(MIN_RECOMMENDED_FOLDS):
        message = fold_count_warning(n)
        assert "통계적으로 충분" not in message


def test_build_summary_stats는_전부_한번에_묶는다():
    folds = [
        _fold(1, "dca", {"amount": 1}, 1.0),
        _fold(2, "dca", {"amount": 1}, -1.0),
    ]
    stats = build_summary_stats(folds)
    assert stats["전략변경횟수"] == 0
    assert stats["OOS_폴드수"] == {"양수": 1, "음수": 1, "전체": 2}
    assert len(stats["전략반복선정"]) == 1
    assert len(stats["전략변경이력"]) == 2
    assert stats["폴드수경고"] is not None  # 폴드 2개는 기본 문턱(3) 미만
