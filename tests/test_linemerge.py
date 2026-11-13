"""段落重建骨架的单元测试。

linemerge 被 extract(通用) 与 paper(论文模式) 共用, 改它会同时影响两条
解析链路, 因此这里直接锁住骨架的行为。
"""

import re
from dataclasses import dataclass

from pdfread import linemerge

SENT_END = re.compile(r"[.!?。！？]['\"”’)\]]?\s*$")
HYPHEN_END = re.compile(r"(\w)[-‐‑]$")
LIST_START = re.compile(r"^\s*([•▪◦‣·–—*]|\(?[a-z]\)|\(?\d{1,2}[.)])\s+", re.I)


@dataclass
class L:
    text: str
    x0: float = 50.0
    y0: float = 0.0
    x1: float = 300.0
    y1: float = 10.0
    size: float = 10.0
    bold: bool = False


def run(lines, **kw):
    return linemerge.merge_lines(
        lines, sent_end=SENT_END, hyphen_end=HYPHEN_END, list_start=LIST_START,
        emit=lambda buf, text: text, **kw)


def rows(*specs):
    """按行依次往下排版, spec = (文本, 右边界, 与上一行的间距)。"""
    out, y = [], 0.0
    for text, x1, gap in specs:
        y += gap
        out.append(L(text=text, y0=y, x1=x1, y1=y + 10.0))
        y += 10.0
    return out


def test_empty_input():
    assert run([]) == []


def test_wrapped_line_joins_into_one_paragraph():
    # 上一行写满列宽且没有句末标点 -> 续行
    assert run(rows(("the model learns a", 300, 0),
                    ("dual grid representation.", 200, 2))) == [
        "the model learns a dual grid representation."]


def test_sentence_end_plus_short_line_starts_new_paragraph():
    """上一行以句号收尾且明显没写满列宽 -> 断段（"段末短行"的典型形态）。

    列宽由该列最宽的行决定，所以先放一条写满的行把右边界撑开；它与下文之间
    隔一个大行距，避免自身参与续接（写满的行无论有没有句号都算续行，
    这是两端对齐排版的正常判断）。
    """
    assert run(rows(("a line wide enough to define the column", 300, 0),
                    ("First paragraph.", 150, 60),
                    ("Second paragraph.", 150, 2))) == [
        "a line wide enough to define the column",
        "First paragraph.", "Second paragraph."]


def test_hyphen_break_is_rejoined_without_space():
    assert run(rows(("recon-", 120, 0),
                    ("struction works.", 200, 2))) == ["reconstruction works."]


def test_list_marker_always_starts_new_item():
    # 即使上一行写满没收尾, 列表符号也必须断开
    assert run(rows(("items are as follows", 300, 0),
                    ("• first item", 300, 2),
                    ("• second item", 300, 2))) == [
        "items are as follows", "• first item", "• second item"]


def test_large_gap_breaks_paragraph():
    assert run(rows(("text continues here", 300, 0),
                    ("far below", 300, 60))) == ["text continues here", "far below"]


def test_font_size_change_breaks_paragraph():
    lines = rows(("Section Title", 300, 0), ("body text here", 300, 2))
    lines[1].size = 14.0
    assert run(lines) == ["Section Title", "body text here"]


def test_split_before_hook_forces_a_break():
    """论文模式用这个钩子把图表内外隔开。"""
    lines = rows(("caption part one", 300, 0), ("caption part two", 300, 2))
    assert run(lines) == ["caption part one caption part two"]
    assert run(lines, split_before=lambda prev, cur, gap: True) == [
        "caption part one", "caption part two"]


def test_clean_hook_replaces_default_whitespace_squeeze():
    lines = [L(text="a    b")]
    assert run(lines) == ["a b"]                       # 默认压缩连续空白
    assert run(lines, clean=lambda s: s.upper()) == ["A    B"]


def test_column_bounds_ignore_outliers():
    # 一条超长行不应把右边界带偏(取 90 分位)
    col = [L(text="x", x0=50, x1=200), L(text="y", x0=50, x1=205),
           L(text="z", x0=50, x1=210), L(text="w", x0=50, x1=900)]
    left, right, span = linemerge.column_bounds(col)
    assert left == 50.0
    assert right <= 900.0 and right >= 200.0
    assert span == right - left


def test_normal_gap_skips_cross_block_jumps():
    # >=40pt 的跳变视为跨区块, 不计入常见行距
    col = rows(("a", 300, 0), ("b", 300, 4), ("c", 300, 4), ("d", 300, 100))
    assert linemerge.normal_gap(col) == 4.0
    assert linemerge.normal_gap([]) == 3.0


def test_join_lines_handles_empty_and_single():
    assert linemerge.join_lines([], HYPHEN_END) == ""
    assert linemerge.join_lines([L(text="  solo  ")], HYPHEN_END) == "solo"
