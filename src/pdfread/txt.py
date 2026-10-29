"""TXT 解析: 编码检测 + 章节切分 + 段落合并。

产出与 inspect_epub 相同的结构(章节/块即"页"), 供统一阅读与翻译管线复用:

- 编码: BOM 优先, 其后依次尝试 utf-8 / gb18030 / big5, 最后 latin-1 兜底
- 章节: 「第N章/回/部/卷」「Chapter N」等标题行识别为一页起点并计入目录
- 无章节标记时按字符数切块(约 8000 字/页), 保证进度记忆与跳转可用
- 段落: 空行分段; 段内硬折行合并(英文断词补空格, 中文直接拼接)
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from .extract import Para

_MAX_SIZE = 50 * 1024 * 1024   # 50 MB, TXT 再大就是异常文件
_PAGE_CHARS = 8000             # 无章节标记时的每页字符预算

_CHAPTER = re.compile(
    r"^\s*(?:"
    r"第\s*[0-9零一二三四五六七八九十百千两]+\s*[章回部卷节篇集]"
    r"|chapter\s+\d+|part\s+\d+|prologue|epilogue"
    r"|序[章言曲]|楔子|尾声|后记|前言|引言"
    r")[^\n]{0,60}$",
    re.I,
)
_WS = re.compile(r"\s+")


def _decode(raw: bytes) -> str:
    """按 BOM → utf-8 → gb18030 → big5 → latin-1 顺序解码。"""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _merge_lines(lines: list[str]) -> str:
    """段内硬折行合并: 英文断词补空格, 中文直接拼接。"""
    out = lines[0]
    for line in lines[1:]:
        if re.search(r"[A-Za-z0-9-]$", out) and re.match(r"[A-Za-z0-9]", line):
            out += " " + line
        else:
            out += line
    return out.strip()


def _paragraphs(text: str) -> list[str]:
    """分段。两种常见排版自动识别:
    - 空行分段(西式): 段内硬折行合并
    - 行即段(中文网文常见, 几乎无空行): 每行独立成段
    过长的段按字符数硬切, 避免超单次翻译预算。"""
    lines = text.splitlines()
    nonempty = [l for l in lines if l.strip()]
    if not nonempty:
        return []
    blank_ratio = 1 - len(nonempty) / max(len(lines), 1)
    if blank_ratio < 0.05:
        paras = [_WS.sub(" ", l).strip() for l in nonempty]
    else:
        paras = []
        buf: list[str] = []
        for raw in lines:
            line = _WS.sub(" ", raw).strip()
            if not line:
                if buf:
                    paras.append(_merge_lines(buf))
                    buf = []
                continue
            buf.append(line)
        if buf:
            paras.append(_merge_lines(buf))
    out: list[str] = []
    for p in paras:
        while len(p) > 4000:
            cut = p.rfind("。", 0, 4000)
            cut = cut + 1 if cut > 2000 else 4000
            out.append(p[:cut])
            p = p[cut:]
        if p:
            out.append(p)
    return out


def inspect_txt(path: str, parse_options: dict | None = None) -> dict:
    """解析 TXT, 返回与 inspect_pdf 兼容的结构(章节/块即页)。"""
    p = Path(path)
    before = p.stat()
    if before.st_size > _MAX_SIZE:
        raise ValueError("TXT 文件过大(上限 50MB)")
    text = _decode(p.read_bytes())

    # 先按章节标题切块; 无章节则全文一块
    chapters: list[tuple[str | None, list[str]]] = []  # (标题, 段落)
    cur_title: str | None = None
    cur: list[str] = []
    for para in _paragraphs(text):
        if len(para) <= 62 and _CHAPTER.match(para):
            if cur:
                chapters.append((cur_title, cur))
            cur_title, cur = para, []
        else:
            cur.append(para)
    if cur:
        chapters.append((cur_title, cur))
    if not chapters:
        raise ValueError("TXT 中没有可读内容")

    # 组装页: 有章节标题则一章一页, 否则按字符预算切块
    pages: list[list[Para]] = []
    htmls: list[str] = []
    toc: list[list] = []

    def emit(title: str | None, paras: list[str]) -> None:
        blocks: list[tuple[str, str]] = []   # (kind, text)
        if title:
            blocks.append(("heading", title))
            toc.append([1, title[:200], len(pages) + 1])
        blocks += [("body", t) for t in paras]

        buf: list[tuple[str, str]] = []
        size = 0

        def flush() -> None:
            nonlocal buf, size
            if not buf:
                return
            pages.append([Para(i, t, k, float(i))
                          for i, (k, t) in enumerate(buf)])
            htmls.append("".join(
                f"<h2>{escape(t)}</h2>" if k == "heading" else f"<p>{escape(t)}</p>"
                for k, t in buf))
            buf, size = [], 0

        for kind, t in blocks:
            if size + len(t) > _PAGE_CHARS and buf:
                flush()
            buf.append((kind, t))
            size += len(t)
        flush()

    for title, paras in chapters:
        if title is None and len(chapters) == 1:
            emit(None, paras)          # 无章节: 纯按字符切块
        elif not paras and title:
            continue                     # 空章节(如纯标题页)跳过
        else:
            emit(title, paras)

    if not pages:
        raise ValueError("TXT 中没有可读内容")

    after = p.stat()
    stamp = (after.st_mtime_ns, after.st_size)
    if stamp != (before.st_mtime_ns, before.st_size):
        raise ValueError("文件在解析期间被修改，请重新打开")
    return {
        "pages": pages,
        "sizes": [(612, 792)] * len(pages),  # 占位, TXT 无固定版面
        "title": p.stem,
        "toc": toc,
        "stamp": stamp,
        "paper_mode": False,
        "fmt": "txt",
        "html": htmls,
    }
