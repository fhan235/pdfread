"""跨平台路径解析。

确保在以下场景都能正常工作:
- 源码目录直接运行
- pip 安装后运行(安装目录只读)
- PyInstaller 打包成 exe / app 后运行
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "pdfread"


def static_dir() -> Path:
    """前端静态资源目录。"""
    # PyInstaller 打包后资源被解压到 _MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        for p in (Path(meipass) / APP_NAME / "static", Path(meipass) / "static"):
            if p.is_dir():
                return p

    try:
        from importlib.resources import files

        p = Path(str(files(APP_NAME) / "static"))
        if p.is_dir():
            return p
    except Exception:
        pass

    return Path(__file__).resolve().parent / "static"


def cache_dir() -> Path:
    """用户级缓存目录(不写入安装目录)。可用 PDFREAD_CACHE_DIR 覆盖。"""
    env = os.environ.get("PDFREAD_CACHE_DIR")
    if env:
        p = Path(env).expanduser()
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        p = Path(base) / APP_NAME if base else Path.home() / ".pdfread"
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Caches" / APP_NAME
    else:
        base = os.environ.get("XDG_CACHE_HOME")
        p = (Path(base).expanduser() if base else Path.home() / ".cache") / APP_NAME

    p.mkdir(parents=True, exist_ok=True)
    return p


def default_cache_db() -> Path:
    """默认翻译缓存数据库路径。"""
    return cache_dir() / "translations.db"


def config_dir() -> Path:
    """持久设置与可清理缓存分开存放。"""
    override = os.environ.get("PDFREAD_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_NAME


def uploads_dir() -> Path:
    """界面上传文件的存放目录。"""
    p = cache_dir() / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _env_roots() -> list[Path]:
    env = os.environ.get("PDFREAD_ROOTS", "")
    out = []
    for part in env.split(os.pathsep):
        part = part.strip()
        if part:
            p = Path(part).expanduser()
            if p.is_dir():
                out.append(p.resolve())
    return out


def default_roots() -> list[Path]:
    """命令行模式默认允许访问的目录。"""
    return _env_roots() or [Path.cwd().resolve()]


def home_roots() -> list[Path]:
    """桌面应用模式默认允许访问的目录: 主目录下的常用位置。"""
    roots = _env_roots()
    if roots:
        roots.append(uploads_dir())
        return roots

    home = Path.home()
    names = [
        "Desktop", "桌面",
        "Downloads", "下载",
        "Documents", "文档",
        "Papers", "papers",
    ]
    roots = [(home / n).resolve() for n in names if (home / n).is_dir()]
    roots.append(uploads_dir())
    return roots or [home.resolve()]
