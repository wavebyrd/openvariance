"""The OpenVariance palette: Excel green and white, shared by Excel exports and dashboards.

Brand green is for chrome (headers, totals, rules). Variance status never
relies on color alone: favorable and unfavorable values also carry an F or U
label, and unfavorable bars are hatched, so reports read correctly for
colorblind readers and in black-and-white print.
"""

GREEN = "#217346"  # brand: header bars, table headers
GREEN_DARK = "#185C37"  # totals, emphasis
GREEN_TINT = "#F1F8F4"  # tiles, banded rows
GREEN_RULE = "#D5E3DA"  # hairlines
WHITE = "#FFFFFF"
INK = "#1F2A24"  # body text
INK_MUTED = "#4A5D52"  # labels
FAVORABLE = "#107C41"
FAVORABLE_BAR = "#5DB585"
FAVORABLE_BG = "#E3F2E9"
UNFAVORABLE = "#B42318"
UNFAVORABLE_BAR = "#D9534F"
UNFAVORABLE_BG = "#FDECEA"
INPUT_BLUE = "#0000FF"  # finance convention: hard-coded inputs in blue, formulas in black


def xl(color: str) -> str:
    """openpyxl wants ARGB without the hash."""
    return "FF" + color.lstrip("#").upper()
