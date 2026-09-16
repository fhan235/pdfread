"""本地设置存储。

桌面应用双击启动时没有 shell 环境, 无法依赖 export 设置的环境变量,
因此允许在界面中填写密钥并保存到用户配置目录。

安全约定:
- 配置文件权限设为 0600(仅当前用户可读写)。
- 密钥只在本机存储与使用, 绝不返回给前端(接口只返回是否已配置)。
- 环境变量优先级高于配置文件, 便于 CI / 服务器场景覆盖。
"""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

from .paths import cache_dir

_LOCK = threading.Lock()


def config_path() -> Path:
    return cache_dir() / "config.json"


def load() -> dict:
    p = config_path()
    if not p.is_file():
        return {}
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(data: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with _LOCK:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
        tmp.replace(p)


def get_key(env_name: str) -> str:
    """取密钥: 环境变量优先, 其次本地配置。"""
    if not env_name:
        return ""
    val = os.environ.get(env_name, "").strip()
    if val:
        return val
    return str(load().get("keys", {}).get(env_name, "")).strip()


def set_key(env_name: str, value: str) -> None:
    """写入或清除某个密钥。"""
    if not env_name:
        return
    data = load()
    keys = data.setdefault("keys", {})
    value = (value or "").strip()
    if value:
        keys[env_name] = value
    else:
        keys.pop(env_name, None)
    save(data)


def configured() -> dict[str, bool]:
    """返回各密钥是否已配置(不泄露内容)。"""
    keys = load().get("keys", {})
    out = {}
    for name, val in keys.items():
        out[name] = bool(str(val).strip())
    return out


def get_provider() -> str:
    return str(load().get("provider", "")).strip()


def set_provider(name: str) -> None:
    data = load()
    data["provider"] = name
    save(data)
