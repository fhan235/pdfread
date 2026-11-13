"""PDF 操作在专用进程执行，不在线程间共享 PyMuPDF 对象。"""
import re
from pathlib import Path
import pymupdf
from .extract import extract_pages


def inspect_pdf(path: str, skip_references: bool = True, parse_options: dict | None = None) -> dict:
    before = Path(path).stat()
    parse_options = parse_options or {}
    paper_mode = False
    if parse_options.get("paper_mode") != "off":
        from . import paper
        paper_mode = parse_options.get("paper_mode") == "on" or paper.looks_like_paper(path)
    if paper_mode:
        pages, sizes = paper.extract_paper(path, skip_refs=parse_options.get("skip_refs", True),
                                           skip_tables=parse_options.get("skip_tables", True),
                                           mask_math=parse_options.get("mask_math", True),
                                           fold_formula=parse_options.get("fold_formula", True))
    else:
        pages, sizes = extract_pages(path, skip_references=skip_references)
    with pymupdf.open(path) as doc:
        title = doc.metadata.get("title") or ""
        # [[级别, 标题, 页码]], 截断防御异常文档的超长目录
        toc = [[int(lvl), str(t)[:200], int(pg)] for lvl, t, pg in
               doc.get_toc()[:500] if 1 <= pg <= len(pages)]
    after = Path(path).stat()
    stamp = (after.st_mtime_ns, after.st_size)
    if stamp != (before.st_mtime_ns, before.st_size):
        raise ValueError("PDF 在解析期间被修改，请重新打开")
    return {"pages": pages, "sizes": sizes, "title": title, "toc": toc,
            "stamp": stamp, "paper_mode": paper_mode}


def render_page(path: str, num: int, dpi: int, stamp: tuple) -> bytes:
    st = Path(path).stat()
    if (st.st_mtime_ns, st.st_size) != stamp:
        raise ValueError("PDF 已被修改，请重新打开")
    with pymupdf.open(path) as doc:
        return doc[num - 1].get_pixmap(dpi=dpi).tobytes("png")


def render_page_svg(path: str, num: int, stamp: tuple) -> bytes:
    """矢量页面(SVG, 文字转路径): 任意缩放都清晰, 且与缩放/DPI 无关。"""
    st = Path(path).stat()
    if (st.st_mtime_ns, st.st_size) != stamp:
        raise ValueError("PDF 已被修改，请重新打开")
    with pymupdf.open(path) as doc:
        return doc[num - 1].get_svg_image(text_as_path=True).encode("utf-8")


def render_thumb(path: str, width: int = 240) -> bytes:
    """文库封面缩略图: 首页按目标宽度渲染成 PNG。

    与 render_page 的区别是不校验 stamp —— 文库里的文档通常没有打开,
    没有 stamp 可比; 结果会按 (mtime, size) 落盘缓存, 文件变了自然换 key。
    """
    with pymupdf.open(path) as doc:
        if not doc.page_count:
            raise ValueError("空文档")
        page = doc[0]
        rect = page.rect
        if rect.width <= 0:
            raise ValueError("页面尺寸异常")
        # 按宽度换算缩放比, 控制在合理范围, 避免超大页面吃内存
        zoom = max(0.05, min(3.0, width / rect.width))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")


MAX_LINKS = 2000   # 单页链接上限, 防御异常文档

_FIG_CAPTION = re.compile(r"^\s*(?:fig(?:ure)?\.?|图)\s*[A-Z]?\.?\d", re.I)
_ANY_CAPTION = re.compile(r"^\s*(?:fig(?:ure)?\.?|tab(?:le)?\.?|图|表)\s*[A-Z]?\.?\d", re.I)
_FIG_GAP = 40.0   # 图形与图注 / 子图之间允许的最大竖直间距(pt)


def _text_lines(page: pymupdf.Page) -> list[tuple]:
    """页面文字行: [(x0, y0, x1, y1, 文本)], 按 (块, 行) 聚合词。"""
    rows: dict[tuple, list] = {}
    for w in page.get_text("words"):
        rows.setdefault((w[5], w[6]), []).append(w)
    out = []
    for ws in rows.values():
        ws.sort(key=lambda w: w[0])
        out.append((min(w[0] for w in ws), min(w[1] for w in ws),
                    max(w[2] for w in ws), max(w[3] for w in ws),
                    " ".join(w[4] for w in ws)))
    return out


