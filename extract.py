"""PDF 文本提取与段落重建。

核心难点: 很多 PDF(尤其 PDFreactor/LaTeX 生成的)把每一行文字都作为独立 block,
直接按 block 翻译会把句子拦腰截断, 既产生大量碎片换行, 又严重损害翻译质量。

本模块从 *行* 级别重新聚类出真正的自然段:
  1. 按 x 坐标检测分栏, 每列独立处理
  2. 用"前一行是否为满行"作为主判据还原被自动折行的段落
  3. 辅以字号、行距、缩进、列表标记等判据切分段落

不做版面还原, 不做原位回写 —— 这是本项目比 pdf2zh 快一个数量级的原因。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from statistics import median
from typing import Iterable

import pymupdf


# ---------- 数据结构 ----------

@dataclass
class Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool


@dataclass
class Para:
    """一个待翻译段落。"""

    idx: int          # 页内序号
    text: str         # 原文
    kind: str         # body | heading | caption | list
    y: float          # 页内纵坐标, 用于排序与滚动定位

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- 正则 ----------

_REF_HEAD = re.compile(
    r"^\s*(references?|bibliography|works\s+cited|参考文献)\s*$", re.I
)
_PAGE_NOISE = re.compile(r"^\s*[\dixvIXV\-–—.,|]{1,12}\s*$")
_URL_ONLY = re.compile(r"^\s*(https?://|www\.)\S+\s*$", re.I)
_CAPTION = re.compile(r"^\s*(figure|fig\.?|table|chart|exhibit)\s*\d", re.I)
# 图表附注 / 数据来源等次要标注
_NOTE = re.compile(
    r"^\s*(note\(?s?\)?|source\(?s?\)?|data\s+source|注|资料来源)\s*[:：]?\s*$", re.I
)
# 列表项 / 编号开头 -> 必定是新段落
_LIST_START = re.compile(
    r"^\s*([•▪◦‣·–—*]|\(?[a-z]\)|\(?\d{1,2}[.)]|\d+\.\d+)\s+", re.I
)
# 行尾连字符断词
_HYPHEN_END = re.compile(r"(\w)[-‐‑]$")
# 句末标点
_SENT_END = re.compile(r"[.!?;:。！？；：\"')\]]\s*$")


def _is_noise(text: str) -> bool:
    t = text.strip()
    if len(t) < 2:
        return True
    if _PAGE_NOISE.match(t) or _URL_ONLY.match(t):
        return True
    letters = sum(c.isalpha() for c in t)
    if letters < max(3, len(t) * 0.25):
        return True
    return False


# ---------- 行提取 ----------

def _page_lines(page: pymupdf.Page) -> list[Line]:
    """取出页面内所有文本行(不是 block)。"""
    out: list[Line] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for ln in block.get("lines", []):
            spans = ln.get("spans", [])
            if not spans:
                continue
            text = "".join(s["text"] for s in spans)
            if not text.strip():
                continue
            x0, y0, x1, y1 = ln["bbox"]
            out.append(
                Line(
                    text=text.rstrip(),
                    x0=round(x0, 1),
                    y0=round(y0, 1),
                    x1=round(x1, 1),
                    y1=round(y1, 1),
                    size=round(max(s.get("size", 0.0) for s in spans), 1),
                    bold=any(
                        "bold" in s.get("font", "").lower() for s in spans
                    ),
                )
            )
    return out


# ---------- 分栏检测 ----------

def _split_columns(lines: list[Line], page_w: float) -> list[list[Line]]:
    """按 x0 聚类检测分栏。返回按阅读顺序排列的每列行集合。"""
    if len(lines) < 8:
        return [lines]

    # 只用正文行(排除跨栏的大标题)参与判断
    body = [l for l in lines if l.x1 - l.x0 > page_w * 0.08]
    if not body:
        return [lines]

    mid = page_w / 2
    left = [l for l in body if l.x1 <= mid + page_w * 0.04]
    right = [l for l in body if l.x0 >= mid - page_w * 0.04]

    # 双栏成立条件: 两侧都有足够多的行, 且几乎没有跨栏正文行
    cross = [l for l in body if l.x0 < mid < l.x1 and l.x1 - l.x0 > page_w * 0.55]
    if len(left) >= 5 and len(right) >= 5 and len(cross) <= len(body) * 0.15:
        others = [l for l in lines if l not in left and l not in right]
        cols = []
        if others:
            cols.append(sorted(others, key=lambda l: (l.y0, l.x0)))
        cols.append(sorted(left, key=lambda l: (l.y0, l.x0)))
        cols.append(sorted(right, key=lambda l: (l.y0, l.x0)))
        return [c for c in cols if c]

    return [sorted(lines, key=lambda l: (l.y0, l.x0))]


# ---------- 段落重建 ----------

def _merge_lines(col: list[Line], body_size: float) -> list[tuple[str, float, float, bool]]:
    """把同一列的行合并成自然段。

    返回 [(文本, y坐标, 字号, 是否粗体), ...]
    """
    if not col:
        return []

    # 该列的右边界(取 90 分位, 避免个别超长行干扰)
    xs = sorted(l.x1 for l in col)
    col_right = xs[int(len(xs) * 0.9)] if xs else 0.0
    # 该列的左边界
    x0s = sorted(l.x0 for l in col)
    col_left = x0s[int(len(x0s) * 0.1)] if x0s else 0.0
    # 常见行距
    gaps = [
        col[i].y0 - col[i - 1].y1
        for i in range(1, len(col))
        if 0 <= col[i].y0 - col[i - 1].y1 < 40
    ]
    norm_gap = median(gaps) if gaps else 3.0

    paras: list[tuple[str, float, float, bool]] = []
    buf: list[Line] = []

    def flush() -> None:
        if not buf:
            return
        parts: list[str] = []
        for i, ln in enumerate(buf):
            t = ln.text.strip()
            if i == 0:
                parts.append(t)
                continue
            prev = parts[-1]
            # 行尾连字符断词 -> 直接拼接
            if _HYPHEN_END.search(prev):
                parts[-1] = _HYPHEN_END.sub(r"\1", prev) + t
            else:
                parts[-1] = prev + " " + t
        text = re.sub(r"\s{2,}", " ", parts[0]).strip()
        first = buf[0]
        paras.append(
            (text, first.y0, max(l.size for l in buf), any(l.bold for l in buf))
        )
        buf.clear()

    for ln in col:
        if not buf:
            buf.append(ln)
            continue

        prev = buf[-1]
        prev_txt = prev.text.strip()
        gap = ln.y0 - prev.y1

        # 前一行是否"写满"该列宽度(两端对齐时右边界会有波动, 用相对比例更稳)
        span = max(col_right - col_left, 1.0)
        prev_full = prev.x1 >= col_left + span * 0.82
        # 前一行是否以句末标点收尾 —— 未收尾几乎必定是折行续接
        prev_ends = bool(_SENT_END.search(prev_txt))
        # 行尾连字符断词, 必然续行
        hyphen = bool(_HYPHEN_END.search(prev_txt))

        same_size = abs(ln.size - prev.size) <= 0.6
        near = -prev.size * 0.5 <= gap <= max(norm_gap * 2.2, prev.size * 1.1)
        indent_ok = abs(ln.x0 - col_left) <= prev.size * 1.2 or ln.x0 >= prev.x0
        new_item = bool(_LIST_START.match(ln.text))

        cont = (
            same_size
            and near
            and indent_ok
            and not new_item
            and (hyphen or prev_full or not prev_ends)
        )

        if cont:
            buf.append(ln)
        else:
            flush()
            buf.append(ln)

    flush()
    return paras


def _classify(text: str, size: float, bold: bool, body_size: float) -> str:
    if _NOTE.match(text):
        return "note"
    if _CAPTION.match(text):
        return "caption"
    if _LIST_START.match(text):
        return "list"
    if (size > body_size * 1.12 or (bold and size >= body_size)) and len(text) < 220:
        return "heading"
    # 短小的非句子片段多为图表内标签, 归为标注以便前端紧凑排版
    if len(text) < 45 and not _SENT_END.search(text.strip()):
        return "note"
    return "body"


# ---------- 主流程 ----------

def extract_pages(
    path: str,
    skip_references: bool = True,
) -> tuple[list[list[Para]], list[tuple[float, float]]]:
    """解析 PDF, 返回 (每页段落列表, 每页尺寸)。"""
    doc = pymupdf.open(path)

    # 统计正文基准字号(按字符数加权)
    size_chars: dict[float, int] = {}
    all_lines: list[list[Line]] = []
    for page in doc:
        lines = _page_lines(page)
        all_lines.append(lines)
        for l in lines:
            size_chars[l.size] = size_chars.get(l.size, 0) + len(l.text)
    body_size = max(size_chars, key=size_chars.get) if size_chars else 10.0

    pages: list[list[Para]] = []
    sizes: list[tuple[float, float]] = []
    in_refs = False
    total_pages = doc.page_count

    for pno, page in enumerate(doc):
        rect = page.rect
        sizes.append((rect.width, rect.height))
        header_lim = rect.height * 0.055
        footer_lim = rect.height * 0.945

        lines = [
            l
            for l in all_lines[pno]
            if not (l.y1 < header_lim or l.y0 > footer_lim)
            or (len(l.text.strip()) > 80)
        ]

        paras: list[Para] = []
        for col in _split_columns(lines, rect.width):
            for text, y, size, bold in _merge_lines(col, body_size):
                if _is_noise(text):
                    continue
                kind = _classify(text, size, bold, body_size)
                # 参考文献区: 必须是后半部分的独立标题, 避免误伤目录里的条目
                if (
                    skip_references
                    and not in_refs
                    and _REF_HEAD.match(text)
                    and kind == "heading"
                    and pno >= total_pages * 0.5
                ):
                    in_refs = True
                if in_refs:
                    continue
                paras.append(
                    Para(idx=len(paras), text=text, kind=kind, y=y)
                )

        # 按纵坐标恢复阅读顺序并重编号
        paras.sort(key=lambda p: p.y)
        for i, p in enumerate(paras):
            p.idx = i
        pages.append(paras)

    doc.close()
    return pages, sizes


def iter_translatable(paras: Iterable[Para]) -> list[Para]:
    return [p for p in paras if len(p.text) >= 2]
