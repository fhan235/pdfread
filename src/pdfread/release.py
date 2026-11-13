"""GitHub Release 查询与安装包下载校验。

从 server.py 抽出来的纯逻辑: 版本号比较、平台资产匹配、代理与 SSL 上下文、
Release 信息获取(网页端 302 优先 + API 富化)、下载与 SHA-256 校验。
这里不含任何 HTTP 路由 —— 路由仍在 server.py, 只调用本模块。

安全约定(改动时保持):
- 下载地址由 _UPDATE_REPO + 版本号 + 资产名拼出, 不接受响应里给的任意 URL;
- 必须用同一 Release 的 SHA256SUMS.txt 校验, 缺校验文件则拒绝安装;
- 包大小上限 _UPDATE_MAX, 防超大响应打满磁盘。
（下载与校验的实现在 server.py 的 _download_asset, 因为它要上报进度。）
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import ssl
import sys
import time
from pathlib import Path

import httpx

from .paths import cache_dir

_UPDATE_REPO = "fhan235/pdfread"
_UPDATE_TTL = 6 * 3600          # 检查结果缓存 6 小时
_UPDATE_MAX = 500 * 1024 * 1024  # 安装包上限 500MB
_UPDATE_SUMS = "SHA256SUMS.txt"  # Release 附带的校验文件(CI 生成)
_VER_RE = re.compile(r"[0-9][0-9A-Za-z.\-]{0,40}")
_ASSET_RE = re.compile(r"pdfread-[A-Za-z0-9.\-]+\.(?:zip|dmg|tar\.gz)")
_download_lock = asyncio.Lock()   # 「仅下载」与「一键更新」写同一文件, 串行化


def _ver_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in re.findall(r"\d+", v)[:4])
    except ValueError:
        return (0,)


def _asset_name() -> str:
    """当前平台对应的安装包文件名。"""
    return ("pdfread-windows-x64.zip" if sys.platform == "win32"
            else "pdfread-macos-arm64.dmg" if sys.platform == "darwin"
            else "pdfread-linux-x64.tar.gz")


def _pick_asset(assets: list) -> dict | None:
    """按当前平台挑选安装包。"""
    want = _asset_name()
    for a in assets:
        if isinstance(a, dict) and str(a.get("name", "")).endswith(want):
            return a
    return None


async def _fallback_latest(client) -> dict | None:
    """API 限流时的回退: 经 releases/latest 的 302 跳转拿最新版本号。

    该端点属于网页端, 不占 API 配额。拿不到 Release 说明,
    安装包地址按命名规律拼接。
    """
    try:
        r = await client.get(
            f"https://github.com/{_UPDATE_REPO}/releases/latest",
            follow_redirects=False)
    except Exception:
        return None
    m = re.search(r"/tag/v?([0-9][0-9A-Za-z.\-]*)\s*$",
                  r.headers.get("location", ""))
    if not m:
        return None
    latest = m.group(1)
    name = _asset_name()
    return {
        "latest": latest,
        "notes": "",
        "page_url": f"https://github.com/{_UPDATE_REPO}/releases/tag/v{latest}",
        "asset_name": name,
        "asset_url": f"https://github.com/{_UPDATE_REPO}/releases/download"
                     f"/v{latest}/{name}",
    }


def _update_proxy() -> str | None:
    """更新检查/下载使用的代理。

    优先级: PDFREAD_PROXY 环境变量 > 设置中手动保存的代理 >
    HTTPS_PROXY 等环境变量 > 系统代理(Windows 注册表 / macOS scutil)。

    注意: PAC 自动配置脚本(AutoConfigURL)无法在此解析, 该场景请手动填写代理。
    """
    v = os.environ.get("PDFREAD_PROXY", "").strip()
    if v:
        return v
    from .settings import get_proxy
    saved = get_proxy()
    if saved:
        return saved
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
              "HTTP_PROXY", "http_proxy"):
        v = os.environ.get(k, "").strip()
        if v:
            return v
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server_, _ = winreg.QueryValueEx(key, "ProxyServer")
            winreg.CloseKey(key)
            if enabled and server_:
                # 可能是 "http=h:1;https=h:2" 或统一 "h:port"
                for part in str(server_).split(";"):
                    part = part.strip()
                    if part.startswith("https="):
                        return "http://" + part[6:]
                first = str(server_).split(";")[0].strip()
                if "=" in first:
                    first = first.split("=", 1)[1]
                if first:
                    return "http://" + first
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            import subprocess
            out = subprocess.run(["scutil", "--proxy"], capture_output=True,
                                 text=True, timeout=3).stdout
            en = re.search(r"HTTPSEnable\s*:\s*1", out)
            host = re.search(r"HTTPSProxy\s*:\s*(\S+)", out)
            port = re.search(r"HTTPSPort\s*:\s*(\d+)", out)
            if en and host:
                return f"http://{host.group(1)}:{(port and port.group(1)) or '443'}"
        except Exception:
            pass
    return None


def _update_ssl_context():
    """优先使用操作系统证书库。

    企业代理常做 TLS 审查(根证书装进了系统库), 而 Python 默认用 certifi
    自带证书, 校验必失败。Windows 读注册表根证书, macOS 读系统钥匙串。
    """
    import ssl
    ctx = ssl.create_default_context()
    try:
        if sys.platform == "win32":
            for store in ("ROOT", "CA"):
                for cert in ssl.enum_certificates(store):
                    try:
                        ctx.load_verify_locations(cadata=cert[0])
                    except Exception:
                        continue
        elif sys.platform == "darwin":
            import subprocess
            out = subprocess.run(
                ["security", "find-certificate", "-a", "-p",
                 "/System/Library/Keychains/SystemRootCertificates.keychain"],
                capture_output=True, text=True, timeout=5).stdout
            if out:
                ctx.load_verify_locations(cadata=out)
    except Exception:
        pass
    return ctx


def _update_client(timeout: float = 8.0, **extra):
    import httpx
    proxy = _update_proxy()
    kwargs = {"timeout": timeout, "verify": _update_ssl_context(), **extra}
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


async def fetch_latest() -> dict:
    """查询 GitHub 最新 Release(不带缓存; 缓存由调用方维护)。

    网页端(releases/latest 302 跳转)优先: 不占未认证 API 的限流配额;
    网页端失败时仍尝试 API(两条通道互为备份); 两通道版本号一致才合并。

    注意: 这里刻意不读写模块级缓存 —— 缓存是运行时状态, 留在 server.py
    与路由放在一起, 测试才能通过 patch server 模块来控制它。
    """
    from . import __version__
    data = {"current": __version__, "newer": False, "error": ""}
    try:
        async with _update_client(timeout=8.0) as client:
            fb = await _fallback_latest(client)
            if not fb:
                # 网页端失败, 退到 API(可能 API 通而网页端不通)
                fb = await _api_latest(client)
            elif fb:
                # 网页端成功时, 尽力用 API 富化(更新说明/精确资产),
                # 但两通道版本号必须一致才合并, 防止版本与下载地址混搭
                api = await _api_latest(client)
                if api and api["latest"] == fb["latest"]:
                    fb["notes"] = api["notes"]
                    if api["asset_url"]:
                        fb["asset_name"] = api["asset_name"]
                        fb["asset_url"] = api["asset_url"]
                    fb["fallback"] = False
            if not fb:
                data["error"] = ("无法访问 GitHub，请检查网络/代理"
                                 "（强制代理环境可在设置 → 关于中手动填写代理地址）")
            else:
                data.update(fb)
                data["newer"] = _ver_tuple(fb["latest"]) > _ver_tuple(__version__)
                data["fallback"] = fb.get("fallback", False)
    except Exception as exc:
        data["error"] = (f"网络请求失败: {str(exc)[:150]}"
                         "（强制代理环境可在设置 → 关于中手动填写代理地址）")
    return data


async def _api_latest(client) -> dict | None:
    """经 GitHub API 查询最新 Release(更新说明 + 精确资产)。"""
    try:
        r = await client.get(
            f"https://api.github.com/repos/{_UPDATE_REPO}/releases/latest",
            headers={"User-Agent": "pdfread-updater",
                     "Accept": "application/vnd.github+json"})
    except Exception:
        return None
    if r.status_code != 200:
        return None
    rel = r.json()
    latest = str(rel.get("tag_name", "")).lstrip("v")
    asset = _pick_asset(rel.get("assets") or [])
    return {
        "latest": latest,
        "notes": str(rel.get("body", ""))[:4000],
        "page_url": str(rel.get("html_url", "")),
        "asset_name": asset and asset.get("name") or "",
        "asset_url": asset and asset.get("browser_download_url") or "",
        "fallback": False,
    }