def _graphic_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """页面上的图形区域: 位图 + 矢量绘制。

    位图用 get_image_info(): 实测能识别 Form XObject 内的插图
    (论文用 \\includegraphics 嵌入的 PDF/PNG 多是这种), 而
    get_text("dict") 的图片块识别不到。
    """
    area = page.rect.width * page.rect.height
    out = []
    raw = [i.get("bbox") for i in page.get_image_info()]
    raw += [d.get("rect") for d in page.get_cdrawings()]
    for b in raw:
        try:
            r = pymupdf.Rect(b) & page.rect
        except Exception:
            continue
        # 细线不算图形: 表格横线、下划线、分隔线紧贴在图上方时
        # 会被当成"相邻子图"吞进来, 把滚动位置抬到表格顶部
        if r.is_empty or r.width < 8 or r.height < 8:
            continue
        if r.width * r.height > area * 0.9:   # 整页背景/裁剪框
            continue
        out.append(r)
    return out


def _figure_view(page: pymupdf.Page, x: float, y: float, geom: dict) -> float | None:
    """目标是图片的图注, 且图注上方紧挨着图形时, 返回图形顶部的纵坐标。

    hyperref(以及 Word 交叉引用)把图片锚点放在图注上, 而图注通常在
    图的下方, 直接跳过去图会在视野之外。表格图注一般在表格上方,
    落点本身就合适, 不做调整。

    只处理未旋转页面: 词坐标与图形坐标都是未旋转坐标, 旋转页上
    "上方" 的含义也不同, 这种情况保持跳到图注。
    """
    if page.rotation:
        return None
    if "words" not in geom:
        geom["words"] = page.get_text("words")
    words = geom["words"]
    near = [w for w in words if y - 4 <= w[1] <= y + 16]
    if not near:
        return None
    # 锚点所在的行: 双栏论文同一高度另一栏可能是正文, 取离锚点 x 最近的词所在行
    def xdist(w):
        return 0.0 if w[0] - 6 <= x <= w[2] + 6 else min(abs(x - w[0]), abs(x - w[2]))
    row_y = min(near, key=lambda w: (xdist(w), w[1]))[1]
    row = sorted((w for w in words if abs(w[1] - row_y) < 2), key=lambda w: w[0])
    # 图注标签: hyperref 把锚点放在「Figure N:」之后(实测), Word 交叉引用放在
    # 标签起点; 因此在锚点左侧 90pt 内找标签。按词匹配而不按 PyMuPDF 的行:
    # 粗体标签与图注正文常被切成两个"行"
    def starts(i, pattern):
        text = row[i][4] + (" " + row[i + 1][4] if i + 1 < len(row) else "")
        return pattern.match(text)

    start = next((i for i, w in enumerate(row)
                  if x - 90 <= w[0] <= x + 6 and starts(i, _FIG_CAPTION)), None)
    if start is None:
        return None
    # 图注在这一行的实际宽度: 向右延伸, 遇到另一个图注标签(双栏时同一高度
    # 的另一栏图注)或明显的大间隔就停。整行宽的图注下面可能挂着左右两个子图
    end = start
    for j in range(start + 1, len(row)):
        if row[j][0] - row[j - 1][2] > 40 or starts(j, _ANY_CAPTION):
            break
        end = j
    cap_y0 = min(w[1] for w in row[start:end + 1])
    cap_x0, cap_x1 = row[start][0] - 10, row[end][2] + 10
    if "lines" not in geom:
        geom["lines"] = _text_lines(page)
    if "rects" not in geom:
        geom["rects"] = _graphic_rects(page)
    pool = [r for r in geom["rects"] if r.y1 <= cap_y0 + 3]

    def x_overlap(r, x0, x1):
        return r.x0 < x1 and r.x1 > x0

    seeds = [i for i, r in enumerate(pool)
             if r.y1 >= cap_y0 - _FIG_GAP and x_overlap(r, cap_x0, cap_x1)]
    if not seeds:
        return None
    region = pymupdf.Rect(pool[seeds[0]])
    for i in seeds[1:]:
        region |= pool[i]
    # 向上吸收相邻的子图; 中间隔着别的图注(另一个图/表)就停
    rest = set(range(len(pool))) - set(seeds)
    grew = True
    while grew:
        grew = False
        for i in sorted(rest):
            r = pool[i]
            if r.y1 < region.y0 - _FIG_GAP or not x_overlap(r, region.x0, region.x1):
                continue
            # 在两者合并后的宽度内找夹在中间的图注: 上方那张图的图注
            # 可能很窄, 只和它自己的图重叠, 不和当前图区重叠
            ux0, ux1 = min(r.x0, region.x0), max(r.x1, region.x1)
            between = [l for l in geom["lines"]
                       if r.y1 - 2 <= l[1] and l[3] <= region.y0 + 2
                       and l[0] < ux1 and l[2] > ux0]
            if any(_ANY_CAPTION.match(l[4]) for l in between):
                continue
            region |= r
            rest.discard(i)
            grew = True
    top = max(region.y0, page.rect.y0)
    return top if top < cap_y0 - 6 else None


