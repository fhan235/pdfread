"""PDF 操作在专用进程执行，不在线程间共享 PyMuPDF 对象。"""
from pathlib import Path
import pymupdf
from .extract import extract_pages


def inspect_pdf(path: str, skip_references: bool = True) -> dict:
    before = Path(path).stat()
    pages, sizes = extract_pages(path, skip_references=skip_references)
    with pymupdf.open(path) as doc:
        title = doc.metadata.get("title") or ""
    after = Path(path).stat()
    stamp = (after.st_mtime_ns, after.st_size)
    if stamp != (before.st_mtime_ns, before.st_size):
        raise ValueError("PDF 在解析期间被修改，请重新打开")
    return {"pages": pages, "sizes": sizes, "title": title, "stamp": stamp}


def render_page(path: str, num: int, dpi: int, stamp: tuple) -> bytes:
    st = Path(path).stat()
    if (st.st_mtime_ns, st.st_size) != stamp:
        raise ValueError("PDF 已被修改，请重新打开")
    with pymupdf.open(path) as doc:
        return doc[num - 1].get_pixmap(dpi=dpi).tobytes("png")
