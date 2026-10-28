"""PDF 操作在专用进程执行，不在线程间共享 PyMuPDF 对象。"""
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
