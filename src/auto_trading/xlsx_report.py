"""백테스트 결과를 서식 있는 엑셀로 만든다.

화면에 남는 JSON과 별개로, 구글 드라이브에 보관해 두고 나중에 다시
열어 보기 좋은 형태로 남기는 것이 목적이다. 헤더에 배경색을 주고,
금액에는 천단위 구분을, 수익률에는 퍼센트 서식을 입힌다.
"""

from __future__ import annotations

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

HEADER_FILL = PatternFill("solid", fgColor="2F6F65")
HEADER_FONT = Font(color="FFFFFF", bold=True)
TITLE_FONT = Font(bold=True, size=14)
POSITIVE_FONT = Font(color="1B7A43")
NEGATIVE_FONT = Font(color="B5502E")
MONEY_FORMAT = "#,##0"
SHARE_FORMAT = "#,##0.0000"
PERCENT_FORMAT = "0.00%"


def _set_column_widths(ws: Worksheet, widths: list[int]) -> None:
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width


def _write_header_row(ws: Worksheet, row: int, headers: list[str]) -> None:
    for col_idx, text in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col_idx, value=text)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")


def _write_summary_block(ws: Worksheet, title: str, info_lines: list[str]) -> int:
    """제목과 안내 줄을 적고, 다음으로 쓸 수 있는 줄 번호를 돌려준다."""
    ws["A1"] = title
    ws["A1"].font = TITLE_FONT
    row = 2
    for line in info_lines:
        ws.cell(row=row, column=1, value=line)
        row += 1
    return row + 1


def build_symbol_report(
    symbol: str,
    strategy_name: str,
    capital: float,
    start: str,
    end: str,
    generated_at_kst: str,
    summary_row: dict,
    daily: pd.DataFrame,
) -> Workbook:
    """종목 하나, 전략 하나의 분석 결과를 담은 엑셀을 만든다."""
    wb = Workbook()
    ws = wb.active
    ws.title = "요약"

    table_row = _write_summary_block(
        ws,
        f"{symbol} - {strategy_name} 분석 결과",
        [f"조회기간: {start} ~ {end}", f"총자본: {capital:,.0f}원", f"생성시각(KST): {generated_at_kst}"],
    )
    headers = ["종목", "전략", "총투자금", "실현손익", "평가손익", "합계", "수익률"]
    _write_header_row(ws, table_row, headers)
    r = table_row + 1
    ws.cell(row=r, column=1, value=summary_row["symbol"])
    ws.cell(row=r, column=2, value=summary_row["strategy_name"])
    ws.cell(row=r, column=3, value=summary_row["총투자금"]).number_format = MONEY_FORMAT
    ws.cell(row=r, column=4, value=summary_row["실현손익"]).number_format = MONEY_FORMAT
    ws.cell(row=r, column=5, value=summary_row["평가손익"]).number_format = MONEY_FORMAT
    ws.cell(row=r, column=6, value=summary_row["합계"]).number_format = MONEY_FORMAT
    ws.cell(row=r, column=7, value=summary_row["수익률"] / 100).number_format = PERCENT_FORMAT
    _set_column_widths(ws, [12, 24, 14, 14, 14, 14, 10])

    ws2 = wb.create_sheet("일별 시계열")
    daily_headers = [
        "거래일", "종가", "현금", "보유수량", "평균단가",
        "누적투자금", "평가금액", "실현손익", "평가손익", "손익합계", "총자산",
    ]
    _write_header_row(ws2, 1, daily_headers)
    for r_idx, row_data in enumerate(daily.itertuples(index=False), start=2):
        ws2.cell(row=r_idx, column=1, value=str(row_data.trade_date))
        ws2.cell(row=r_idx, column=2, value=round(float(row_data.close))).number_format = MONEY_FORMAT
        ws2.cell(row=r_idx, column=3, value=round(float(row_data.cash))).number_format = MONEY_FORMAT
        ws2.cell(row=r_idx, column=4, value=float(row_data.shares)).number_format = SHARE_FORMAT
        ws2.cell(row=r_idx, column=5, value=round(float(row_data.avg_cost))).number_format = MONEY_FORMAT
        ws2.cell(row=r_idx, column=6, value=round(float(row_data.invested_cumulative))).number_format = MONEY_FORMAT
        ws2.cell(row=r_idx, column=7, value=round(float(row_data.market_value))).number_format = MONEY_FORMAT
        ws2.cell(row=r_idx, column=8, value=round(float(row_data.realized_pnl))).number_format = MONEY_FORMAT
        ws2.cell(row=r_idx, column=9, value=round(float(row_data.unrealized_pnl))).number_format = MONEY_FORMAT
        ws2.cell(row=r_idx, column=10, value=round(float(row_data.total_pnl))).number_format = MONEY_FORMAT
        ws2.cell(row=r_idx, column=11, value=round(float(row_data.total_value))).number_format = MONEY_FORMAT
    ws2.freeze_panes = "A2"
    _set_column_widths(ws2, [12, 12, 13, 12, 12, 14, 14, 13, 13, 13, 14])

    return wb


def build_comparison_report(
    symbols: list[str],
    capital: float,
    start: str,
    end: str,
    generated_at_kst: str,
    summary_rows: list[dict],
) -> Workbook:
    """여러 종목·여러 전략을 한 번에 비교한 결과를 담은 엑셀을 만든다."""
    wb = Workbook()
    ws = wb.active
    ws.title = "종목 비교"

    table_row = _write_summary_block(
        ws,
        f"종목 비교 결과 ({', '.join(symbols)})",
        [f"조회기간: {start} ~ {end}", f"총자본(종목·전략마다): {capital:,.0f}원", f"생성시각(KST): {generated_at_kst}"],
    )
    headers = ["종목", "전략", "총투자금", "실현손익", "평가손익", "합계", "수익률"]
    _write_header_row(ws, table_row, headers)
    for i, row in enumerate(summary_rows, start=table_row + 1):
        ws.cell(row=i, column=1, value=row["symbol"])
        ws.cell(row=i, column=2, value=row["strategy_name"])
        ws.cell(row=i, column=3, value=row["총투자금"]).number_format = MONEY_FORMAT
        ws.cell(row=i, column=4, value=row["실현손익"]).number_format = MONEY_FORMAT
        ws.cell(row=i, column=5, value=row["평가손익"]).number_format = MONEY_FORMAT
        ws.cell(row=i, column=6, value=row["합계"]).number_format = MONEY_FORMAT
        pct_cell = ws.cell(row=i, column=7, value=row["수익률"] / 100)
        pct_cell.number_format = PERCENT_FORMAT
        pct_cell.font = POSITIVE_FONT if row["수익률"] >= 0 else NEGATIVE_FONT
    ws.freeze_panes = f"A{table_row + 1}"
    _set_column_widths(ws, [12, 26, 14, 14, 14, 14, 10])

    return wb
