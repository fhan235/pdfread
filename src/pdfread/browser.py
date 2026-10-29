"""系统浏览器探测与 HTML 转 PDF。

复用系统已安装的 Chrome / Edge, 不引入额外浏览器依赖:
- 应用模式开窗(app.py)
- headless --print-to-pdf 把网页转成 PDF(本模块)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def browser_candidates() -> list[str]:
    """按优先级返回可能的浏览器可执行文件路径。"""
    out: list[str] = []
    if sys.platform == "win32":
        for exe in ("msedge", "chrome"):
            p = shutil.which(exe)
            if p:
                out.append(p)
        for pth in (
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        ):
            if os.path.isfile(pth):
                out.append(pth)
    elif sys.platform == "darwin":
        for pth in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ):
            if os.path.isfile(pth):
                out.append(pth)
    else:
        for exe in (
            "google-chrome", "chromium", "chromium-browser", "microsoft-edge",
        ):
            p = shutil.which(exe)
            if p:
                out.append(p)
    return out


def find_browser() -> str | None:
    cands = browser_candidates()
    return cands[0] if cands else None


def html_to_pdf(url: str, dest: Path, timeout: float = 90.0,
                pinned_ip: str | None = None) -> None:
    """用系统浏览器 headless 模式把网页打印成 PDF。

    没有可用浏览器时抛出 RuntimeError, 调用方应提示用户改用 PDF 链接。

    SSRF 防护: 页面加载后执行的 JS / meta refresh / 子资源请求不经过
    urlfetch 的白名单校验, 因此用 --host-resolver-rules 把目标域名钉到
    已校验的 IP, 其余域名一律解析失败。
    残余风险: 页面内以 IP 字面量直链的内网资源(不经 DNS)无法被该机制拦截,
    转换网页链接时应知晓这一点。
    """
    browser = find_browser()
    if browser is None:
        raise RuntimeError(
            "未找到 Chrome / Edge, 无法把网页转换为 PDF(可直接粘贴 PDF 链接)"
        )

    dest = Path(dest).resolve()
    cmd = [
        browser,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        # 给 JS 渲染留出虚拟时间, 提升动态页面完整度
        "--virtual-time-budget=10000",
    ]
    if pinned_ip:
        from urllib.parse import urlsplit
        host = urlsplit(url).hostname
        if host:
            # 目标域名钉到已校验 IP(顺带消除 DNS rebinding 窗口),
            # 其余所有域名解析失败, 阻断页面级跳转到内网
            cmd.append(
                f"--host-resolver-rules=MAP {host} {pinned_ip}, MAP * ~NOTFOUND"
            )
    cmd += [f"--print-to-pdf={dest}", url]
    try:
        proc = subprocess.run(
            cmd,
            timeout=timeout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("网页转换超时") from exc

    if not dest.is_file() or dest.stat().st_size == 0:
        detail = proc.stderr.decode("utf-8", "replace")[:200]
        raise RuntimeError(f"网页转换失败: {detail or '浏览器未产出文件'}")

    with dest.open("rb") as fh:
        if fh.read(4) != b"%PDF":
            dest.unlink(missing_ok=True)
            raise RuntimeError("网页转换产物不是有效的 PDF")
