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
from dataclasses import dataclass, asdict
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
_AFTER_REFS = re.compile(r"^\s*(appendix|appendices|supplement(?:ary)?|acknowledg(?:e)?ments?|附录|致谢)\b", re.I)
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
            size = max(s.get("size", 0.0) for s in spans)
            # 过滤旋转/竖排文本(如 arXiv 侧边水印):
            # 字符数不少却挤在极窄的横向范围内, 说明是旋转排布
            n = len(text.strip())
            if n > 8 and (x1 - x0) < n * max(size, 1.0) * 0.18:
                continue
            out.append(
                Line(
                    text=text.rstrip(),
                    x0=round(x0, 1),
                    y0=round(y0, 1),
                    x1=round(x1, 1),
                    y1=round(y1, 1),
                    size=round(size, 1),
                    bold=any(
                        "bold" in s.get("font", "").lower() for s in spans
                    ),
                )
            )
    return out


# ---------- 分栏检测 ----------

def _split_columns(lines: list[Line], page_w: float) -> list[list[Line]]:
    """按阅读顺序把页面行切分为若干组: 通栏带 / 双栏带的左栏 / 右栏。

    双栏论文的阅读顺序是"从上到下, 从左到右":
    页面先被通栏元素(标题、作者、跨栏表格)横向切成若干水平带,
    带与带之间按 y 顺序; 每个双栏带内部则左栏整列读完再读右栏。

    因此不能简单地"把所有通栏行提到最前", 也不能对全页做全局 y 排序 ——
    前者会让页中部的跨栏元素跑到页首, 后者会把左右栏交错洗牌。
    """
    if len(lines) < 8:
        return [sorted(lines, key=lambda l: (l.y0, l.x0))]

    mid = page_w / 2

    def is_full(l: Line) -> bool:
        """是否为跨栏(通栏)行。"""
        if not (l.x0 < mid < l.x1):
            return False
        # 足够宽 -> 通栏正文/摘要
        if l.x1 - l.x0 > page_w * 0.45:
            return True
        # 或者水平居中 -> 居中的标题、作者、单位行
        return abs((l.x0 + l.x1) / 2 - mid) <= page_w * 0.04

    # 判断是否真的存在双栏结构(只看较宽的非通栏行)
    wide = [
        l for l in lines
        if l.x1 - l.x0 > page_w * 0.08 and not is_full(l)
    ]
    wl = [l for l in wide if (l.x0 + l.x1) / 2 < mid]
    wr = [l for l in wide if (l.x0 + l.x1) / 2 >= mid]
    if len(wl) < 5 or len(wr) < 5:
        return [sorted(lines, key=lambda l: (l.y0, l.x0))]

    # 按 y 扫描, 用通栏行把页面切成交替的 通栏带 / 双栏带
    key = lambda l: (l.y0, l.x0)
    bands: list[tuple[str, list[Line]]] = []
    cur_full: list[Line] = []
    cur_cols: list[Line] = []
    for l in sorted(lines, key=key):
        if is_full(l):
            if cur_cols:
                bands.append(("cols", cur_cols))
                cur_cols = []
            cur_full.append(l)
        else:
            if cur_full:
                bands.append(("full", cur_full))
                cur_full = []
            cur_cols.append(l)
    if cur_full:
        bands.append(("full", cur_full))
    if cur_cols:
        bands.append(("cols", cur_cols))

    out: list[list[Line]] = []
    for kind, group in bands:
        if kind == "full":
            out.append(sorted(group, key=key))
            continue
        left = [l for l in group if (l.x0 + l.x1) / 2 < mid]
        right = [l for l in group if (l.x0 + l.x1) / 2 >= mid]
        if left:
            out.append(sorted(left, key=key))
        if right:
            out.append(sorted(right, key=key))
    return [g for g in out if g]


# 严格句末标点(用于碎片愈合与正文判定)。
# 只认点号类: 括号/引号结尾的多半是表格单元格、图例或坐标轴标签,
# 如 'Training steps (thousands)'、'LDM+VF loss (MAE) [16]' 并非完整句子。
_HARD_SENT_END = re.compile(r"[.!?。！？]\s*$")


def _heal_fragments(paras: list[Para]) -> list[Para]:
    """合并被版面因素误断的正文碎片。

    规则: 某正文段以小写字母/逗号类字符开头时, 向前(越过 note/caption/list,
    但不越过 heading)找最近的正文段; 若它未以句末标点收尾, 说明二者本属
    同一句, 把当前段并回前文。连字符断词直接拼合。

    典型场景: 双栏页左栏底部的半句, 续文在右栏顶部, 中间隔着脚注/图注。
    """
    out: list[Para] = []
    for p in paras:
        if p.kind == "body":
            ct = p.text.lstrip()
            lower_start = ct[:1].islower() or ct[:1] in ",;:)"
            if lower_start:
                # 向前找最近的正文段。可越过 note/caption/list/heading:
                # 真实章节标题之后必然另起新句(大写开头), 小写开头意味着
                # 中间的"标题"是图内文字等误判, 句子被版面隔断
                j = len(out) - 1
                while j >= 0 and out[j].kind != "body":
                    j -= 1
                if j >= 0 and out[j].kind == "body":
                    prev = out[j]
                    pt = prev.text.rstrip()
                    hyphen = bool(_HYPHEN_END.search(pt))
                    open_end = not _HARD_SENT_END.search(pt)
                    if hyphen or open_end:
                        if hyphen:
                            prev.text = _HYPHEN_END.sub(r"\1", pt) + ct
                        else:
                            prev.text = pt + " " + ct
                        continue  # 当前段已并入前文, 不再单列
        out.append(p)
    for i, p in enumerate(out):
        p.idx = i
    return out


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
    # 标题判定先于列表: '1. Introduction' 这类章节标题以数字开头,
    # 但字号/加粗特征应先按标题归类
    if (size > body_size * 1.12 or (bold and size >= body_size)) and len(text) < 220:
        return "heading"
    if _LIST_START.match(text):
        return "list"
    # 短小的非句子片段多为图表内标签, 归为标注以便前端紧凑排版
    if len(text) < 80 and not _HARD_SENT_END.search(text.strip()):
        return "note"
    return "body"


# ---------- 主流程 ----------

def extract_pages(
    path: str,
    skip_references: bool = True,
) -> tuple[list[list[Para]], list[tuple[float, float]]]:
    """解析 PDF, 返回 (每页段落列表, 每页尺寸)。"""
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("PDF 已加密，请先解密后再打开")
        if not doc.page_count:
            raise ValueError("PDF 没有页面")
        return _extract_document(doc, skip_references)


def _extract_document(doc, skip_references: bool):

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
                if in_refs and not _REF_HEAD.match(text) and (
                    kind == "heading" or _AFTER_REFS.match(text)
                ):
                    in_refs = False
                if in_refs:
                    continue
                paras.append(
                    Para(idx=len(paras), text=text, kind=kind, y=y)
                )

        # 保持当前的纵向对照阅读方式：同一高度的左右栏内容相邻出现。
        # 这适合在 PDF 原版页面旁按视觉位置阅读；之后可考虑做成可选模式。
        paras.sort(key=lambda p: p.y)
        paras = _heal_fragments(paras)
        for i, p in enumerate(paras):
            p.idx = i
        pages.append(paras)

    return pages, sizes


def iter_translatable(paras: Iterable[Para]) -> list[Para]:
    return [p for p in paras if len(p.text) >= 2]
