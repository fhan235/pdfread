"""EPUB 解析: 章节即页。

EPUB 是 ZIP 包 + XHTML 章节, 段落结构天然存在(<p>/<h1-h6>/<li>),
不需要 PDF 那套版面分析, 提取质量反而更高。

- inspect_epub: 按 spine 顺序把每个内容文档作为一个"页",
  产出与 inspect_pdf 相同的结构 + 净化后的章节 HTML(供左栏渲染)
- read_resource: 供服务端按需取包内图片等资源(左栏插图)
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from .extract import Para

_NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# 提取为待译段落的块级标签 -> 段落类型
_BLOCK_KIND = {
    **{f"h{i}": "heading" for i in range(1, 7)},
    "li": "list",
    "p": "body",
    "blockquote": "body",
    "pre": "body",
}
# 净化后允许保留的标签(其余解包为纯文本)
_ALLOWED = set(_BLOCK_KIND) | {
    "b", "strong", "i", "em", "u", "s", "sub", "sup", "br", "span",
    "div", "section", "img", "table", "thead", "tbody", "tr", "td", "th",
}
_IMG_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
}
_WS = re.compile(r"\s+")


def _opf_path(zf: zipfile.ZipFile) -> str:
    root = ET.fromstring(zf.read("META-INF/container.xml"))
    node = root.find(".//container:rootfile", _NS)
    if node is None or not node.get("full-path"):
        raise ValueError("EPUB 缺少 OPF 声明")
    return node.get("full-path")


def _text_of(tag) -> str:
    return _WS.sub(" ", tag.get_text(" ", strip=True)).strip()


def _sanitize(soup, base: str) -> str:
    """净化章节内容: 去脚本/事件/外链, 图片重写到包内资源端点。"""
    for tag in soup.find_all(["script", "style", "link", "meta", "iframe",
                              "object", "embed", "form", "input", "button",
                              "audio", "video"]):
        tag.decompose()
    for tag in soup.find_all(True):
        # 事件属性与危险协议
        for attr in list(tag.attrs):
            if attr.lower().startswith("on"):
                del tag[attr]
        for attr in ("href", "src"):
            val = tag.get(attr)
            if isinstance(val, str) and re.match(r"(?i)\s*(javascript|data|vbscript):", val):
                del tag[attr]
        if tag.name not in _ALLOWED:
            tag.unwrap()
        elif tag.name == "img":
            src = tag.get("src", "")
            if src:
                tag["src"] = "__EPUB_RES__" + posixpath.normpath(
                    posixpath.join(base, src))
            else:
                tag.decompose()
    return "".join(str(c) for c in soup.children)


def inspect_epub(path: str, parse_options: dict | None = None) -> dict:
    """解析 EPUB, 返回与 inspect_pdf 兼容的结构(章节即页)。"""
    before = Path(path).stat()
    pages: list[list[Para]] = []
    htmls: list[str] = []

    try:
        with zipfile.ZipFile(path) as zf:
            opf = _opf_path(zf)
            base_dir = posixpath.dirname(opf)
            root = ET.fromstring(zf.read(opf))

            title_node = root.find(".//dc:title", _NS)
            title = (title_node.text or "").strip() if title_node is not None else ""

            manifest = {}
            for item in root.findall(".//opf:manifest/opf:item", _NS):
                manifest[item.get("id")] = item.get("href", "")

            for ref in root.findall(".//opf:spine/opf:itemref", _NS):
                href = manifest.get(ref.get("idref"), "")
                if not href or not re.search(r"\.(x?html?)$", href, re.I):
                    continue
                member = posixpath.normpath(posixpath.join(base_dir, href))
                try:
                    raw = zf.read(member)
                except KeyError:
                    continue
                soup = BeautifulSoup(raw, "html.parser")
                body = soup.find("body") or soup

                paras = []
                for el in body.find_all(list(_BLOCK_KIND)):
                    # 嵌套块只取最外层(如 li 里的 p)
                    if el.parent and el.parent.name in _BLOCK_KIND:
                        continue
                    text = _text_of(el)
                    if len(text) < 2:
                        continue
                    paras.append(Para(len(paras), text,
                                      _BLOCK_KIND[el.name], float(len(paras))))
                if not paras:
                    continue  # 封面/空白章节不占页

                pages.append(paras)
                htmls.append(_sanitize(body, base_dir))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError(f"EPUB 无法解析: {exc}") from None

    if not pages:
        raise ValueError("EPUB 中没有可读章节")

    after = Path(path).stat()
    stamp = (after.st_mtime_ns, after.st_size)
    if stamp != (before.st_mtime_ns, before.st_size):
        raise ValueError("文件在解析期间被修改，请重新打开")
    return {
        "pages": pages,
        "sizes": [(612, 792)] * len(pages),  # 占位, EPUB 无固定版面
        "title": title,
        "stamp": stamp,
        "paper_mode": False,
        "fmt": "epub",
        "html": htmls,
    }


def read_resource(path: str, member: str) -> tuple[bytes, str]:
    """读取 EPUB 包内资源(图片), 返回 (内容, MIME)。防路径穿越。"""
    member = posixpath.normpath(member)
    if member.startswith("../") or member.startswith("/"):
        raise ValueError("非法资源路径")
    ext = Path(member).suffix.lower()
    if ext not in _IMG_EXT:
        raise ValueError("仅允许图片资源")
    with zipfile.ZipFile(path) as zf:
        try:
            data = zf.read(member)
        except KeyError:
            raise ValueError("资源不存在") from None
    return data, _IMG_EXT[ext]
