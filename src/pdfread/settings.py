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
import threading
import tempfile
from pathlib import Path

from .paths import cache_dir, config_dir

_LOCK = threading.RLock()


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    p = config_path()
    # 首次读取即迁移到持久目录，保留旧文件以便回退。
    if not p.is_file() and not os.environ.get("PDFREAD_CONFIG_DIR"):
        p = cache_dir() / "config.json"
    if not p.is_file():
        return {}
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        for field in ("keys", "profiles"):
            if field in data and not isinstance(data[field], dict):
                data[field] = {}
        if p != config_path():
            with _LOCK:
                if not config_path().exists():
                    save(data)
        return data
    except Exception:
        return {}


def save(data: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        fd, name = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=p.parent)
        tmp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            tmp.replace(p)
        finally:
            tmp.unlink(missing_ok=True)


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
    with _LOCK:
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
    update_translation(name)


def update_translation(provider: str, model=None, base_url=None, key=None) -> None:
    from .translate import PROVIDERS

    with _LOCK:
        data = load()
        data["provider"] = provider
        profile = data.setdefault("profiles", {}).setdefault(provider, {})
        for name, value in (("model", model), ("base_url", base_url)):
            if value is not None:
                profile[name] = value
        if key is not None:
            env = PROVIDERS[provider]["key_env"]
            keys = data.setdefault("keys", {})
            if key:
                keys[env] = key
            else:
                keys.pop(env, None)
        save(data)


def profile(provider: str) -> dict:
    value = load().get("profiles", {}).get(provider, {})
    if not isinstance(value, dict):
        return {}
    return {key: val for key, val in value.items() if key in {"model", "base_url"} and isinstance(val, str)}


def translation_config(provider=None, model=None, base_url=None, **kwargs):
    from .translate import TransConfig
    chosen = provider or os.environ.get("PDFREAD_PROVIDER") or get_provider() or "deepseek"
    saved = profile(chosen)
    return TransConfig(provider=chosen,
                       model=model if model is not None else os.environ.get("PDFREAD_MODEL", saved.get("model", "")),
                       base_url=base_url if base_url is not None else os.environ.get("PDFREAD_BASE_URL", saved.get("base_url", "")),
                       **kwargs)


# ---------- 论文解析与打开历史 ----------

PARSE_DEFAULTS = {
    "paper_mode": "auto", "translate_figtext": False,
    "translate_caption": True, "skip_refs": True,
    "skip_tables": True, "mask_math": True, "fold_formula": True,
}
HISTORY_MAX = 20


def get_parse() -> dict:
    saved = load().get("parse", {})
    return {**PARSE_DEFAULTS, **({k: v for k, v in saved.items() if k in PARSE_DEFAULTS} if isinstance(saved, dict) else {})}


def set_parse(values: dict) -> dict:
    with _LOCK:
        data = load()
        current = data.get("parse", {})
        current = dict(current) if isinstance(current, dict) else {}
        current.update({k: v for k, v in (values or {}).items() if k in PARSE_DEFAULTS})
        data["parse"] = current
        save(data)
    return get_parse()


def get_history() -> list[dict]:
    value = load().get("history", [])
    return value if isinstance(value, list) else []


def add_history(path: str, name: str = "") -> None:
    with _LOCK:
        items = [x for x in get_history() if isinstance(x, dict) and x.get("path") != str(path)]
        items.insert(0, {"path": str(path), "name": name or os.path.basename(path)})
        data = load()
        data["history"] = items[:HISTORY_MAX]
        save(data)


def remove_history(path: str) -> None:
    with _LOCK:
        data = load()
        data["history"] = [x for x in get_history() if not isinstance(x, dict) or x.get("path") != str(path)]
        save(data)
