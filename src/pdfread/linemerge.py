"""段落重建的共享骨架。

`extract.py`(通用)与 `paper.py`(论文模式)都要把"行"合并成"段", 主体逻辑
(列边界、常见行距、续行判据、行尾连字符拼接)完全一致, 原先是两份 85 行的
近似代码并存 —— 改了一边忘另一边的典型温床。

但两边并非可以直接合并:
- Line 类型不同(paper 的多 italic / in_fig 字段)
- 正则不同(paper 的 _SENT_END / _LIST_START 更严格, 是按论文版面调出来的)
- paper 多两条规则(图表内外不混合、图表内短标签互不合并)和 _clean 处理

因此这里只抽取"与类型和正则无关"的骨架, 把差异通过参数注入:
调用方自带正则与收尾钩子, 行为保持各自原样。
"""

from __future__ import annotations

import re
from statistics import median
from typing import Any, Callable, Iterable, Protocol, Sequence

_WS = re.compile(r"\s{2,}")


class LineLike(Protocol):
    """两个模块的 Line 都满足这个形状(结构化类型, 不需要共同基类)。"""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool


def column_bounds(col: Sequence[LineLike]) -> tuple[float, float, float]:
    """列的左右边界与跨度。

    取 10/90 分位而不是极值: 个别超长行(公式、表格线)不会把边界带偏。
    """
    xs = sorted(l.x1 for l in col)
    x0s = sorted(l.x0 for l in col)
    col_right = xs[int(len(xs) * 0.9)] if xs else 0.0
    col_left = x0s[int(len(x0s) * 0.1)] if x0s else 0.0
    return col_left, col_right, max(col_right - col_left, 1.0)


def normal_gap(col: Sequence[LineLike]) -> float:
    """该列的常见行距(中位数)。>=40pt 的跳变是跨区块, 不计入。"""
    gaps = [
        col[i].y0 - col[i - 1].y1
        for i in range(1, len(col))
        if 0 <= col[i].y0 - col[i - 1].y1 < 40
    ]
    return median(gaps) if gaps else 3.0


def join_lines(buf: Sequence[LineLike], hyphen_end: re.Pattern) -> str:
    """把一段里的多行拼成一行文本。

    行尾连字符(hyphen_end)表示断词, 直接接上不留空格; 其余按空格拼。
    """
    parts: list[str] = []
    for i, ln in enumerate(buf):
        t = ln.text.strip()
        if i == 0:
            parts.append(t)
            continue
        prev = parts[-1]
        if hyphen_end.search(prev):
            parts[-1] = hyphen_end.sub(r"\1", prev) + t
        else:
            parts[-1] = prev + " " + t
    return parts[0] if parts else ""


def continues(
    prev: LineLike,
    cur: LineLike,
    *,
    col_left: float,
    span: float,
    norm_gap: float,
    sent_end: re.Pattern,
    hyphen_end: re.Pattern,
    list_start: re.Pattern,
) -> bool:
    """当前行是否是上一行的续行(即同一段)。

    五个条件缺一不可, 都是按真实 PDF 调出来的:
    - same_size: 字号跳变意味着换了角色(标题/脚注)
    - near:      行距不能超过常见行距太多; 允许轻微负值(同一行的微小抖动)
    - indent_ok: 左边界没有明显外凸(新段常有首行缩进或回到列左)
    - new_item:  列表项符号开头 -> 必定是新段
    - 末尾判据:  行尾连字符断词、或上一行写满列宽、或上一行没有句末标点,
                 三者有其一才算续行
    """
    prev_txt = prev.text.strip()
    gap = cur.y0 - prev.y1

    same_size = abs(cur.size - prev.size) <= 0.6
    near = -prev.size * 0.5 <= gap <= max(norm_gap * 2.2, prev.size * 1.1)
    indent_ok = abs(cur.x0 - col_left) <= prev.size * 1.2 or cur.x0 >= prev.x0
    new_item = bool(list_start.match(cur.text))

    prev_full = prev.x1 >= col_left + span * 0.82
    prev_ends = bool(sent_end.search(prev_txt))
    hyphen = bool(hyphen_end.search(prev_txt))

    return (
        same_size
        and near
        and indent_ok
        and not new_item
        and (hyphen or prev_full or not prev_ends)
    )


def merge_lines(
    col: Sequence[LineLike],
    *,
    sent_end: re.Pattern,
    hyphen_end: re.Pattern,
    list_start: re.Pattern,
    emit: Callable[[list[LineLike], str], Any],
    split_before: Callable[[LineLike, LineLike, float], bool] | None = None,
    clean: Callable[[str], str] | None = None,
) -> list[Any]:
    """把同一列的行合并成段, 返回 emit() 产出的列表。

    emit(buf, text)         —— 由调用方决定产出什么形状的元组
    split_before(prev, cur, gap) —— 额外的强制断段规则(论文模式用它处理图表)
    clean(text)             —— 可选的文本清理
    """
    if not col:
        return []

    col_left, _col_right, span = column_bounds(col)
    gap_norm = normal_gap(col)

    out: list[Any] = []
    buf: list[LineLike] = []

    def flush() -> None:
        if not buf:
            return
        text = join_lines(buf, hyphen_end)
        text = clean(text) if clean else _WS.sub(" ", text).strip()
        out.append(emit(list(buf), text))
        buf.clear()

    for ln in col:
        if not buf:
            buf.append(ln)
            continue
        prev = buf[-1]
        if split_before and split_before(prev, ln, ln.y0 - prev.y1):
            flush()
            buf.append(ln)
            continue
        if continues(prev, ln, col_left=col_left, span=span, norm_gap=gap_norm,
                     sent_end=sent_end, hyphen_end=hyphen_end,
                     list_start=list_start):
            buf.append(ln)
        else:
            flush()
            buf.append(ln)

    flush()
    return out
