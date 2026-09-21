"""기존 시세와 새로 받은 시세를 합치는 순수 함수. 구글 드라이브나 야후를
몰라야 테스트하기 쉽다."""

from __future__ import annotations

import pandas as pd


def merge_price_data(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """같은 날짜가 겹치면 새로 받은 쪽을 남긴다. 날짜 순으로 정렬해서 돌려준다."""
    if existing.empty:
        return new.sort_values("trade_date").reset_index(drop=True)
    if new.empty:
        return existing.sort_values("trade_date").reset_index(drop=True)
    combined = pd.concat([existing, new], ignore_index=True)
    return (
        combined.drop_duplicates(subset=["trade_date"], keep="last")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
