"""PDF 双栏对照阅读器 —— 后端服务。

安全说明:
- 服务默认只绑定 127.0.0.1, 不对外暴露。
- API Key 从环境变量或本地设置读取，不返回给前端。
- 文件访问限定在白名单目录内, 防止路径穿越。
- 上传限制扩展名与大小。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import multiprocessing
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    Response,
    StreamingResponse,
)

from .library import Library
from .pdfworker import inspect_pdf, page_words, render_page, render_page_svg
from .paths import cache_dir, config_dir, static_dir, uploads_dir
from .translate import PROVIDERS, Cache, TransConfig, Translator

MAX_UPLOAD = 200 * 1024 * 1024  # 200 MB

_pool = None


def _executor():
    global _pool
    if _pool is None:
        from ._macos import hide_dock_icon

        _pool = ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=hide_dock_icon,
        )
    return _pool


def _close_pool() -> None:
    """关闭进程池并确保 worker 退出。

    shutdown(wait=True) 会等正在执行的任务结束; worker 卡住时
    (大文件解析 / PyMuPDF 在 C 层阻塞) 会无限等待, 退出后进程残留。
    注意: shutdown() 会把 _processes 置 None, 必须先抓进程列表,
    再 shutdown, 最后 terminate, 顺序不能反。
    """
    global _pool
    pool, _pool = _pool, None
    if pool is None:
        return
    procs = list((getattr(pool, "_processes", None) or {}).values())
    pool.shutdown(wait=False, cancel_futures=True)
    for proc in procs:
        try:
            if proc.is_alive():
                proc.terminate()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app):
    yield
    await asyncio.to_thread(_close_pool)
    if STATE["cache"] is not None:
        STATE["cache"].close()
        STATE["cache"] = None
    if STATE["library"] is not None:
        STATE["library"].close()
        STATE["library"] = None


app = FastAPI(title="PDF Bilingual Reader", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])


@app.middleware("http")
async def local_requests(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin and origin != f"{request.url.scheme}://{request.headers.get('host', '')}"
    ):
        return JSONResponse({"detail": "仅允许同源访问"}, status_code=403)
    return await call_next(request)

STATE: dict = {
    "pdf": None,
    "doc": None,
    "pages": [],
    "sizes": [],
    "roots": [],
    "cfg": None,
    "cache": None,
    "library": None,
    "docs": {},
}


# ---------- 工具 ----------

def _roots() -> list[Path]:
    return STATE["roots"] or []


def _within_roots(p: Path) -> bool:
    for r in _roots():
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def _safe_resolve(raw: str) -> Path:
    """校验路径在白名单目录内, 防止路径穿越读取任意文件。"""
    p = Path(raw).expanduser().resolve()
    if not _within_roots(p):
        raise HTTPException(403, "该路径不在允许访问的目录内")
    if not p.is_file() or p.suffix.lower() not in (".pdf", ".epub", ".txt"):
        raise HTTPException(404, "PDF / EPUB 文件不存在")
    return p


def _cleanup_upload(path: Path) -> None:
    """尽力清理上传文件，不让 Windows 上子进程的短暂文件锁掩盖原始错误。"""
    for attempt in range(8):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == 7:
                return
            time.sleep(0.05 * (attempt + 1))


def _register(path: Path, data: dict, name: str | None = None) -> dict:
    # 同一路径重复打开时复用原 id: 前端标签保持稳定, 不产生重复标签
    doc_id = None
    for old_id, old in list(STATE["docs"].items()):
        if old["path"] == path:
            doc_id = old_id
            STATE["docs"].pop(old_id)
            break
    current = {**data, "path": path, "name": name or path.name,
               "id": doc_id or uuid4().hex}
    try:
        if STATE["library"] is not None:
            current["lib"] = STATE["library"].upsert_document(
                str(path), current["name"], data.get("title") or "",
                pages=len(data["pages"]))
            current["progress"] = STATE["library"].get_progress(current["lib"])
    except Exception:
        pass  # 文库登记失败不影响阅读
    STATE["docs"][current["id"]] = current
    while len(STATE["docs"]) > 16:
        STATE["docs"].pop(next(iter(STATE["docs"])))
    STATE.update(pdf=path, doc=current, pages=data["pages"], sizes=data["sizes"])
    try:
        from .settings import add_history
        add_history(str(path), current["name"])
    except Exception:
        pass
    return current


def _inspect_fn(path: Path):
    """按格式选择解析函数(在 PDF 进程池中执行, 崩溃隔离一致)。"""
    suffix = path.suffix.lower()
    if suffix == ".epub":
        from .epub import inspect_epub
        return inspect_epub, True, {}
    if suffix == ".txt":
        from .txt import inspect_txt
        return inspect_txt, True, {}
    from .settings import get_parse
    return inspect_pdf, True, get_parse()


def _load(path: Path, skip_references: bool = True) -> None:
    fn, _, opts = _inspect_fn(path)
    if path.suffix.lower() != ".pdf":   # EPUB/TXT 无解析选项
        data = _executor().submit(fn, str(path)).result()
    else:
        data = _executor().submit(fn, str(path), skip_references, opts).result()
    _register(path, data)


async def _restart_executor() -> None:
    """解析/渲染失败后重建进程池。

    PyMuPDF 打开失败时可能在 C 层残留文件句柄(Python 的 with 管不到,
    已实测确认): Linux 无感, Windows 上文件被锁住导致清理失败。
    让句柄随 worker 进程一起退出是最可靠的释放方式。
    """
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await asyncio.to_thread(pool.shutdown, True, cancel_futures=True)


async def _inspect(path: Path, skip_references: bool) -> dict:
    fn, _, opts = _inspect_fn(path)
    args = (str(path),) if path.suffix.lower() != ".pdf" else (str(path), skip_references, opts)
    try:
        return await asyncio.get_running_loop().run_in_executor(_executor(), fn, *args)
    except Exception:
        await _restart_executor()
        raise HTTPException(400, "文件无法解析，请确认文件完整、未加密且包含内容") from None


def _document(doc: str = "") -> dict:
    current = STATE["docs"].get(doc) if doc else STATE["doc"]
    if current is None:
        raise HTTPException(410 if doc else 400, "文档已过期，请重新打开" if doc else "尚未加载 PDF")
    return current


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------- 路由 ----------

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((static_dir() / "index.html").read_text("utf-8"))


@app.get("/static/{name:path}")
def static_files(name: str) -> FileResponse:
    """前端静态资源(css/js)。解析后必须仍在 static 目录内, 防路径穿越。"""
    base = static_dir().resolve()
    p = (base / name).resolve()
    if not p.is_file() or (p != base and base not in p.parents):
        raise HTTPException(404, "资源不存在")
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


@app.get("/icon.png")
def app_icon() -> FileResponse:
    """应用图标(网页 favicon / 浏览器应用模式任务栏图标)。"""
    from .paths import asset_png
    p = asset_png()
    if not p:
        raise HTTPException(404, "图标不存在")
    return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/docs")
def list_docs() -> dict:
    """列出当前打开的所有文档, 供前端恢复标签栏。"""
    return {
        "active": STATE["doc"]["id"] if STATE["doc"] else "",
        "docs": [
            {"id": d["id"], "name": d["name"], "title": d.get("title") or "",
             "pages": len(d["pages"])}
            for d in STATE["docs"].values()
        ],
    }


@app.post("/api/docs/close")
async def close_doc(payload: dict) -> dict:
    """关闭一个文档标签; 若关闭的是当前文档则切到最近一个。"""
    doc_id = str(payload.get("id", ""))
    existed = STATE["docs"].pop(doc_id, None) is not None
    if STATE["doc"] and STATE["doc"]["id"] == doc_id:
        STATE.update(pdf=None, doc=None, pages=[], sizes=[])
        if STATE["docs"]:
            d = STATE["docs"][next(reversed(STATE["docs"]))]
            STATE.update(pdf=d["path"], doc=d, pages=d["pages"], sizes=d["sizes"])
    return {"ok": True, "existed": existed, **list_docs()}


@app.get("/api/info")
async def get_info(doc: str = "") -> dict:
    return info(doc)


def info(doc: str = "") -> dict:
    model, ready, warn = "", False, ""
    cfg: TransConfig | None = STATE["cfg"]
    if cfg is not None:
        try:
            _, model, _ = cfg.resolve()
            ready = True
        except Exception as e:
            warn = str(e)
            model = PROVIDERS.get(cfg.provider, {}).get("model", "")

    base = {
        "provider": cfg.provider if cfg else "",
        "model": model,
        "ready": ready,
        "warn": warn,
        "providers": sorted(PROVIDERS),
        "roots": [str(r) for r in _roots()],
    }

    if not doc and STATE["doc"] is None:
        return {"loaded": False, **base}

    current = _document(doc)
    return {
        "loaded": True,
        "name": current["name"],
        "path": str(current["path"]),
        "pages": len(current["pages"]),
        "chars": sum(len(p.text) for pg in current["pages"] for p in pg),
        "paras": sum(len(pg) for pg in current["pages"]),
        "sizes": current["sizes"],
        "ver": current["id"],
        "doc": current["id"],
        "title": current["title"],
        "lib": current.get("lib"),
        "progress": current.get("progress", 0),
        "toc": current.get("toc") or [],
        "paper_mode": bool(current.get("paper_mode")),
        "fmt": current.get("fmt", "pdf"),
        **base,
    }


@app.post("/api/roots")
async def add_root(payload: dict) -> dict:
    """把用户指定的目录加入可访问白名单(用户主动授权)。"""
    raw = str(payload.get("path", "")).strip()
    if not raw:
        raise HTTPException(400, "路径为空")
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(404, f"目录不存在: {p}")
    if not _within_roots(p):
        STATE["roots"].append(p)
    return {"roots": [str(r) for r in _roots()], "dir": str(p)}


@app.get("/api/browse")
def browse(dir: str = "") -> dict:
    """列出白名单目录内的 PDF 与子目录, 供界面内选择文件。"""
    roots = _roots()
    if not dir:
        if len(roots) == 1:
            target = roots[0]
        else:
            return {
                "cwd": "",
                "parent": None,
                "dirs": [{"name": str(r), "path": str(r)} for r in roots],
                "files": [],
            }
    else:
        target = Path(dir).expanduser().resolve()
        if not _within_roots(target) or not target.is_dir():
            raise HTTPException(403, "该目录不在允许访问的范围内")

    dirs, files = [], []
    try:
        for e in sorted(target.iterdir(), key=lambda x: x.name.lower()):
            if e.name.startswith(".") or not _within_roots(e.resolve()):
                continue
            if e.is_dir():
                dirs.append({"name": e.name, "path": str(e)})
            elif e.suffix.lower() in (".pdf", ".epub", ".txt"):
                try:
                    size = e.stat().st_size
                except OSError:
                    size = 0
                files.append({"name": e.name, "path": str(e), "size": size})
    except PermissionError:
        raise HTTPException(403, "无权限读取该目录")

    parent = str(target.parent) if _within_roots(target.parent) else None
    return {"cwd": str(target), "parent": parent, "dirs": dirs, "files": files}


@app.get("/api/history")
async def get_history_api() -> list[dict]:
    from .settings import get_history
    return [{"path": item.get("path", ""), "name": item.get("name", ""),
             "exists": Path(str(item.get("path", ""))).is_file()}
            for item in get_history() if isinstance(item, dict)]


@app.post("/api/history/remove")
async def remove_history_api(payload: dict) -> dict:
    from .settings import remove_history
    remove_history(str(payload.get("path", "")))
    return {"ok": True}


# ---------- 文库(文件夹 / 标签 / 高亮) ----------

def _library() -> Library:
    lib = STATE.get("library")
    if lib is None:
        raise HTTPException(500, "文库未初始化")
    return lib


@app.get("/api/library")
def library_overview() -> dict:
    return _library().overview()


@app.post("/api/library/folders")
def library_add_folder(payload: dict) -> dict:
    try:
        return _library().add_folder(str(payload.get("name", "")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/library/folders/rename")
def library_rename_folder(payload: dict) -> dict:
    try:
        _library().rename_folder(int(payload.get("id", 0)),
                                 str(payload.get("name", "")))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True}


@app.post("/api/library/folders/delete")
def library_delete_folder(payload: dict) -> dict:
    _library().delete_folder(int(payload.get("id", 0)))
    return {"ok": True}


@app.post("/api/library/documents/move")
def library_move_doc(payload: dict) -> dict:
    folder = payload.get("folder_id")
    try:
        _library().move_document(int(payload.get("id", 0)),
                                 int(folder) if folder else None)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True}


@app.post("/api/library/documents/tags")
def library_set_tags(payload: dict) -> dict:
    tags = payload.get("tags")
    if not isinstance(tags, list):
        raise HTTPException(400, "tags 必须是数组")
    try:
        _library().set_tags(int(payload.get("id", 0)), tags)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True}


@app.post("/api/library/documents/remove")
def library_remove_doc(payload: dict) -> dict:
    """从文库移除条目(不删除磁盘文件, 不影响已打开的标签)。"""
    _library().remove_document(int(payload.get("id", 0)))
    return {"ok": True}


@app.post("/api/library/progress")
def library_progress(payload: dict) -> dict:
    """记录阅读进度(当前页码)。"""
    try:
        _library().set_progress(int(payload.get("id", 0)),
                                int(payload.get("page", 0)))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True}


@app.post("/api/library/open")
async def library_open(payload: dict) -> dict:
    """从文库打开文档。文库条目本身是用户授权的白名单,
    仍校验文件存在与扩展名, 防数据库被篡改后读取任意文件。"""
    entry = _library().document_path(int(payload.get("id", 0)))
    if entry is None:
        raise HTTPException(404, "文档不在文库中")
    raw, name = entry
    p = Path(raw).expanduser().resolve()
    if not p.is_file() or p.suffix.lower() not in (".pdf", ".epub", ".txt"):
        raise HTTPException(404, "文件已不在原位置，请重新导入")
    # 已打开的文档直接激活, 不重复解析(整篇解析是打开的主要耗时)
    for old in STATE["docs"].values():
        if old["path"] == p:
            STATE.update(pdf=p, doc=old, pages=old["pages"], sizes=old["sizes"])
            try:
                if STATE["library"] is not None:
                    STATE["library"].upsert_document(
                        str(p), old["name"], old.get("title") or "")
            except Exception:
                pass
            return info(old["id"])
    if not _within_roots(p):
        parent = p.parent
        if parent.is_dir():
            STATE["roots"].append(parent)
    data = await _inspect(p, True)
    current = _register(p, data, name)
    return info(current["id"])


@app.get("/api/words/{num}")
async def words(num: int, doc: str = "") -> dict:
    """一页的词坐标, 供划线吸附(仅 PDF)。"""
    current = _document(doc)
    if current.get("fmt", "pdf") != "pdf":
        raise HTTPException(400, "该格式暂不支持划线")
    if not (1 <= num <= len(current["pages"])):
        raise HTTPException(404, "页码超出范围")
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            _executor(), page_words, str(current["path"]), num,
            current["stamp"])
    except Exception:
        await _restart_executor()
        raise HTTPException(409, "PDF 已变更或无法读取，请重新打开") from None
    return {"page": num, "words": result}


@app.get("/api/highlights")
def list_marks(doc: str = "") -> dict:
    current = _document(doc)
    if not current.get("lib"):
        return {"items": []}
    return {"items": _library().list_highlights(current["lib"])}


@app.post("/api/highlights")
def add_mark(payload: dict) -> dict:
    current = _document(str(payload.get("doc", "")))
    if not current.get("lib"):
        raise HTTPException(400, "该文档未登记到文库")
    if not (1 <= int(payload.get("page", 0)) <= len(current["pages"])):
        raise HTTPException(400, "页码超出范围")
    try:
        return _library().add_highlight(
            current["lib"], int(payload.get("page", 0)),
            payload.get("rects"), str(payload.get("color", "yellow")))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/highlights/delete")
def delete_mark(payload: dict) -> dict:
    _library().delete_highlight(int(payload.get("id", 0)))
    return {"ok": True}


@app.post("/api/open")
async def open_pdf(path: str = Query(...), skip_references: bool = True) -> dict:
    target = _safe_resolve(path)
    data = await _inspect(target, skip_references)
    current = _register(target, data)
    return info(current["id"])


# 打开任务的进度(下载/转换/解析), 供前端轮询
PROGRESS: dict[str, dict] = {}


def _set_progress(task: str, stage: str, done: int = 0, total: int | None = None) -> None:
    if not task:
        return
    if len(PROGRESS) > 200:
        PROGRESS.pop(next(iter(PROGRESS)))
    PROGRESS[task] = {"stage": stage, "done": done, "total": total, "ts": time.time()}


@app.get("/api/progress")
def get_progress(task: str = "") -> dict:
    return PROGRESS.get(task, {"stage": "unknown"})


@app.post("/api/open-url")
async def open_url(payload: dict) -> dict:
    """从 URL 打开 PDF; 网页链接经系统浏览器 headless 转成 PDF。

    所有 URL 先经 urlfetch 的 SSRF 校验(协议/凭证/内网拦截/重定向复验)。
    """
    from .urlfetch import UrlRejected, fetch_document, normalize_url

    raw = payload.get("url", "")
    kind = str(payload.get("kind", "auto"))
    task = str(payload.get("task", ""))
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(400, "链接为空")
    if kind not in ("auto", "pdf", "html"):
        raise HTTPException(400, "kind 必须是 auto / pdf / html")

    try:
        url = normalize_url(raw.strip())
        _set_progress(task, "download", 0, None)
        # 下载/转换是阻塞型网络 IO, 放线程里, 不占用 PDF 进程池
        path, name = await asyncio.to_thread(
            fetch_document,
            url,
            uploads_dir(),
            kind,
            200 * 1024 * 1024,
            (lambda stage, done, total: _set_progress(task, stage, done, total))
            if task else None,
        )
    except UrlRejected as exc:
        _set_progress(task, "error")
        raise HTTPException(400, str(exc)) from None
    except RuntimeError as exc:
        _set_progress(task, "error")
        raise HTTPException(502, str(exc)) from None

    root = uploads_dir().resolve()
    if root not in STATE["roots"]:
        STATE["roots"].append(root)

    _set_progress(task, "parse")
    data = await _inspect(path, True)
    current = _register(path, data, name)
    _set_progress(task, "done")
    return info(current["id"])


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), skip_references: bool = True) -> dict:
    """上传 PDF/EPUB 并立即打开。存到用户缓存目录, 不污染安装目录。"""
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    ext = Path(name).suffix.lower()
    if ext not in (".pdf", ".epub", ".txt"):
        raise HTTPException(400, "仅支持 PDF / EPUB 文件")

    dest_dir = uploads_dir()
    dest = dest_dir / f"{uuid4().hex}{ext}"
    fd, raw = tempfile.mkstemp(prefix="upload-", suffix=".part", dir=dest_dir)
    temporary = Path(raw)

    size = 0
    try:
        with os.fdopen(fd, "wb") as fh:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "文件过大(上限 200 MB)")
                fh.write(chunk)
        temporary.replace(dest)
        try:
            data = await _inspect(dest, skip_references)
        except HTTPException:
            _cleanup_upload(dest)
            raise
        current = _register(dest, data, name)
    finally:
        _cleanup_upload(temporary)
        await file.close()

    root = dest_dir.resolve()
    if root not in STATE["roots"]:
        STATE["roots"].append(root)

    return info(current["id"])


@app.get("/api/settings")
async def get_settings() -> dict:
    """返回可配置项与各服务密钥的"是否已配置"状态(不返回密钥内容)。"""
    from .settings import configured, get_proxy, profile

    done = configured()
    items = []
    cfg = STATE["cfg"]
    for name in sorted(PROVIDERS):
        env = PROVIDERS[name]["key_env"]
        saved = profile(name)
        items.append({
            "provider": name,
            "model": PROVIDERS[name]["model"],
            "models": PROVIDERS[name].get("models", []),
            "custom_model": cfg.model if cfg and cfg.provider == name else saved.get("model", ""),
            "qa_model": saved.get("qa_model", ""),
            "base_url": cfg.base_url if cfg and cfg.provider == name else saved.get("base_url", ""),
            "key_env": env,
            "needs_key": bool(env),
            "configured": (not env) or bool(done.get(env))
                          or bool(os.environ.get(env, "").strip()),
            "from_env": bool(env and os.environ.get(env, "").strip()),
        })
    cfg: TransConfig | None = STATE["cfg"]
    return {"current": cfg.provider if cfg else "", "items": items,
            "proxy": os.environ.get("PDFREAD_PROXY", "").strip() or get_proxy(),
            "proxy_from_env": bool(os.environ.get("PDFREAD_PROXY", "").strip())}


@app.post("/api/settings")
async def set_settings(payload: dict, doc: str = "") -> dict:
    """保存密钥 / 切换服务。密钥写入用户配置文件(权限 0600)。"""
    from .settings import profile, set_proxy, update_translation
    old = STATE["cfg"] or TransConfig()
    provider = str(payload.get("provider", old.provider)).strip()
    if provider not in PROVIDERS:
        raise HTTPException(400, "未知翻译服务")
    proxy = payload.get("proxy")
    if proxy is not None:
        if not isinstance(proxy, str):
            raise HTTPException(400, "设置值必须为字符串")
        proxy = proxy.strip()
        if proxy:
            p = urlsplit(proxy)
            if p.scheme not in ("http", "https") or not p.hostname \
                    or p.username or p.password:
                raise HTTPException(400, "代理地址格式应为 http://主机:端口，不含账号密码")
            try:
                port = p.port
            except ValueError:
                raise HTTPException(400, "代理端口无效") from None
            if port is not None and not (1 <= port <= 65535):
                raise HTTPException(400, "代理端口无效")
        set_proxy(proxy)
    saved = profile(provider)
    model = payload.get("model", old.model if provider == old.provider else saved.get("model", ""))
    base_url = payload.get("base_url", old.base_url if provider == old.provider else saved.get("base_url", ""))
    qa_model = payload.get("qa_model")
    key = payload.get("key")
    if not isinstance(model, str) or not isinstance(base_url, str) or (key is not None and not isinstance(key, str)) \
            or (qa_model is not None and not isinstance(qa_model, str)):
        raise HTTPException(400, "设置值必须为字符串")
    if key is not None and not PROVIDERS[provider]["key_env"]:
        raise HTTPException(400, "该服务无需密钥")
    try:
        cfg = replace(old, provider=provider, model=model.strip(), base_url=base_url.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if doc:
        _document(doc)
    update_translation(provider, cfg.model, cfg.base_url, key.strip() if key is not None else None,
                       qa_model.strip() if qa_model is not None else None)
    STATE["cfg"] = cfg
    return info(doc)


@app.get("/api/parse-settings")
async def get_parse_settings() -> dict:
    from .settings import PARSE_DEFAULTS, get_parse
    return {"options": get_parse(), "defaults": PARSE_DEFAULTS,
            "applied_paper_mode": bool(STATE.get("doc") and STATE["doc"].get("paper_mode"))}


@app.post("/api/parse-settings")
async def set_parse_settings(payload: dict, doc: str = "") -> dict:
    from .settings import get_parse, set_parse
    before, options = get_parse(), set_parse(payload or {})
    structural = {"paper_mode", "skip_refs", "skip_tables", "mask_math", "fold_formula"}
    current = _document(doc) if doc else STATE["doc"]
    reloaded = False
    if current and any(before.get(k) != options.get(k) for k in structural):
        data = await _inspect(current["path"], options.get("skip_refs", True))
        current = _register(current["path"], data, current["name"])
        reloaded = True
    return {"options": options, "applied_paper_mode": bool(current and current.get("paper_mode")),
            "reloaded": reloaded, "info": info(current["id"]) if current else info()}


@app.get("/api/chapter/{num}")
async def chapter_html(num: int, doc: str = "") -> HTMLResponse:
    """净化后的章节 HTML(EPUB/TXT), 供左栏渲染(替代 PDF 页面图)。"""
    current = _document(doc)
    htmls = current.get("html")
    if htmls is None:
        raise HTTPException(400, "该文档没有章节内容")
    if not (1 <= num <= len(htmls)):
        raise HTTPException(404, "章节超出范围")
    return HTMLResponse(htmls[num - 1],
                        headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/epub-res/{num}")
async def epub_resource(num: int, path: str = Query(...), doc: str = "") -> Response:
    """EPUB 包内图片资源。路径限定在包内, 防穿越。"""
    current = _document(doc)
    if current.get("fmt") != "epub":
        raise HTTPException(400, "该文档不是 EPUB")
    if not (1 <= num <= len(current["pages"])):
        raise HTTPException(404, "章节超出范围")
    from .epub import read_resource
    try:
        data, mime = read_resource(str(current["path"]), path)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    headers = {"Cache-Control": "private, max-age=3600"}
    if mime == "image/svg+xml":
        # SVG 以主动文档打开可执行脚本; sandbox 头禁止脚本与同源访问
        headers["Content-Security-Policy"] = "sandbox"
    return Response(data, media_type=mime, headers=headers)


@app.get("/api/page/{num}.svg")
async def page_svg(num: int, doc: str = "") -> Response:
    """矢量页面(默认渲染方式): 文字转路径, 缩放不失真, 无需 DPI 计算。"""
    current = _document(doc)
    if current.get("fmt", "pdf") != "pdf":
        raise HTTPException(400, "该格式无页面图，请使用章节接口")
    if not (1 <= num <= len(current["pages"])):
        raise HTTPException(404, "页码超出范围")
    try:
        svg = await asyncio.get_running_loop().run_in_executor(
            _executor(), render_page_svg, str(current["path"]), num,
            current["stamp"])
    except Exception:
        await _restart_executor()
        raise HTTPException(409, "PDF 已变更或无法渲染，请重新打开") from None
    return Response(
        svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=3600" if doc else "no-store"},
    )


@app.get("/api/page/{num}.png")
async def page_png(num: int, dpi: int = 110, doc: str = "") -> Response:
    current = _document(doc)
    if current.get("fmt", "pdf") != "pdf":
        raise HTTPException(400, "该格式无页面图片，请使用章节接口")
    if not (1 <= num <= len(current["pages"])):
        raise HTTPException(404, "页码超出范围")
    dpi = max(60, min(dpi, 450))
    try:
        png = await asyncio.get_running_loop().run_in_executor(
            _executor(), render_page, str(current["path"]), num, dpi, current["stamp"])
    except Exception:
        await _restart_executor()
        raise HTTPException(409, "PDF 已变更或无法渲染，请重新打开") from None
    return Response(
        png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600" if doc else "no-store"},
    )


@app.get("/api/text/{num}")
async def page_text(num: int, doc: str = "") -> dict:
    pages = _document(doc)["pages"]
    if not (1 <= num <= len(pages)):
        raise HTTPException(404, "页码超出范围")
    return {"page": num, "paras": [p.to_dict() for p in pages[num - 1]]}


@app.get("/api/translate")
async def translate_stream(
    start: int = 1,
    end: int = 0,
    lang: str = "zh",
    doc: str = "",
) -> StreamingResponse:
    """逐页流式翻译(SSE)。边翻边推, 前端立即可读。"""
    current = _document(doc)
    pages = current["pages"]
    if not pages:
        raise HTTPException(400, "尚未加载 PDF")

    total = len(pages)
    end = total if end == 0 else end
    if not 1 <= start <= end <= total or lang != "zh":
        raise HTTPException(400, "页码范围无效，或目标语言不是 zh")

    cfg: TransConfig = replace(STATE["cfg"])
    try:
        tr = Translator(cfg, STATE["cache"])
    except Exception as e:
        raise HTTPException(400, str(e))

    async def gen():
        tasks = set()
        failed = 0
        try:
            yield _sse("meta", {"start": start, "end": end, "total": total, "doc": current["id"]})
            async def work(n):
                from .paper import unmask_formulas
                blocks = pages[n - 1]
                indexes = [i for i, p in enumerate(blocks) if _translatable(getattr(p, "kind", "body"))]
                values = await tr.translate([blocks[i].text for i in indexes], lang) if indexes else []
                if len(values) != len(indexes):
                    raise ValueError("翻译结果段落数不匹配")
                out = [""] * len(blocks)
                for i, value in zip(indexes, values): out[i] = value
                items = []
                for p, value in zip(blocks, out):
                    formulas = getattr(p, "formulas", None) or {}
                    src = unmask_formulas(p.text, formulas) if formulas else p.text
                    dst = unmask_formulas(value, formulas) if value and formulas else value
                    items.append({"idx": p.idx, "kind": p.kind, "src": src, "dst": dst,
                                  "status": "failed" if dst.startswith("[翻译失败]") else "success"})
                return n, items

            pending = iter(range(start, end + 1))
            for _ in range(min(cfg.concurrency, end - start + 1)):
                tasks.add(asyncio.create_task(work(next(pending))))
            while tasks:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    tasks.remove(task)
                    n, items = task.result()
                    failed += sum(it["status"] == "failed" for it in items)
                    yield _sse("page", {"page": n, "items": items, "doc": current["id"]})
                    following = next(pending, None)
                    if following is not None:
                        tasks.add(asyncio.create_task(work(following)))
            yield _sse("done", {"ok": failed == 0, "failed": failed})
        except Exception:
            yield _sse("failure", {"message": "翻译任务中断，请重试；已完成段落保留在缓存中"})
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/polish")
async def polish_stream(doc: str = "", lang: str = "zh") -> StreamingResponse:
    """整体润色(第二遍): 取全部已缓存译文, 逐页顺序润色,
    每页携带上一页润色结尾约 300 字作为衔接上下文。"""
    current = _document(doc)
    pages = current["pages"]
    if not pages:
        raise HTTPException(400, "尚未加载文档")
    if lang != "zh":
        raise HTTPException(400, "目标语言仅支持 zh")

    cfg: TransConfig = replace(STATE["cfg"])
    try:
        tr = Translator(cfg, STATE["cache"])
    except Exception as e:
        raise HTTPException(400, str(e))

    async def gen():
        from .paper import unmask_formulas
        context = ""
        skipped = 0
        try:
            yield _sse("meta", {"total": len(pages), "doc": current["id"]})
            for n in range(1, len(pages) + 1):
                idxs, kinds, srcs, dsts = [], [], [], []
                for p in pages[n - 1]:
                    if not _translatable(getattr(p, "kind", "body")):
                        continue
                    formulas = getattr(p, "formulas", None) or {}
                    dst = tr.lookup(p.text, lang)
                    if not dst:
                        continue
                    src = unmask_formulas(p.text, formulas) if formulas else p.text
                    dst = unmask_formulas(dst, formulas) if formulas else dst
                    idxs.append(p.idx)
                    kinds.append(p.kind)
                    srcs.append(src)
                    dsts.append(dst)
                if not dsts:
                    skipped += 1
                    yield _sse("page", {"page": n, "skip": True,
                                        "doc": current["id"]})
                    continue
                polished = await tr.polish(dsts, context, lang)
                context = polished[-1][-300:]
                items = [{"idx": i, "kind": k, "src": s, "dst": d,
                          "status": "success"}
                         for i, k, s, d in zip(idxs, kinds, srcs, polished)]
                yield _sse("page", {"page": n, "items": items,
                                    "doc": current["id"]})
            yield _sse("done", {"ok": True, "skipped": skipped})
        except Exception:
            yield _sse("failure",
                       {"message": "润色任务中断，请重试；已完成页面保留在缓存中"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


QA_BUDGET = 60000  # 问答正文字符预算: 超出保留首 70% + 尾 30%


def _fulltext(current: dict, budget: int = QA_BUDGET) -> str:
    """拼接全文正文(带页码标记), 超预算时掐中段留首尾。"""
    parts = []
    for n, page in enumerate(current["pages"], 1):
        text = "\n".join(p.text for p in page if p.text.strip())
        if text:
            parts.append(f"[第{n}页]\n{text}")
    full = "\n\n".join(parts)
    if len(full) <= budget:
        return full
    head = int(budget * 0.7)
    return (full[:head] + "\n\n[……中间部分因长度限制省略……]\n\n"
            + full[-(budget - head):])


def _ask_args(payload: dict):
    """问答类接口的公共入参校验: 文档 / 问题 / 历史(过滤 system 防注入)。"""
    current = _document(str(payload.get("doc", "")))
    question = str(payload.get("question", "")).strip()
    if not question or len(question) > 4000:
        raise HTTPException(400, "问题为空或过长(上限 4000 字符)")
    history = payload.get("history") or []
    if not isinstance(history, list) or len(history) > 20:
        raise HTTPException(400, "对话历史格式无效")
    clean = []
    for h in history:
        if (isinstance(h, dict) and h.get("role") in ("user", "assistant")
                and isinstance(h.get("content"), str)
                and len(h["content"]) <= 8000):
            clean.append({"role": h["role"], "content": h["content"]})
    return current, question, clean


def _qa_cfg() -> TransConfig:
    """问答/速读专用配置: 若该服务配置了独立的问答模型则覆盖, 否则沿用翻译模型。"""
    from .settings import profile
    cfg: TransConfig = replace(STATE["cfg"])
    qa = profile(cfg.provider).get("qa_model", "").strip()
    if qa:
        cfg = replace(cfg, model=qa)
    return cfg


@app.post("/api/ask")
async def ask_doc(payload: dict) -> dict:
    """基于当前文档全文的 AI 问答(非流式, 保留给简单调用方)。"""
    current, question, clean = _ask_args(payload)
    cfg: TransConfig = _qa_cfg()
    try:
        tr = Translator(cfg, STATE["cache"])
    except Exception as e:
        raise HTTPException(400, str(e))
    try:
        answer = await tr.ask(question, _fulltext(current), clean)
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from None
    return {"answer": answer}


@app.post("/api/ask-stream")
async def ask_stream(payload: dict) -> StreamingResponse:
    """流式问答(SSE): delta 事件逐块推送, done/failure 收尾。"""
    current, question, clean = _ask_args(payload)
    cfg: TransConfig = _qa_cfg()
    try:
        tr = Translator(cfg, STATE["cache"])
    except Exception as e:
        raise HTTPException(400, str(e))

    async def gen():
        try:
            async for delta in tr.ask_stream(question, _fulltext(current), clean):
                yield _sse("delta", {"text": delta})
            yield _sse("done", {"ok": True})
        except RuntimeError as e:
            yield _sse("failure", {"message": str(e)})
        except Exception:
            yield _sse("failure", {"message": "问答请求中断，请重试"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/translate-text")
async def translate_text(payload: dict) -> dict:
    """划词翻译: 翻译一小段选中文字(走翻译缓存, 重复划不重复计费)。"""
    _document(str(payload.get("doc", "")))
    text = str(payload.get("text", "")).strip()
    if not text or len(text) > 20000:
        raise HTTPException(400, "文本为空或过长(上限 20000 字符)")
    cfg: TransConfig = replace(STATE["cfg"])
    try:
        tr = Translator(cfg, STATE["cache"])
    except Exception as e:
        raise HTTPException(400, str(e))
    values = await tr.translate([text], "zh")
    dst = values[0] if values else ""
    if dst.startswith("[翻译失败]"):
        raise HTTPException(502, dst)
    return {"dst": dst}


@app.post("/api/summary")
async def summary_doc(payload: dict) -> dict:
    """速读摘要: 固定结构化提示词, 结果按(全文, 模型)缓存, 重开零成本。"""
    current = _document(str(payload.get("doc", "")))
    cfg: TransConfig = _qa_cfg()
    try:
        tr = Translator(cfg, STATE["cache"])
    except Exception as e:
        raise HTTPException(400, str(e))
    text = _fulltext(current)
    key = Cache.key(text, "summary\x00" + tr.cache_model, "zh")
    hit = STATE["cache"].get_many([key]).get(key)
    if hit:
        return {"answer": hit, "cached": True}
    try:
        answer = await tr.summarize(text)
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from None
    STATE["cache"].put_many([(key, answer)])
    return {"answer": answer, "cached": False}


@app.get("/api/raw")
async def raw_pdf(doc: str = "") -> FileResponse:
    current = _document(doc)
    mime = {"epub": "application/epub+zip", "txt": "text/plain"}.get(
        current.get("fmt"), "application/pdf")
    return FileResponse(current["path"], media_type=mime,
                        headers={"Cache-Control": "no-store"})


# ---------- 版本更新 ----------

_UPDATE_REPO = "fhan235/pdfread"
_UPDATE_TTL = 6 * 3600          # 检查结果缓存 6 小时
_UPDATE_MAX = 500 * 1024 * 1024  # 安装包上限 500MB
_update_cache: dict = {"ts": 0.0, "data": None}


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


async def _latest_release(force: bool = False) -> dict:
    """查询 GitHub 最新 Release, 结果缓存 6 小时, 离线时静默返回旧值。

    网页端(releases/latest 302 跳转)优先: 不占未认证 API 的限流配额;
    网页端失败时仍尝试 API(两条通道互为备份); 两通道版本号一致才合并。
    """
    if not force and _update_cache["data"] and \
            time.time() - _update_cache["ts"] < _UPDATE_TTL:
        return _update_cache["data"]
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
    _update_cache.update(ts=time.time(), data=data)
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


def _consume_update_result() -> dict | None:
    """启动时检查上次自动更新的结果(成功/失败), 只报一次。"""
    marker = cache_dir() / "update" / "pending.json"
    try:
        data = json.loads(marker.read_text("utf-8"))
        marker.unlink()
    except Exception:
        return None
    from . import __version__
    want = str(data.get("version", ""))
    ok = bool(want) and _ver_tuple(__version__) >= _ver_tuple(want)
    return {"ok": ok, "version": want, "log": str(data.get("log", ""))}


@app.get("/api/update/check")
async def update_check(force: bool = False) -> dict:
    data = await _latest_release(force)
    result = _consume_update_result()
    if result is not None:
        data["update_result"] = result
    return data


async def _download_asset(data: dict, task: str = "") -> Path:
    """下载最新安装包到缓存目录, 返回路径。只允许本仓库 Release 地址。"""
    url = data.get("asset_url") or ""
    if not data.get("newer") or not url:
        raise HTTPException(400, data.get("error") or "没有可用的新版本")
    parts = urlsplit(url)
    allowed = (parts.hostname == "github.com"
               and parts.path.startswith(f"/{_UPDATE_REPO}/releases/download/"))
    if parts.scheme != "https" or not allowed:
        raise HTTPException(400, "下载地址不合法")

    dest_dir = cache_dir() / "downloads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / data["asset_name"]
    try:
        async with _update_client(timeout=60.0,
                                  follow_redirects=True) as client:
            async with client.stream(
                    "GET", url,
                    headers={"User-Agent": "pdfread-updater"}) as r:
                if r.status_code != 200:
                    raise HTTPException(502, f"下载失败: HTTP {r.status_code}")
                size = 0
                total = int(r.headers.get("content-length") or 0) or None
                with dest.open("wb") as fh:
                    async for chunk in r.aiter_bytes(1 << 16):
                        size += len(chunk)
                        if size > _UPDATE_MAX:
                            raise HTTPException(413, "安装包过大")
                        fh.write(chunk)
                        _set_progress(task, "download", size, total)
        _set_progress(task, "done", 1, 1)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(502, f"下载失败: {exc}") from None
    return dest


@app.post("/api/update/download")
async def update_download() -> dict:
    """仅下载最新安装包到缓存目录。"""
    data = await _latest_release()
    dest = await _download_asset(data)
    return {"path": str(dest), "name": dest.name, "size": dest.stat().st_size,
            "version": data.get("latest", "")}


@app.post("/api/update/apply")
async def update_apply(task: str = "") -> dict:
    """一键更新: 下载安装包 -> 派生自身 exe 作为更新器 -> 前端随后调用 shutdown。

    仅打包后的桌面应用可用(需要可替换的安装目录); 源码模式请用 pip 升级。
    更新器是应用自身的第二个进程(--apply-update), 不走脚本解释器,
    避免 PowerShell 脚本被杀毒软件拦截。写入 pending 标记,
    下次启动时校验更新结果并在检查接口中回执。
    """
    if not getattr(sys, "frozen", False):
        raise HTTPException(400, "源码/开发模式不支持自动安装，请用 pip 或重新下载")
    data = await _latest_release()
    pkg = await _download_asset(data, task)

    # 更新回执: 下次启动按版本号判断是否成功
    log_path = cache_dir() / "update" / "update.log"
    try:
        marker = {"version": data.get("latest", ""), "ts": time.time(),
                  "log": str(log_path)}
        (cache_dir() / "update").mkdir(parents=True, exist_ok=True)
        (cache_dir() / "update" / "pending.json").write_text(
            json.dumps(marker, ensure_ascii=False), "utf-8")
    except OSError:
        pass

    from .paths import install_target
    args = [sys.executable, "--apply-update", str(pkg), str(os.getpid())]
    if sys.platform == "win32":
        subprocess.Popen(
            args,
            creationflags=subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(args, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "version": data.get("latest", ""),
            "message": "更新器已就绪",
            "log": str(log_path),
            "install_dir": str(install_target()[0])}


# ---------- 页面心跳 / 关页退出看门狗 ----------
# 桌面版回退到浏览器模式时, 用户关掉页面后服务没有退出通道, 进程会残留。
# 页面每 5s 发一次心跳; pagehide(关页/刷新)时启动 8s 退出倒计时,
# 期间任何页面(含刷新后的新页面、其他标签页)心跳到达则取消。

_HEARTBEAT = {"last": 0.0, "kill_at": 0.0, "enabled": False}


@app.post("/api/heartbeat")
def heartbeat() -> dict:
    _HEARTBEAT["last"] = time.time()
    _HEARTBEAT["kill_at"] = 0.0    # 有心跳则取消关页倒计时
    return {"ok": True}


@app.post("/api/pagehide")
def pagehide() -> dict:
    if _HEARTBEAT["enabled"]:
        _HEARTBEAT["kill_at"] = time.time() + 8
    return {"ok": True}


def enable_page_watchdog() -> None:
    """开启关页看门狗(仅桌面版浏览器回退模式调用)。

    两道保险: pagehide 显式倒计时; 以及心跳消失兜底(浏览器被强杀/
    崩溃时 beacon 可能没送出)。后台标签节流(约 1 次/分钟)不会误伤。
    """
    if _HEARTBEAT["enabled"]:
        return
    _HEARTBEAT["enabled"] = True

    def loop() -> None:
        while True:
            time.sleep(2)
            now = time.time()
            kill_at = _HEARTBEAT["kill_at"]
            gone = _HEARTBEAT["last"] and now - _HEARTBEAT["last"] > 180
            if (kill_at and now >= kill_at) or gone:
                _close_pool()
                os._exit(0)

    threading.Thread(target=loop, daemon=True).start()


@app.post("/api/shutdown")
async def shutdown() -> dict:
    """退出应用。

    先返回响应, 再强制回收进程池(worker 卡住也不能拖住退出),
    最后退出。os._exit 放在 finally, 任何清理异常都不能阻止退出。
    """

    async def stop() -> None:
        try:
            await asyncio.sleep(0.4)
            _close_pool()
        finally:
            os._exit(0)

    asyncio.create_task(stop())
    return {"ok": True, "message": "服务即将退出"}


# ---------- 初始化 ----------

def configure(
    pdf: Path | None,
    roots: list[Path],
    cfg: TransConfig,
    cache_path: Path,
    skip_references: bool = True,
) -> None:
    """由 CLI / 桌面入口调用, 装配运行时状态。"""
    STATE["cfg"] = cfg
    if STATE["cache"] is not None:
        STATE["cache"].close()
    STATE["cache"] = Cache(str(cache_path))
    if STATE["library"] is not None:
        STATE["library"].close()
    STATE["library"] = Library(str(config_dir() / "library.db"))
    STATE["roots"] = [r.resolve() for r in roots]
    STATE.update(pdf=None, doc=None, docs={}, pages=[], sizes=[])
    if pdf is not None:
        _load(pdf, skip_references)


def _translatable(kind: str) -> bool:
    from .settings import get_parse
    options = get_parse()
    if kind == "figtext": return bool(options.get("translate_figtext"))
    if kind == "caption": return bool(options.get("translate_caption", True))
    return kind in {"body", "heading", "list"}
