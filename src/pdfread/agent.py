"""Agent 工具集: 让问答模型能自主查阅 GitHub 仓库与网页。

安全模型(与 urlfetch 的"用户主动输入 URL"不同, 这里 URL 由模型生成):
- L1 结构化工具(github_*): 域名硬编码, URL 由本模块拼接, 模型只能提供
  受正则约束的 repo/path/ref/query 字段 —— 模型没有任何渠道把文档正文
  塞进 URL, 因此提示注入无法借此外泄数据, 可免确认执行。
- L2 自由抓取(fetch_url): 模型可指定任意地址, 必须经用户确认后才执行,
  并复用 urlfetch 的 SSRF 校验(内网拦截 + 逐跳复验)。

额外约束:
- TOOLS 定义为模块级常量且顺序固定, 保证每轮请求字节级一致以命中
  DeepSeek 的 prompt cache(tools 数组每次都算输入 token)。
- 所有工具失败都返回文本而非抛异常: 让模型看到错误并自我纠正,
  而不是直接中断整个会话。
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import quote, urlsplit

import httpx

from .urlfetch import UrlRejected, validate_url

# ---------- 限额 ----------

MAX_FILE_BYTES = 512 * 1024     # 单文件原始大小上限
MAX_RESULT_CHARS = 24000        # 回灌给模型的单次工具结果上限
MAX_TREE_ENTRIES = 800          # 列目录条目上限
MAX_PAGE_CHARS = 12000          # 网页正文上限

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=15.0, pool=10.0)
_UA = "pdfread-agent/0.5 (+https://github.com/fhan235/pdfread)"

# ---------- 参数校验 ----------

_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}/[A-Za-z0-9._-]{1,120}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")

# 文本文件后缀白名单: 其余(图片/模型权重/压缩包)直接拒绝, 避免把二进制塞给模型
_TEXT_EXT = {
    "", ".txt", ".md", ".rst", ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts",
    ".tsx", ".jsx", ".c", ".h", ".cc", ".cpp", ".hpp", ".cu", ".cuh", ".java",
    ".go", ".rs", ".rb", ".php", ".cs", ".swift", ".kt", ".scala", ".m",
    ".mm", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".lua", ".pl", ".r",
    ".jl", ".sql", ".html", ".htm", ".css", ".scss", ".less", ".vue",
    ".svelte", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".proto", ".graphql", ".tex", ".bib", ".gradle", ".cmake",
    ".dockerfile", ".gitignore", ".env", ".lock", ".mod", ".sum", ".ipynb",
}


class ToolError(Exception):
    """工具参数非法或执行失败(消息会回灌给模型, 不含敏感信息)。"""


def _check_repo(repo: str) -> str:
    repo = str(repo or "").strip().strip("/")
    # 容错: 模型常直接给出完整 URL
    if "github.com" in repo:
        m = re.search(r"github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)", repo)
        if m:
            repo = m.group(1)
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not _REPO_RE.match(repo):
        raise ToolError("repo 格式应为 owner/name, 例如 pytorch/pytorch")
    return repo


def _check_path(path: str, *, allow_empty: bool = False) -> str:
    path = str(path or "").strip().lstrip("/")
    if not path:
        if allow_empty:
            return ""
        raise ToolError("path 不能为空")
    if len(path) > 400:
        raise ToolError("path 过长")
    # 阻断路径穿越与协议走私
    if ".." in path.split("/") or "\\" in path or "://" in path:
        raise ToolError("path 非法")
    return path


def _check_ref(ref: str) -> str:
    ref = str(ref or "").strip().strip("/")
    if not ref:
        return ""
    if not _REF_RE.match(ref) or ".." in ref:
        raise ToolError("ref 非法")
    return ref


def _ext(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    if "." not in base:
        return ""
    return "." + base.rsplit(".", 1)[-1].lower()


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[……内容超过 {limit} 字符已截断……]"


# ---------- GitHub 访问 ----------

def _gh_headers() -> dict:
    """GitHub API 请求头; 有 token 时限流 60/h -> 5000/h。"""
    h = {"User-Agent": _UA, "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _gh_error(status: int) -> ToolError:
    if status == 404:
        return ToolError("目标不存在(仓库私有、路径拼错或分支名不对)")
    if status == 403:
        return ToolError(
            "GitHub 限流或需要认证(未配置 GITHUB_TOKEN 时为 60 次/小时)。"
            "可稍后再试, 或改用已获取到的信息作答")
    if status == 401:
        return ToolError("GITHUB_TOKEN 无效")
    return ToolError(f"GitHub 返回 HTTP {status}")


async def _gh_get(client: httpx.AsyncClient, url: str,
                  accept: str | None = None) -> httpx.Response:
    headers = _gh_headers()
    if accept:
        headers["Accept"] = accept
    r = await client.get(url, headers=headers, timeout=_TIMEOUT,
                         follow_redirects=True)
    # 跟随重定向后复验落点域名, 防 repo 改名等场景跳出白名单
    host = (urlsplit(str(r.url)).hostname or "").lower()
    if host not in ("api.github.com", "raw.githubusercontent.com",
                    "codeload.github.com"):
        raise ToolError("GitHub 响应重定向到非预期域名, 已拦截")
    if r.status_code != 200:
        raise _gh_error(r.status_code)
    return r


async def github_list_tree(client: httpx.AsyncClient, *, repo: str,
                           ref: str = "", path: str = "") -> str:
    """列出仓库文件树(递归), 可按子目录前缀过滤。"""
    repo = _check_repo(repo)
    ref = _check_ref(ref)
    path = _check_path(path, allow_empty=True)

    if not ref:
        r = await _gh_get(client, f"https://api.github.com/repos/{repo}")
        ref = (r.json() or {}).get("default_branch") or "main"

    r = await _gh_get(
        client,
        f"https://api.github.com/repos/{repo}/git/trees/{quote(ref, safe='')}"
        "?recursive=1")
    data = r.json() or {}
    entries = [e for e in (data.get("tree") or []) if e.get("type") == "blob"]
    if path:
        pre = path.rstrip("/") + "/"
        entries = [e for e in entries
                   if e.get("path", "").startswith(pre) or e.get("path") == path]
    if not entries:
        raise ToolError(f"{repo}@{ref} 下没有匹配 '{path}' 的文件")

    truncated = data.get("truncated") or len(entries) > MAX_TREE_ENTRIES
    lines = [f"{e['path']}  ({e.get('size', 0)}B)"
             for e in entries[:MAX_TREE_ENTRIES]]
    head = f"仓库 {repo}@{ref} 共 {len(entries)} 个文件"
    if truncated:
        head += f"(仅列出前 {len(lines)} 个)"
    return _clip(head + ":\n" + "\n".join(lines), MAX_RESULT_CHARS)


async def github_read_file(client: httpx.AsyncClient, *, repo: str,
                           path: str, ref: str = "") -> str:
    """读取仓库中单个文本文件, 返回带行号的内容。

    优先走 raw.githubusercontent.com: 该 CDN 不消耗 API 配额,
    未配置 GITHUB_TOKEN 时(60 次/小时)可把配额全留给列目录。
    """
    repo = _check_repo(repo)
    path = _check_path(path)
    ref = _check_ref(ref)
    if _ext(path) not in _TEXT_EXT:
        raise ToolError(f"{path} 不是可读文本文件(仅支持源码/配置/文档类后缀)")

    raw: bytes | None = None
    used_ref = ref
    for cand in ([ref] if ref else ["main", "master"]):
        url = (f"https://raw.githubusercontent.com/{repo}/"
               f"{quote(cand, safe='')}/{quote(path, safe='/')}")
        try:
            r = await client.get(url, headers={"User-Agent": _UA},
                                 timeout=_TIMEOUT, follow_redirects=False)
        except httpx.HTTPError:
            continue
        if r.status_code == 200:
            raw, used_ref = r.content, cand
            break

    if raw is None:
        # CDN 没命中(默认分支既非 main 也非 master, 或文件确实不存在)时回退 API
        url = (f"https://api.github.com/repos/{repo}/contents/"
               f"{quote(path, safe='/')}")
        if ref:
            url += f"?ref={quote(ref, safe='')}"
        r = await _gh_get(client, url, accept="application/vnd.github.raw")
        raw = r.content

    raw = raw[:MAX_FILE_BYTES]
    if b"\x00" in raw[:4096]:
        raise ToolError(f"{path} 是二进制文件, 无法阅读")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")

    lines = text.splitlines()
    body = "\n".join(f"{i:>5}| {ln}" for i, ln in enumerate(lines, 1))
    head = (f"{repo}/{path}" + (f"@{used_ref}" if used_ref else "")
            + f" ({len(lines)} 行)")
    return _clip(head + ":\n" + body, MAX_RESULT_CHARS)


async def github_search_code(client: httpx.AsyncClient, *, repo: str,
                             query: str) -> str:
    """在指定仓库内搜索代码(需要 GITHUB_TOKEN)。"""
    repo = _check_repo(repo)
    query = str(query or "").strip()
    if not query or len(query) > 200:
        raise ToolError("query 为空或过长")
    if not os.environ.get("GITHUB_TOKEN", "").strip():
        raise ToolError(
            "代码搜索需要配置 GITHUB_TOKEN 环境变量。"
            "请改用 github_list_tree 浏览文件树后再读取具体文件")

    q = quote(f"{query} repo:{repo}", safe="")
    r = await _gh_get(client,
                      f"https://api.github.com/search/code?q={q}&per_page=20")
    items = (r.json() or {}).get("items") or []
    if not items:
        raise ToolError(f"在 {repo} 中没有搜到 '{query}'")
    lines = [f"- {it.get('path')}" for it in items]
    return _clip(f"在 {repo} 中搜索 '{query}' 命中 {len(items)} 个文件:\n"
                 + "\n".join(lines), MAX_RESULT_CHARS)


# ---------- 自由网页抓取(L2, 需确认) ----------

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\n{3,}")


async def fetch_url(client: httpx.AsyncClient, *, url: str) -> str:
    """抓取任意网页并提取正文(已过用户确认 + SSRF 校验)。"""
    url = str(url or "").strip()
    if len(url) > 2000:
        raise ToolError("URL 过长")
    try:
        url = validate_url(url)
    except UrlRejected as e:
        raise ToolError(f"链接被安全策略拒绝: {e}") from None

    try:
        r = await client.get(url, headers={"User-Agent": _UA},
                             timeout=_TIMEOUT, follow_redirects=False)
    except httpx.HTTPError:
        raise ToolError("网络请求失败") from None
    # 手动处理重定向: 每一跳都要重新过 SSRF 校验
    hops = 0
    while r.status_code in (301, 302, 303, 307, 308) and hops < 3:
        loc = r.headers.get("location", "")
        if not loc:
            raise ToolError("重定向缺少目标地址")
        try:
            url = validate_url(httpx.URL(url).join(loc).__str__())
        except UrlRejected as e:
            raise ToolError(f"重定向目标被拒绝: {e}") from None
        r = await client.get(url, headers={"User-Agent": _UA},
                             timeout=_TIMEOUT, follow_redirects=False)
        hops += 1
    if r.status_code >= 400:
        raise ToolError(f"下载失败: HTTP {r.status_code}")

    ctype = r.headers.get("content-type", "").lower()
    if "application/pdf" in ctype:
        raise ToolError("该链接是 PDF, 请让用户用「从 URL 打开」功能导入阅读")
    text = r.text[:MAX_PAGE_CHARS * 6]
    if "html" in ctype or text.lstrip()[:1] == "<":
        text = _TAG.sub(" ", text)
        text = _ANY_TAG.sub("\n", text)
    text = _WS.sub("\n\n", "\n".join(
        ln.strip() for ln in text.splitlines())).strip()
    if not text:
        raise ToolError("页面没有可提取的文本")
    return _clip(f"{url} 的正文:\n{text}", MAX_PAGE_CHARS)


# ---------- 工具注册表 ----------

# 顺序与内容必须保持稳定: 每轮请求都会原样发送, 变动会导致 prompt cache 失效
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "github_list_tree",
            "description": (
                "列出 GitHub 仓库的文件树。探索一个陌生仓库时应首先调用它, "
                "据此判断项目结构并挑选要精读的文件。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string",
                             "description": "仓库, 格式 owner/name"},
                    "ref": {"type": "string",
                            "description": "分支/标签/commit, 省略则用默认分支"},
                    "path": {"type": "string",
                             "description": "只列该子目录, 省略则列全仓库"},
                },
                "required": ["repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_read_file",
            "description": (
                "读取 GitHub 仓库中某个文本文件的完整内容(带行号)。"
                "引用代码时请注明文件路径与行号。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string",
                             "description": "仓库, 格式 owner/name"},
                    "path": {"type": "string",
                             "description": "文件路径, 如 src/model.py"},
                    "ref": {"type": "string",
                            "description": "分支/标签/commit, 可省略"},
                },
                "required": ["repo", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_search_code",
            "description": (
                "在指定 GitHub 仓库内按关键字搜索代码。"
                "需要服务端配置 GITHUB_TOKEN, 不可用时改用 github_list_tree。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string",
                             "description": "仓库, 格式 owner/name"},
                    "query": {"type": "string",
                              "description": "搜索关键字, 如 class Encoder"},
                },
                "required": ["repo", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "抓取任意网页正文。仅在 github 工具无法满足时使用; "
                "每次调用都需要用户当场授权, 因此不要频繁调用。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string",
                            "description": "完整的 http/https 链接"},
                },
                "required": ["url"],
            },
        },
    },
]

# 需要用户确认的工具(L2)
NEEDS_CONFIRM = {"fetch_url"}

_IMPL = {
    "github_list_tree": github_list_tree,
    "github_read_file": github_read_file,
    "github_search_code": github_search_code,
    "fetch_url": fetch_url,
}


def tool_names() -> set[str]:
    return set(_IMPL)


def describe_call(name: str, args: dict) -> str:
    """生成给用户看的一行调用摘要。"""
    if name == "github_list_tree":
        p = args.get("path") or ""
        return f"列出 {args.get('repo', '?')} 的文件树" + (f" ({p})" if p else "")
    if name == "github_read_file":
        return f"读取 {args.get('repo', '?')}/{args.get('path', '?')}"
    if name == "github_search_code":
        return f"在 {args.get('repo', '?')} 中搜索 \"{args.get('query', '')}\""
    if name == "fetch_url":
        return f"抓取网页 {args.get('url', '?')}"
    return name


def exfil_warnings(name: str, args: dict) -> list[str]:
    """检查自由抓取参数中的数据外泄特征, 供确认弹窗标红。"""
    if name != "fetch_url":
        return []
    url = str(args.get("url", ""))
    out = []
    parts = urlsplit(url)
    qs = (parts.query or "") + (parts.fragment or "")
    if len(qs) > 120:
        out.append(f"链接的查询参数异常长({len(qs)} 字符), 可能夹带了文档内容")
    if re.search(r"%[0-9A-Fa-f]{2}%[0-9A-Fa-f]{2}%[0-9A-Fa-f]{2}", qs):
        out.append("查询参数中有大量编码文本, 疑似在外传正文")
    host = (parts.hostname or "").lower()
    if host and not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", host):
        out.append("域名形态可疑")
    return out


def parse_args(raw: str) -> dict:
    """解析模型给出的 arguments(容忍空串与代码块包裹)。"""
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
    try:
        data = json.loads(raw)
    except ValueError:
        raise ToolError("参数不是合法 JSON, 请重新生成") from None
    if not isinstance(data, dict):
        raise ToolError("参数必须是 JSON 对象")
    return data


async def run_tool(client: httpx.AsyncClient, name: str, args: dict) -> str:
    """执行工具; 任何失败都转成文本结果回灌给模型。"""
    fn = _IMPL.get(name)
    if fn is None:
        return f"[工具错误] 未知工具 {name}"
    allowed = {"repo", "path", "ref", "query", "url"}
    kwargs = {k: v for k, v in args.items() if k in allowed}
    try:
        return await fn(client, **kwargs)
    except ToolError as e:
        return f"[工具错误] {e}"
    except TypeError:
        return f"[工具错误] {name} 的参数不正确"
    except httpx.HTTPError:
        return "[工具错误] 网络请求失败, 请稍后重试或换一种方式"
    except Exception as e:  # noqa: BLE001 - 兜底, 避免中断整个会话
        return f"[工具错误] {type(e).__name__}"
