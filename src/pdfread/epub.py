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
# 解压上限: 防恶意 EPUB 构造 zip bomb 耗尽内存
_MAX_MEMBER = 20 * 1024 * 1024    # 单个包内文件
_MAX_TOTAL = 300 * 1024 * 1024    # 整包累计
_WS = re.compile(r"\s+")
_NCX_NS = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}


def _headingish(el, text: str) -> bool:
    """判定短段落是否为标题: 类名提示, 或全部文字被粗体包裹(calibre 风格)。"""
    if not (0 < len(text) <= 80):
        return False
    cls = " ".join(el.get("class") or [])
    if _HEADING_CLS.search(cls):
        return True
    covered = sum(len(_text_of(d)) for d in el.find_all(["b", "strong"]))
    covered += sum(len(_text_of(d))
                   for d in el.find_all(class_=re.compile(r"(?i)bold")))
    return covered >= len(text) * 0.9


def _read_ncx(zf: zipfile.ZipFile, manifest: dict, media: dict,
              base_dir: str, member_page: dict) -> list:
    """读 EPUB2 的 toc.ncx 正式目录, 返回 [[层级, 标题, 页码]]。"""
    ncx_href = next((h for i, h in manifest.items()
                     if media.get(i) == "application/x-dtbncx+xml"), None)
    if not ncx_href:
        return []
    ncx_path = posixpath.normpath(posixpath.join(base_dir, ncx_href))
    try:
        root = ET.fromstring(_read_member(zf, ncx_path))
    except (KeyError, ValueError, ET.ParseError):
        return []
    out = []

    def walk(node, depth):
        for np in node.findall("ncx:navPoint", _NCX_NS):
            label = np.find("ncx:navLabel/ncx:text", _NCX_NS)
            content = np.find("ncx:content", _NCX_NS)
            if label is not None and label.text and content is not None:
                src = posixpath.normpath(posixpath.join(
                    posixpath.dirname(ncx_path),
                    (content.get("src") or "").split("#")[0]))
                page = member_page.get(src)
                if page:
                    out.append([min(depth, 4), label.text.strip()[:200], page])
            walk(np, depth + 1)

    navmap = root.find("ncx:navMap", _NCX_NS)
    if navmap is not None:
        walk(navmap, 1)
    return out


def _read_member(zf: zipfile.ZipFile, member: str,
                 limit: int = _MAX_MEMBER) -> bytes:
    """读包内文件, 先按未压缩大小设上限(zip bomb 防护)。"""
    info = zf.getinfo(member)
    if info.file_size > limit:
        raise ValueError(f"包内文件过大(超过 {limit // 1048576}MB 上限)")
    return zf.read(member)


def _opf_path(zf: zipfile.ZipFile) -> str:
    root = ET.fromstring(_read_member(zf, "META-INF/container.xml"))
    node = root.find(".//container:rootfile", _NS)
    if node is None or not node.get("full-path"):
        raise ValueError("EPUB 缺少 OPF 声明")
    return node.get("full-path")


def _text_of(tag) -> str:
    return _WS.sub(" ", tag.get_text(" ", strip=True)).strip()


# 出版商常用 class 名暗示标题(其样式表被剥离后, 标题会退化成普通段落)
_HEADING_CLS = re.compile(
    r"(?i)(?:^|[\s_-])(?:chapter[\s_-]*(?:title|heading|name)|title|heading|h[1-6])"
    r"(?:$|[\s_-])"
)


