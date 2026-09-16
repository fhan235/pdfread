"""学术论文版面分析。

面向计算机领域会议/期刊论文(双栏、图表密集、公式多)的专门处理。
与通用 extract.py 的关键差异:

1. 图表区域感知: 由 get_drawings()/get_images() 聚类出图表框,
   落在框内的文字标为 figtext(坐标轴、图例、数据标签), 默认不翻译。
2. 跨图表段落续接: 正文被插图物理隔断后, 跳过图表文字与图注,
   把残尾与续文重新缝合成完整段落。
3. 公式占位: 行内公式替换为 ⟦F1⟧ 占位符送翻译, 译后还原,
   既保证句子连贯又避免模型改写公式。
4. 结构识别: 章节编号标题、参考文献/致谢/附录区、表格数据行。
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
    italic: bool = False
    # 落在图表区域内
    in_fig: bool = False


@dataclass
class Block:
    """一个待呈现单元。"""

    idx: int
    text: str
    kind: str          # body | heading | caption | list | figtext | table | formula
    y: float
    # 公式占位符还原表: {"F1": "原始公式文本"}
    formulas: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- 正则 ----------

# 章节标题: '1. Introduction' / '5.2. Foundation Models' / 'A.1 Details'
_SECTION = re.compile(
    r"^\s*(?:\d+|[A-Z])(?:\.\d+)*\.?\s+[A-Z][A-Za-z].{0,80}$"
)
# 无编号的常见章节名
_SECTION_WORD = re.compile(
    r"^\s*(abstract|introduction|related\s+work|background|method(?:s|ology)?|"
    r"experiments?|results?|discussion|conclusions?|limitations?|"
    r"acknowledgual|acknowledge?ments?|references?|bibliography|appendix|"
    r"supplementary(?:\s+material)?)\s*$",
    re.I,
)
# 跳过区起始
_REF_HEAD = re.compile(
    r"^\s*(references?|bibliography|acknowledge?ments?|"
    r"appendix|supplementary(?:\s+material)?)\b", re.I
)
# 图/表标题
_CAPTION = re.compile(
    r"^\s*(figure|fig\.?|table|tab\.?|algorithm|alg\.?|chart|exhibit)\s*"
    r"(\d+|[IVX]+)\b", re.I
)
_LIST_START = re.compile(r"^\s*([•▪◦‣·–—*]|\(?[a-z]\)|\(?\d{1,2}[.)])\s+", re.I)
_HYPHEN_END = re.compile(r"(\w)[-‐‑]$")
# 句子终止(只认点号类, 括号/引号结尾多为表格单元格)
_SENT_END = re.compile(r"[.!?。！？]['\"”’)\]]?\s*$")
# 纯数据行: 主要由数字/符号构成
_DATA_ROW = re.compile(r"^[\s\d.,%+\-±×/()\[\]|:∼~<>=]*$")
# 数学字体特征
_MATH_FONT = re.compile(
    r"(cmmi|cmsy|cmex|cmr|msam|msbm|mathit|mathsy|mathex|"
    r"stix|xits|euclid|symbol)", re.I
)
# URL / 邮箱独占一行
_URL_ONLY = re.compile(r"^\s*(https?://|www\.|[\w.+-]+@[\w.-]+)\S*\s*$", re.I)
# 页码等
_PAGE_NOISE = re.compile(r"^\s*[\dixvIXV\-–—.,|]{1,12}\s*$")
# arXiv 页脚/水印
_ARXIV_MARK = re.compile(r"arxiv:\d{4}\.\d{4,5}", re.I)


# ---------- 图表区域 ----------

def _merge_boxes(raw: list[list[float]], min_ratio: float = 0.5) -> list[list[float]]:
    """合并有实质重叠的矩形。

    用空间网格做候选筛选, 避免朴素两两比较的 O(n^2):
    矢量图密集的论文单页可有 200+ 个绘制元素, 朴素做法会明显拖慢解析。
    """
    if len(raw) <= 1:
        return [list(b) for b in raw]

    def ratio(a: list[float], b: list[float]) -> float:
        w = min(a[2], b[2]) - max(a[0], b[0])
        h = min(a[3], b[3]) - max(a[1], b[1])
        if w <= 0 or h <= 0:
            return 0.0
        inter = w * h
        sa = (a[2] - a[0]) * (a[3] - a[1])
        sb = (b[2] - b[0]) * (b[3] - b[1])
        return inter / max(min(sa, sb), 1.0)

    # 网格边长取框宽高中位数, 保证每格内候选数量可控
    cell = max(
        median([b[2] - b[0] for b in raw] + [b[3] - b[1] for b in raw]), 20.0
    )

    boxes = [list(b) for b in raw]
    alive = [True] * len(boxes)

    changed = True
    while changed:
        changed = False
        grid: dict[tuple[int, int], list[int]] = {}
        for i, b in enumerate(boxes):
            if not alive[i]:
                continue
            for gx in range(int(b[0] // cell), int(b[2] // cell) + 1):
                for gy in range(int(b[1] // cell), int(b[3] // cell) + 1):
                    grid.setdefault((gx, gy), []).append(i)

        for i, b in enumerate(boxes):
            if not alive[i]:
                continue
            cand: set[int] = set()
            for gx in range(int(b[0] // cell), int(b[2] // cell) + 1):
                for gy in range(int(b[1] // cell), int(b[3] // cell) + 1):
                    cand.update(grid.get((gx, gy), ()))
            for j in cand:
                if j <= i or not alive[j] or not alive[i]:
                    continue
                if ratio(boxes[i], boxes[j]) >= min_ratio:
                    boxes[i] = [
                        min(boxes[i][0], boxes[j][0]),
                        min(boxes[i][1], boxes[j][1]),
                        max(boxes[i][2], boxes[j][2]),
                        max(boxes[i][3], boxes[j][3]),
                    ]
                    alive[j] = False
                    changed = True

    return [b for b, ok in zip(boxes, alive) if ok]


def _fig_boxes(
    page: pymupdf.Page,
    text_dict: dict | None = None,
) -> list[tuple[float, float, float, float]]:
    """聚类出页面上的图表区域框。

    来源: 矢量绘制(折线图/坐标轴)与位图。仅合并 *真正重叠* 的框,
    不做邻近扩张 —— 否则相邻子图会连成横跨整页的巨框, 把图注和正文
    一并吞掉。

    位图位置直接取 get_text("dict") 中 type==1 的图片块 bbox:
    page.get_image_rects() 需要重新扫描页面内容流, 在图片较多的论文上
    实测单页可耗时数秒(整篇近 10s), 而图片块 bbox 是解析文本时顺带得到的。
    """
    raw: list[list[float]] = []

    for dr in page.get_drawings():
        r = dr.get("rect")
        if r is None:
            continue
        # 忽略极细的线(表格横线、下划线)与极小的装饰
        if r.width < 16 or r.height < 16:
            continue
        raw.append([r.x0, r.y0, r.x1, r.y1])

    if text_dict is None:
        text_dict = page.get_text("dict")
    for blk in text_dict.get("blocks", []):
        if blk.get("type") != 1:
            continue
        x0, y0, x1, y1 = blk["bbox"]
        if x1 - x0 >= 16 and y1 - y0 >= 16:
            raw.append([x0, y0, x1, y1])

    if not raw:
        return []

    raw = _merge_boxes(raw)

    page_area = page.rect.width * page.rect.height
    out: list[tuple[float, float, float, float]] = []
    for b in raw:
        x0, y0, x1, y1 = b
        # 越出页面边界的框多为背景/裁剪矩形, 会误吞正文
        if (
            x0 < page.rect.x0 - 2 or y0 < page.rect.y0 - 2
            or x1 > page.rect.x1 + 2 or y1 > page.rect.y1 + 2
        ):
            continue
        # 过滤异常大的区域, 避免吞掉正文
        if (x1 - x0) * (y1 - y0) >= page_area * 0.42:
            continue
        out.append((x0, y0, x1, y1))
    return out


def _in_boxes(l: Line, boxes: list[tuple[float, float, float, float]]) -> bool:
    """行的中心是否落在某个图表框内。"""
    cx = (l.x0 + l.x1) / 2
    cy = (l.y0 + l.y1) / 2
    for x0, y0, x1, y1 in boxes:
        if x0 - 2 <= cx <= x1 + 2 and y0 - 2 <= cy <= y1 + 2:
            return True
    return False


# ---------- 行提取 ----------

def _page_lines(page: pymupdf.Page) -> list[Line]:
    # get_text 只调用一次, 图片块 bbox 与文字行共用同一份结果
    td = page.get_text("dict")
    boxes = _fig_boxes(page, td)
    out: list[Line] = []
    for block in td["blocks"]:
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
            fonts = " ".join(s.get("font", "") for s in spans).lower()

            n = len(text.strip())
            # 过滤旋转/竖排文本(arXiv 侧边水印)
            if n > 8 and (x1 - x0) < n * max(size, 1.0) * 0.18:
                continue

            l = Line(
                text=text.rstrip(),
                x0=round(x0, 1),
                y0=round(y0, 1),
                x1=round(x1, 1),
                y1=round(y1, 1),
                size=round(size, 1),
                bold="bold" in fonts,
                italic="italic" in fonts or "oblique" in fonts,
            )
            # 图内文字必须"看起来像标签": 短。
            # 完整句子(较长且含句末标点)即便压在图形上, 也是正文或图注,
            # 这是防止图形框误判吞掉正文的关键保险。
            stripped = text.strip()
            looks_label = len(stripped) < 60 and not _SENT_END.search(stripped)
            l.in_fig = looks_label and _in_boxes(l, boxes)
            out.append(l)
    return out


# ---------- 版面分带(继承通用逻辑, 论文场景加强) ----------

def _split_columns(lines: list[Line], page_w: float) -> list[list[Line]]:
    """按水平带切分: 通栏带 / 双栏带(左栏 -> 右栏)。"""
    if len(lines) < 8:
        return [sorted(lines, key=lambda l: (l.y0, l.x0))]

    mid = page_w / 2

    def is_full(l: Line) -> bool:
        if not (l.x0 < mid < l.x1):
            return False
        if l.x1 - l.x0 > page_w * 0.45:
            return True
        return abs((l.x0 + l.x1) / 2 - mid) <= page_w * 0.04

    wide = [
        l for l in lines
        if l.x1 - l.x0 > page_w * 0.08 and not is_full(l)
    ]
    wl = [l for l in wide if (l.x0 + l.x1) / 2 < mid]
    wr = [l for l in wide if (l.x0 + l.x1) / 2 >= mid]
    if len(wl) < 5 or len(wr) < 5:
        return [sorted(lines, key=lambda l: (l.y0, l.x0))]

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


# ---------- 段落合并 ----------

def _clean(text: str) -> str:
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _merge_lines(col: list[Line]) -> list[tuple[str, float, float, bool, bool]]:
    """把同一列的行合并成段。

    返回 [(文本, y, 字号, 粗体, 是否图表内), ...]
    图表内的行不与正文行合并。
    """
    if not col:
        return []

    xs = sorted(l.x1 for l in col)
    col_right = xs[int(len(xs) * 0.9)]
    x0s = sorted(l.x0 for l in col)
    col_left = x0s[int(len(x0s) * 0.1)]
    span = max(col_right - col_left, 1.0)
    gaps = [
        col[i].y0 - col[i - 1].y1
        for i in range(1, len(col))
        if 0 <= col[i].y0 - col[i - 1].y1 < 40
    ]
    norm_gap = median(gaps) if gaps else 3.0

    out: list[tuple[str, float, float, bool, bool]] = []
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
            if _HYPHEN_END.search(prev):
                parts[-1] = _HYPHEN_END.sub(r"\1", prev) + t
            else:
                parts[-1] = prev + " " + t
        first = buf[0]
        out.append((
            _clean(parts[0]),
            first.y0,
            max(l.size for l in buf),
            any(l.bold for l in buf),
            first.in_fig,
        ))
        buf.clear()

    for ln in col:
        if not buf:
            buf.append(ln)
            continue
        prev = buf[-1]
        # 图表内外不混合
        if prev.in_fig != ln.in_fig:
            flush()
            buf.append(ln)
            continue

        pt = prev.text.strip()
        gap = ln.y0 - prev.y1
        prev_full = prev.x1 >= col_left + span * 0.82
        prev_ends = bool(_SENT_END.search(pt))
        hyphen = bool(_HYPHEN_END.search(pt))
        same_size = abs(ln.size - prev.size) <= 0.6
        near = -prev.size * 0.5 <= gap <= max(norm_gap * 2.2, prev.size * 1.1)
        indent_ok = abs(ln.x0 - col_left) <= prev.size * 1.2 or ln.x0 >= prev.x0
        new_item = bool(_LIST_START.match(ln.text))
        # 图表内的短标签互不合并
        if ln.in_fig and (len(pt) < 30 or gap > prev.size * 1.2):
            flush()
            buf.append(ln)
            continue

        cont = (
            same_size and near and indent_ok and not new_item
            and (hyphen or prev_full or not prev_ends)
        )
        if cont:
            buf.append(ln)
        else:
            flush()
            buf.append(ln)

    flush()
    return out


# ---------- 分类 ----------

def _classify(
    text: str, size: float, bold: bool, in_fig: bool, body_size: float
) -> str:
    t = text.strip()
    if in_fig:
        return "figtext"
    if _CAPTION.match(t):
        return "caption"
    # 章节标题: 编号式或常见章节词
    if (_SECTION.match(t) or _SECTION_WORD.match(t)) and len(t) < 90:
        return "heading"
    if (size > body_size * 1.1 or (bold and size >= body_size)) and len(t) < 120:
        return "heading"
    # 纯数据行 -> 表格
    if _DATA_ROW.match(t) and len(t) > 2:
        return "table"
    if _LIST_START.match(t):
        return "list"
    # 短且无终止标点 -> 零散标签
    if len(t) < 60 and not _SENT_END.search(t):
        return "figtext"
    return "body"


# ---------- 公式占位 ----------

# 行内公式候选。
# 注意: 不能把 '×' '−' 这类在普通文本里常见的符号单独当公式
# (如 '21.8× faster'、'256×256' 都是正常表述)。
_FORMULA_PAT = re.compile(
    r"("
    r"\$[^$]{1,80}\$"                                  # LaTeX $...$
    r"|\\\(.{1,80}?\\\)"                               # LaTeX \(...\)
    r"|[A-Za-z][\w]{0,3}\s*=\s*[^\s,.;:)]{1,30}"       # x = ...
    r"|[A-Za-z]_\{[\w,+\-]{1,10}\}"                    # x_{ij}
    r"|[A-Za-z]\^\{[\w,+\-]{1,10}\}"                   # x^{2}
    r"|[∑∏∫√∇∂][^\s]{0,20}"                            # 求和/积分等算子
    r"|[≈≤≥≠≡∈∉⊂⊆∀∃]\s*[^\s]{1,20}"                    # 关系符
    r"|\b[a-zA-Z]\s*\([a-zA-Z](?:\s*,\s*[a-zA-Z])*\)"  # f(x), f(x,y)
    r"|\bL_?\{?(?:mcos|mdms|rec|gan|kl|total)\}?"       # 论文常见损失符号
    r")"
)


def mask_formulas(text: str) -> tuple[str, dict[str, str]]:
    """把行内公式替换为占位符, 返回 (masked_text, 还原表)。"""
    table: dict[str, str] = {}
    idx = 0

    def sub(m: re.Match) -> str:
        nonlocal idx
        idx += 1
        key = f"F{idx}"
        table[key] = m.group(0)
        return f"⟦{key}⟧"

    masked = _FORMULA_PAT.sub(sub, text)
    # 占位符过多说明整体是公式块, 不做替换
    if idx > 6 or (idx and len(table) * 8 > len(text) * 0.6):
        return text, {}
    return masked, table


def unmask_formulas(text: str, table: dict[str, str]) -> str:
    """把占位符还原为原始公式。"""
    for key, val in table.items():
        text = text.replace(f"⟦{key}⟧", val).replace(f"[{key}]", val)
    return text


# ---------- 噪声判定 ----------

def _is_noise(text: str) -> bool:
    t = text.strip()
    if len(t) < 2:
        return True
    if _PAGE_NOISE.match(t) or _URL_ONLY.match(t) or _ARXIV_MARK.search(t):
        return True
    letters = sum(c.isalpha() for c in t)
    if letters < max(2, len(t) * 0.2):
        return True
    return False


# ---------- 跨图表段落缝合 ----------

def _is_standalone_formula(text: str) -> bool:
    """判断是否为独立成行的公式块。

    典型形态: 很短、数学符号/占位符密度高、不含完整句子结构,
    常带编号如 '(1)'。这类块会把一个自然段截成两半。
    """
    t = text.strip()
    if not t or len(t) > 160:
        return False
    if _SENT_END.search(t) and len(t) > 60:
        return False
    # 去掉公式编号后评估
    core = re.sub(r"\(\d{1,2}\)\s*$", "", t).strip()
    if not core:
        return True
    # 占位符本身就是公式
    if "⟦" in core:
        letters = sum(c.isalpha() for c in re.sub(r"⟦[^⟧]*⟧", "", core))
        return letters < 25
    math_chars = sum(
        1 for c in core
        if c in "=+-−×÷/^_{}[]()∑∏∫√∇∂≈≤≥≠≡∈∉⊂⊆∀∃∥·"
        or "\u0370" <= c <= "\u03ff"       # 希腊字母
        or "\u2190" <= c <= "\u22ff"       # 数学运算符号
    )
    words = [w for w in re.split(r"\s+", core) if len(w) > 2 and w.isalpha()]
    # 数学符号占比高, 且几乎没有正常单词
    return math_chars >= 2 and len(words) <= 2 and math_chars * 4 >= len(core)


def _stitch_pages(pages: list[list[Block]], fold_formula: bool) -> list[list[Block]]:
    """跨页缝合正文。

    论文正文是连续的一条流, 被分页、插图与行间公式切碎。在全文层面缝合:
    - 残尾: 很短的正文块(如 'scalability.')若其前文未收尾, 并回前文
    - 续文: 小写开头的正文块并回前文
    - 独立公式行(fold_formula=True): 并回前文, 使被公式截断的段落复原
    向前查找会跨过 figtext/caption/table/list 以及页边界。
    """
    flat: list[tuple[int, Block]] = [
        (pi, b) for pi, pg in enumerate(pages) for b in pg
    ]

    def prev_body(kept: list[tuple[int, Block]]) -> Block | None:
        """向前找可作为续接目标的正文块。

        会跳过极短的表格残片(如 'Rec.'、'FID'): 它们虽被判为正文,
        但不是真正的段落, 不应成为愈合的屏障。
        """
        j = len(kept) - 1
        while j >= 0:
            b = kept[j][1]
            if b.kind == "body" and len(b.text.strip()) >= 12:
                return b
            j -= 1
        return None

    kept: list[tuple[int, Block]] = []
    for pi, b in flat:
        if b.kind not in ("body", "formula"):
            kept.append((pi, b))
            continue

        ct = b.text.strip()
        # 独立公式行: 并回前文并等待后续续文接上
        if fold_formula and b.kind == "body" and _is_standalone_formula(ct):
            prev = prev_body(kept)
            if prev is not None:
                prev.text = prev.text.rstrip() + " " + ct
                prev.formulas.update(b.formulas)
                continue

        lower_start = bool(ct) and (ct[0].islower() or ct[0] in ",;:)")
        short_tail = len(ct) < 30
        if not (lower_start or short_tail):
            kept.append((pi, b))
            continue

        prev = prev_body(kept)
        if prev is None:
            kept.append((pi, b))
            continue

        pt = prev.text.rstrip()
        hyphen = bool(_HYPHEN_END.search(pt))
        open_end = not _SENT_END.search(pt)
        if not (hyphen or open_end):
            kept.append((pi, b))
            continue

        if hyphen:
            prev.text = _HYPHEN_END.sub(r"\1", pt) + ct
        else:
            prev.text = pt + " " + ct
        prev.formulas.update(b.formulas)

    out: list[list[Block]] = [[] for _ in pages]
    for pi, b in kept:
        out[pi].append(b)
    for pg in out:
        for i, b in enumerate(pg):
            b.idx = i
    return out


# ---------- 主流程 ----------

def extract_paper(
    path: str,
    skip_refs: bool = True,
    skip_tables: bool = True,
    mask_math: bool = True,
    fold_formula: bool = True,
) -> tuple[list[list[Block]], list[tuple[float, float]]]:
    """解析学术论文, 返回 (每页块列表, 每页尺寸)。

    skip_refs     跳过参考文献/致谢/附录
    skip_tables   跳过纯数据表格行
    mask_math     行内公式用占位符替代后再翻译
    fold_formula  把独立成行的公式并回相邻正文, 复原被截断的段落
    """
    doc = pymupdf.open(path)
    try:
        return _extract_paper_doc(
            doc, skip_refs, skip_tables, mask_math, fold_formula
        )
    finally:
        # 无论解析中途是否异常都必须关闭, 否则句柄会残留在
        # 常驻 worker 进程里, Windows 上会锁住文件
        doc.close()


def _extract_paper_doc(
    doc: pymupdf.Document,
    skip_refs: bool,
    skip_tables: bool,
    mask_math: bool,
    fold_formula: bool,
) -> tuple[list[list[Block]], list[tuple[float, float]]]:
    """extract_paper 的主体(文档已由调用方保证关闭)。"""
    # 正文基准字号
    size_chars: dict[float, int] = {}
    all_lines: list[list[Line]] = []
    for page in doc:
        lines = _page_lines(page)
        all_lines.append(lines)
        for l in lines:
            if not l.in_fig:
                size_chars[l.size] = size_chars.get(l.size, 0) + len(l.text)
    body_size = max(size_chars, key=size_chars.get) if size_chars else 10.0

    pages: list[list[Block]] = []
    sizes: list[tuple[float, float]] = []
    in_skip = False
    total = doc.page_count

    for pno, page in enumerate(doc):
        rect = page.rect
        sizes.append((rect.width, rect.height))
        header_lim = rect.height * 0.05
        footer_lim = rect.height * 0.95

        lines = [
            l for l in all_lines[pno]
            if (not (l.y1 < header_lim or l.y0 > footer_lim))
            or len(l.text.strip()) > 80
        ]

        blocks: list[Block] = []
        for col in _split_columns(lines, rect.width):
            for text, y, size, bold, in_fig in _merge_lines(col):
                if _is_noise(text):
                    continue
                kind = _classify(text, size, bold, in_fig, body_size)

                # 参考文献/致谢/附录: 后半部分的独立标题触发
                if (
                    skip_refs and not in_skip
                    and kind == "heading"
                    and _REF_HEAD.match(text)
                    and pno >= total * 0.4
                ):
                    in_skip = True
                if in_skip and skip_refs:
                    continue
                if kind == "table" and skip_tables:
                    continue

                formulas: dict[str, str] = {}
                if mask_math and kind in ("body", "caption", "list"):
                    text, formulas = mask_formulas(text)

                blocks.append(
                    Block(idx=len(blocks), text=text, kind=kind, y=y,
                          formulas=formulas)
                )

        pages.append(blocks)

    # 正文是跨页连续的一条流, 在全文层面缝合
    pages = _stitch_pages(pages, fold_formula)
    return pages, sizes


# ---------- 论文自动识别 ----------

def looks_like_paper(path: str, probe_pages: int = 4) -> bool:
    """粗判是否为学术论文(双栏 + 图表 + 学术章节词)。

    只抽查前若干页, 用于 paper_mode=auto 时自动选择解析策略。
    """
    try:
        doc = pymupdf.open(path)
    except Exception:
        return False

    try:
        n = min(doc.page_count, probe_pages)
        if n == 0:
            return False

        two_col_pages = 0
        academic_hits = 0
        for i in range(n):
            page = doc[i]
            w = page.rect.width
            mid = w / 2
            td = page.get_text("dict")
            lines = []
            for blk in td.get("blocks", []):
                if blk.get("type") != 0:
                    continue
                for ln in blk.get("lines", []):
                    x0, y0, x1, y1 = ln["bbox"]
                    if x1 - x0 > w * 0.08:
                        lines.append((x0, x1))
            left = sum(1 for x0, x1 in lines if (x0 + x1) / 2 < mid)
            right = sum(1 for x0, x1 in lines if (x0 + x1) / 2 >= mid)
            if left >= 8 and right >= 8:
                two_col_pages += 1

            text = page.get_text()
            if re.search(
                r"\b(abstract|introduction|related\s+work|we\s+propose|"
                r"experiments?|arxiv|doi:)\b", text, re.I
            ):
                academic_hits += 1

        return two_col_pages >= max(1, n // 2) and academic_hits >= 1
    finally:
        doc.close()