def page_links(path: str, num: int, stamp: tuple) -> list[list]:
    """一页内指向本文档其他位置的链接(引用、图表/章节交叉引用等)。

    返回 [x0, y0, x1, y1, 目标页码(1 起), 锚点纵坐标, 滚动纵坐标] 精简行,
    均为**显示坐标**(PDF 点, 左上原点, 已含页面旋转), 与 page.rect /
    INFO.sizes / SVG 页面图同一坐标系。锚点用于高亮落点; 滚动纵坐标
    通常与锚点相同, 指向图片时为图的顶部(见 _figure_view)。
    外链、跨文件链接与解析不出目标页的链接一律忽略。

    PyMuPDF 对两类内部链接给出的目标点坐标系不同(已实测):
    - LINK_GOTO(显式目标): MuPDF 已换算成目标页的显示坐标
    - LINK_NAMED(命名目标, LaTeX hyperref 的引用都是这种): 来自
      resolve_names(), 仍是 PDF 原始坐标(左下原点、未旋转),
      需乘目标页的 transformation_matrix 与 rotation_matrix
    """
    st = Path(path).stat()
    if (st.st_mtime_ns, st.st_size) != stamp:
        raise ValueError("PDF 已被修改，请重新打开")
    out: list[list] = []
    geoms: dict[int, dict] = {}   # 目标页的文字行/图形, 同页多个链接只算一次
    with pymupdf.open(path) as doc:
        page = doc[num - 1]
        for link in page.get_links():
            kind = link.get("kind")
            if kind not in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED):
                continue
            target = link.get("page")
            if not isinstance(target, int) or not 0 <= target < doc.page_count:
                continue
            r = pymupdf.Rect(link["from"])
            if r.is_empty or r.is_infinite:
                continue
            tpage = doc[target]
            x = y = 0.0   # 目标无具体位置(/Fit 等)时跳到页首
            to = link.get("to")
            if to is not None:
                try:
                    pt = pymupdf.Point(to)
                    if kind == pymupdf.LINK_NAMED:
                        pt = pt * tpage.transformation_matrix * tpage.rotation_matrix
                    x, y = pt.x, pt.y
                except Exception:
                    x = y = 0.0
            if y != y or y in (float("inf"), float("-inf")):   # NaN / Inf
                y = 0.0
            if x != x or x in (float("inf"), float("-inf")):
                x = 0.0
            y = min(max(y, 0.0), tpage.rect.height)
            view = None
            # 参考文献条目不可能是图注, 跳过分析省掉逐个读目标页
            if not str(link.get("nameddest", "")).startswith("cite."):
                try:
                    view = _figure_view(tpage, x, y, geoms.setdefault(target, {}))
                except Exception:
                    view = None   # 分析失败不影响跳转本身
            out.append([round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2),
                        target + 1, round(y, 2), round(y if view is None else view, 2)])
            if len(out) >= MAX_LINKS:
                break
    return out


def page_words(path: str, num: int, stamp: tuple) -> list[list]:
    """一页的全部词坐标(PDF 点), 供前端划线吸附。

    返回 [x0, y0, x1, y1, word, block, line] 精简行。
    排序遵循阅读顺序: 块(≈栏) -> 行 -> 词。双栏论文中
    不能用纯几何排序(y, x), 否则同一视觉行的左右两栏词会交错,
    拖选右栏时会误选中左栏的词。
    """
    st = Path(path).stat()
    if (st.st_mtime_ns, st.st_size) != stamp:
        raise ValueError("PDF 已被修改，请重新打开")
    with pymupdf.open(path) as doc:
        words = doc[num - 1].get_text("words")
    lines: dict[tuple, list] = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)
    ordered = []
    for key in sorted(lines, key=lambda k: (k[0], min(w[1] for w in lines[k]))):
        ordered.extend(sorted(lines[key], key=lambda w: w[0]))
    return [[round(w[0], 2), round(w[1], 2), round(w[2], 2), round(w[3], 2),
             w[4], w[5], w[6]] for w in ordered]
