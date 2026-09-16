"""翻译引擎: OpenAI 兼容接口 + 并发 + SQLite 缓存。

设计要点:
- 按页打包整页段落一次请求(而不是每段一次), 请求数从 2000+ 降到 ~100。
- 用编号分隔符让模型逐段返回, 再按编号切回去。
- SQLite 缓存以 (文本, 模型, 目标语言) 的 hash 为键, 重开文档不重复付费。
- API Key 从环境变量或本地设置读取，不写入日志。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sqlite3
import threading
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

import httpx


# ---------- 服务预设 ----------

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "silicon": {
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "key_env": "SILICON_API_KEY",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_env": "DASHSCOPE_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "key_env": "",  # 本地无需 key
    },
}

SYSTEM_PROMPT = (
    "你是专业的学术文献翻译。将用户给出的英文段落翻译成简体中文。\n"
    "规则:\n"
    "1. 输入每段以 <<<序号>>> 开头, 输出必须保持完全相同的 <<<序号>>> 标记和段落数量。\n"
    "2. 每个序号对应的译文必须是**连续的一整段, 内部不要换行**。\n"
    "3. 只输出译文, 不要解释、不要加注、不要合并或拆分段落。\n"
    "4. 专业术语准确, 语句通顺, 符合中文科技文献表达习惯。\n"
    "5. 保留数字、单位、专有名词缩写(如 GDP、R&D、STEM)原样。\n"
    "6. 数学公式、代码、变量名保持原样不译。"
)

_SEG = re.compile(r"<<<(\d+)>>>")


def tidy(text: str) -> str:
    """压平译文中的换行与多余空白, 保证前端呈现为连续段落。"""
    text = text.replace("\r", "")
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"[ \t\u3000]{2,}", " ", text)
    return text.strip()


# ---------- 配置 ----------

@dataclass
class TransConfig:
    provider: str = "deepseek"
    model: str = ""
    base_url: str = ""
    concurrency: int = 8
    max_chars: int = 2600      # 单次请求最大字符数
    timeout: float = 120.0

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise ValueError("未知翻译服务")
        if not 1 <= self.concurrency <= 32:
            raise ValueError("并发数必须在 1–32 之间")
        if self.max_chars < 100 or self.timeout <= 0:
            raise ValueError("字符预算至少为 100，超时必须大于 0")
        if self.base_url:
            url = urlsplit(self.base_url)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("API 地址必须是有效的 HTTP(S) 地址，不能包含密钥、查询参数或片段")

    def resolve(self) -> tuple[str, str, str]:
        """返回 (base_url, model, api_key)。

        密钥来源: 环境变量优先, 其次本地配置文件(界面中填写)。
        """
        from .settings import get_key

        preset = PROVIDERS.get(self.provider)
        if preset is None:
            raise ValueError(f"未知服务: {self.provider}")
        base_url = (self.base_url or preset["base_url"]).rstrip("/")
        model = self.model or preset["model"]
        key_env = preset["key_env"]
        if not key_env:
            return base_url, model, "local"
        api_key = get_key(key_env)
        if not api_key:
            raise RuntimeError(
                f"尚未配置 {key_env}。可在界面右上角「设置」中填写, "
                f"或设置环境变量 {key_env}"
            )
        return base_url, model, api_key


# ---------- 缓存 ----------

class Cache:
    """线程安全的 SQLite 翻译缓存。"""

    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS t ("
            "k TEXT PRIMARY KEY, v TEXT NOT NULL)"
        )
        self._conn.commit()

    @staticmethod
    def key(text: str, model: str, lang: str) -> str:
        raw = f"{model}\x00{lang}\x00{text}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def get_many(self, keys: list[str]) -> dict[str, str]:
        if not keys:
            return {}
        out: dict[str, str] = {}
        with self._lock:
            for i in range(0, len(keys), 400):
                chunk = keys[i : i + 400]
                ph = ",".join("?" * len(chunk))
                cur = self._conn.execute(
                    f"SELECT k, v FROM t WHERE k IN ({ph})", chunk
                )
                out.update(dict(cur.fetchall()))
        return out

    def put_many(self, items: list[tuple[str, str]]) -> None:
        if not items:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO t (k, v) VALUES (?, ?)", items
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ---------- 翻译器 ----------

class Translator:
    def __init__(self, cfg: TransConfig, cache: Cache):
        self.cfg = replace(cfg)
        self.cache = cache
        self.base_url, self.model, self._key = cfg.resolve()
        self.cache_model = "\x00".join((self.base_url, self.model, hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(), "v2"))
        self._sem = asyncio.Semaphore(cfg.concurrency)

    def _pack(self, texts: list[str]) -> list[list[int]]:
        """把段落按字符预算打包成若干批, 返回下标分组。"""
        groups: list[list[int]] = []
        cur: list[int] = []
        size = 0
        for i, t in enumerate(texts):
            n = len(t)
            if cur and size + n > self.cfg.max_chars:
                groups.append(cur)
                cur, size = [], 0
            cur.append(i)
            size += n
        if cur:
            groups.append(cur)
        return groups

    async def _call(self, client: httpx.AsyncClient, payload: str) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": payload},
            ],
            "temperature": 0.2,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self._key and self._key != "local":
            headers["Authorization"] = f"Bearer {self._key}"

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers=headers,
                    timeout=self.cfg.timeout,
                )
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                data = r.json()
                choice = data["choices"][0]
                if choice.get("finish_reason") in {"length", "content_filter"}:
                    raise ValueError("模型输出被截断或过滤，请减少每次翻译内容")
                content = choice["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("翻译服务返回空内容")
                return content
            except httpx.HTTPStatusError as e:
                # 认证与参数错误不能靠重试解决，也不把响应正文/密钥带到界面。
                raise RuntimeError(f"翻译服务返回 HTTP {e.response.status_code}，请检查密钥、模型和地址") from None
            except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"翻译请求失败（{type(last_err).__name__}），请检查网络或服务状态")

    @staticmethod
    def _unpack(reply: str, n: int) -> list[str]:
        """按 <<<i>>> 标记切分模型回复。"""
        out = [""] * n
        marks = list(_SEG.finditer(reply))
        if not marks:
            # 模型没按格式返回, 整体作为第一段
            if n == 1:
                return [tidy(reply)]
            return out
        ids = [int(m.group(1)) for m in marks]
        if len(set(ids)) != len(ids) or any(i < 0 or i >= n for i in ids):
            return out
        for j, m in enumerate(marks):
            i = int(m.group(1))
            end = marks[j + 1].start() if j + 1 < len(marks) else len(reply)
            if 0 <= i < n:
                out[i] = tidy(reply[m.end() : end])
        return out

    async def translate(self, texts: list[str], lang: str = "zh") -> list[str]:
        if lang != "zh":
            raise ValueError("当前仅支持翻译为简体中文")
        chunks, owners = [], []
        for i, text in enumerate(texts):
            # 尽量在空白处切分，超长无空白片段也受硬上限约束。
            while len(text) > self.cfg.max_chars:
                cut = text.rfind(" ", self.cfg.max_chars // 2, self.cfg.max_chars + 1)
                cut = cut + 1 if cut >= 0 else self.cfg.max_chars
                chunks.append(text[:cut])
                owners.append(i)
                text = text[cut:]
            chunks.append(text)
            owners.append(i)
        translated = await self._translate_short(chunks, lang)
        result = [[] for _ in texts]
        for i, value in zip(owners, translated):
            result[i].append(value)
        return [next((v for v in parts if v.startswith("[翻译失败]")), " ".join(parts)) for parts in result]

    async def _translate_short(self, texts: list[str], lang: str) -> list[str]:
        """翻译一组段落, 返回等长译文列表。命中缓存的不发请求。"""
        if not texts:
            return []

        keys = [Cache.key(t, self.cache_model, lang) for t in texts]
        cached = self.cache.get_many(keys)
        result: list[str] = [cached.get(k, "") for k in keys]

        todo = [i for i, v in enumerate(result) if not v]
        if not todo:
            return result

        groups = self._pack([texts[i] for i in todo])

        async with httpx.AsyncClient() as client:

            async def run(group: list[int]) -> None:
                real = [todo[g] for g in group]
                payload = "\n\n".join(
                    f"<<<{j}>>>\n{texts[i]}" for j, i in enumerate(real)
                )
                async with self._sem:
                    try:
                        reply = await self._call(client, payload)
                    except Exception as e:
                        for i in real:
                            result[i] = f"[翻译失败] {e}"
                        return
                parts = self._unpack(reply, len(real))
                # 格式不完整时仅重试缺失段落，禁止把原文伪装成译文。
                for j, i in enumerate(real):
                    if not parts[j]:
                        try:
                            async with self._sem:
                                retry = await self._call(client, f"<<<0>>>\n{texts[i]}")
                            parts[j] = self._unpack(retry, 1)[0]
                        except Exception:
                            pass
                fresh: list[tuple[str, str]] = []
                for j, i in enumerate(real):
                    val = parts[j] or "[翻译失败] 模型返回的段落格式不完整，请重试"
                    result[i] = val
                    if parts[j]:
                        fresh.append((keys[i], val))
                self.cache.put_many(fresh)

            await asyncio.gather(*(run(g) for g in groups))

        return result