def _sanitize(soup, base: str) -> str:
    """净化章节内容: 去脚本/事件/内联样式, 图片重写到包内资源端点。

    class 保留(仅字符串, 便于标题启发式识别); id 清除(防与应用内
    #left 等查询冲突); style 清除(可含 url() 外发请求/fixed 覆盖层)。

    base 是章节文件自身所在目录(按 EPUB 规范, src 相对章节解析)。
    """
    # 标题启发式: 类名含 title/heading 或全粗体的短 p/div 提升为 h2
    for tag in soup.find_all(["p", "div"]):
        if _headingish(tag, _text_of(tag)):
            tag.name = "h2"
    for tag in soup.find_all(["script", "style", "link", "meta", "iframe",
                              "object", "embed", "form", "input", "button",
                              "audio", "video"]):
        tag.decompose()
    for tag in soup.find_all(True):
        # 事件属性、内联样式、id 清除
        for attr in list(tag.attrs):
            la = attr.lower()
            if la.startswith("on") or la in ("style", "id"):
                del tag[attr]
        for attr in ("href", "src"):
            val = tag.get(attr)
            if isinstance(val, str) and re.match(r"(?i)\s*(javascript|data|vbscript):", val):
                del tag[attr]
        if tag.name not in _ALLOWED:
            tag.unwrap()
        elif tag.name == "img":
            src = tag.get("src", "")
            if not src:
                # srcset 兜底: 取第一个候选
                srcset = tag.get("srcset", "")
                if srcset:
                    src = srcset.split(",")[0].strip().split(" ")[0]
                    tag["src"] = src
                    del tag["srcset"]
            if not src:
                tag.decompose()
            elif re.match(r"(?i)\s*https?://", src):
                # 外链图片(追踪/泄漏风险)替换为占位说明
                tag.replace_with("[外链图片已省略]")
            else:
                from urllib.parse import unquote
                tag["src"] = "__EPUB_RES__" + posixpath.normpath(
                    posixpath.join(base, unquote(src)))
    return "".join(str(c) for c in soup.children)


def inspect_epub(path: str, parse_options: dict | None = None) -> dict:
    """解析 EPUB, 返回与 inspect_pdf 兼容的结构(章节即页)。"""
    before = Path(path).stat()
    pages: list[list[Para]] = []
    htmls: list[str] = []
    toc: list[list] = []

    try:
        total_inflated = 0
        with zipfile.ZipFile(path) as zf:
            opf = _opf_path(zf)
            base_dir = posixpath.dirname(opf)
            root = ET.fromstring(_read_member(zf, opf))

            title_node = root.find(".//dc:title", _NS)
            title = (title_node.text or "").strip() if title_node is not None else ""

            manifest = {}
            media = {}
            for item in root.findall(".//opf:manifest/opf:item", _NS):
                manifest[item.get("id")] = item.get("href", "")
                media[item.get("id")] = item.get("media-type", "")

            member_page: dict = {}   # 章节包内路径 -> 页码, 供 NCX 目录定位
            for ref in root.findall(".//opf:spine/opf:itemref", _NS):
                href = manifest.get(ref.get("idref"), "")
                if not href or not re.search(r"\.(x?html?)$", href, re.I):
                    continue
                member = posixpath.normpath(posixpath.join(base_dir, href))
                try:
                    raw = _read_member(zf, member)
                except KeyError:
                    continue
                total_inflated += len(raw)
                if total_inflated > _MAX_TOTAL:
                    raise ValueError("EPUB 解压总量过大(疑似压缩炸弹)")
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
                    kind = _BLOCK_KIND[el.name]
                    # 粗体短段落( calibre 风格标题)归入 heading
                    if kind == "body" and _headingish(el, text):
                        kind = "heading"
                    paras.append(Para(len(paras), text, kind, float(len(paras))))
                if not paras:
                    continue  # 封面/空白章节不占页

                member_page[member] = len(pages) + 1
                # 章节首个标题作为目录项(NCX 缺失时的回退)
                head = next((p.text for p in paras if p.kind == "heading"), None)
                if head:
                    toc.append([1, head[:200], len(pages) + 1])
                pages.append(paras)
                # 图片 src 相对章节文件自身目录解析, 不是 OPF 目录
                htmls.append(_sanitize(body, posixpath.dirname(member)))

            # 目录: 优先采用 EPUB 自带的 NCX 目录(比猜章节标题可靠)
            ncx_toc = _read_ncx(zf, manifest, media, base_dir, member_page)
            if ncx_toc:
                toc = ncx_toc
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
        "toc": toc,
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
            data = _read_member(zf, member)
        except KeyError:
            raise ValueError("资源不存在") from None
    return data, _IMG_EXT[ext]
