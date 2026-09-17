"""从 URL 获取 PDF(带 SSRF 防护)。

安全要点:
- 仅允许 http/https, URL 不允许内嵌凭证
- 解析域名后校验所有 A/AAAA 记录, 拦截环回/私网/保留地址
  (并按内网环境约定拦截 9/11/21/30 段)
- 手动跟随重定向, 每一跳重新校验, 防"公网链接 302 到内网"
- 流式下载, 首块校验 %PDF 魔数, 限制大小
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx

MAX_DOWNLOAD = 200 * 1024 * 1024  # 200 MB
MAX_REDIRECTS = 5
_UA = "pdfread/0.5 (+https://github.com/fhan235/pdfread)"
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)

_BLOCKED_NETWORKS = [
    ipaddress.ip_network(c)
    for c in (
        "0.0.0.0/8", "9.0.0.0/8", "10.0.0.0/8", "11.0.0.0/8",
        "21.0.0.0/8", "30.0.0.0/8",
        "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
        "172.16.0.0/12", "192.168.0.0/16",
        "192.0.0.0/24", "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
        "224.0.0.0/4", "240.0.0.0/4",
        "::1/128", "fc00::/7", "fe80::/10",
    )
]

_ARXIV = re.compile(
    r"^(https?://(?:www\.)?arxiv\.org)/(abs|html|pdf)/([0-9.]+[0-9A-Za-z-]*)/?$",
    re.I,
)


class UrlRejected(ValueError):
    """URL 未通过安全校验。"""


def _blocked_ip(ip) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _blocked_ip(mapped)
    return any(ip in net for net in _BLOCKED_NETWORKS)


def validate_url(url: str, *, resolve: bool = True) -> str:
    """校验 URL 合法性并拦截内网目标, 返回规范化后的 URL。"""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UrlRejected("仅支持 http/https 链接")
    if parts.username or parts.password:
        raise UrlRejected("链接中不允许包含用户名或密码")
    host = parts.hostname
    if not host:
        raise UrlRejected("链接缺少主机名")

    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        ips = []
        if resolve:
            port = parts.port or (443 if parts.scheme == "https" else 80)
            try:
                infos = socket.getaddrinfo(
                    host, port, type=socket.SOCK_STREAM
                )
            except socket.gaierror as exc:
                raise UrlRejected(f"域名无法解析: {host}") from exc
            ips = [ipaddress.ip_address(i[4][0]) for i in infos]

    for ip in ips:
        if _blocked_ip(ip):
            raise UrlRejected("目标地址位于环回/内网/保留网段, 已拦截")
    return parts.geturl()


def normalize_url(url: str) -> str:
    """常见学术站点链接规范化。

    arxiv.org/abs/xxxx 与 /html/xxxx 统一转成 /pdf/xxxx。
    """
    m = _ARXIV.match(url.strip())
    if m and m.group(2).lower() != "pdf":
        return f"{m.group(1)}/pdf/{m.group(3)}"
    return url.strip()


def _open_stream(client: httpx.Client, url: str) -> httpx.Response:
    """发起 GET, 手动跟随重定向(每跳重新做 SSRF 校验)。"""
    for _ in range(MAX_REDIRECTS + 1):
        resp = client.send(
            client.build_request("GET", url, headers={"User-Agent": _UA}),
            stream=True,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location", "")
            resp.close()
            if not loc:
                raise UrlRejected("重定向缺少目标地址")
            url = validate_url(urljoin(url, loc))
            continue
        if resp.status_code >= 400:
            resp.close()
            raise UrlRejected(f"下载失败: HTTP {resp.status_code}")
        return resp
    raise UrlRejected("重定向次数过多")


def _suggest_name(url: str) -> str:
    parts = urlsplit(url)
    base = parts.path.rstrip("/").rsplit("/", 1)[-1]
    if base.lower().endswith(".pdf"):
        return base
    if base:
        return f"{base}.pdf"
    return f"{parts.hostname or 'document'}.pdf"


def fetch_document(
    raw_url: str,
    dest_dir: Path,
    kind: str = "auto",
    max_size: int = MAX_DOWNLOAD,
    progress=None,
) -> tuple[Path, str]:
    """把 URL 变成本地 PDF 文件, 返回 (路径, 建议文件名)。

    kind: auto(按内容识别) | pdf | html。
    HTML 内容由系统浏览器 headless 打印为 PDF。
    """
    url = validate_url(normalize_url(raw_url))
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    fd, tmpname = tempfile.mkstemp(prefix="dl-", suffix=".part", dir=dest_dir)
    tmp = Path(tmpname)
    is_pdf = False
    final_url = url

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = _open_stream(client, url)
            final_url = getattr(resp, "url", None) and str(resp.url) or url
            try:
                ctype = resp.headers.get("content-type", "").lower()
                it = resp.iter_bytes(1 << 16)
                sniff = next(it, b"")
                is_pdf = (
                    kind == "pdf"
                    or (kind == "auto"
                        and (sniff.lstrip()[:4] == b"%PDF"
                             or "application/pdf" in ctype))
                ) and kind != "html"

                if is_pdf:
                    if not (sniff.lstrip()[:4] == b"%PDF"
                            or "application/pdf" in ctype):
                        raise UrlRejected("目标内容不是 PDF")
                    total = None
                    try:
                        cl = resp.headers.get("content-length", "")
                        total = int(cl) if cl else None
                    except ValueError:
                        total = None
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(sniff)
                        size = len(sniff)
                        if progress:
                            progress("download", size, total)
                        for chunk in it:
                            size += len(chunk)
                            if size > max_size:
                                raise UrlRejected("文件超过 200MB 上限")
                            fh.write(chunk)
                            if progress:
                                progress("download", size, total)
            finally:
                resp.close()

        if not is_pdf:
            os.close(fd)
            tmp.unlink(missing_ok=True)
            if progress:
                progress("convert", 0, None)
            from .browser import html_to_pdf

            html_to_pdf(final_url, tmp)

        with tmp.open("rb") as fh:
            if fh.read(4) != b"%PDF":
                raise UrlRejected("获取的内容不是有效的 PDF")

        final = dest_dir / f"{uuid4().hex}.pdf"
        tmp.replace(final)
        return final, _suggest_name(final_url)

    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        tmp.unlink(missing_ok=True)
        raise
