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
import multiprocessing
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
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

from .pdfworker import inspect_pdf, render_page
from .paths import static_dir, uploads_dir
from .translate import PROVIDERS, Cache, TransConfig, Translator

MAX_UPLOAD = 200 * 1024 * 1024  # 200 MB

_pool = None


def _executor():
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    return _pool


@asynccontextmanager
async def lifespan(app):
    yield
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=True, cancel_futures=True)
        _pool = None
    if STATE["cache"] is not None:
        STATE["cache"].close()
        STATE["cache"] = None


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
    if not p.is_file() or p.suffix.lower() != ".pdf":
        raise HTTPException(404, "PDF 文件不存在")
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
    current = {**data, "path": path, "name": name or path.name, "id": uuid4().hex}
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


def _load(path: Path, skip_references: bool = True) -> None:
    from .settings import get_parse
    data = _executor().submit(inspect_pdf, str(path), skip_references, get_parse()).result()
    _register(path, data)


async def _inspect(path: Path, skip_references: bool) -> dict:
    from .settings import get_parse
    try:
        return await asyncio.get_running_loop().run_in_executor(_executor(), inspect_pdf, str(path), skip_references, get_parse())
    except Exception:
        raise HTTPException(400, "PDF 无法解析，请确认文件完整、未加密且包含页面") from None


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
        "paper_mode": bool(current.get("paper_mode")),
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
            elif e.suffix.lower() == ".pdf":
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


@app.post("/api/open")
async def open_pdf(path: str = Query(...), skip_references: bool = True) -> dict:
    target = _safe_resolve(path)
    data = await _inspect(target, skip_references)
    current = _register(target, data)
    return info(current["id"])


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), skip_references: bool = True) -> dict:
    """上传 PDF 并立即打开。存到用户缓存目录, 不污染安装目录。"""
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF 文件")

    dest_dir = uploads_dir()
    dest = dest_dir / f"{uuid4().hex}.pdf"
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
    from .settings import configured, profile

    done = configured()
    items = []
    cfg = STATE["cfg"]
    for name in sorted(PROVIDERS):
        env = PROVIDERS[name]["key_env"]
        saved = profile(name)
        items.append({
            "provider": name,
            "model": PROVIDERS[name]["model"],
            "custom_model": cfg.model if cfg and cfg.provider == name else saved.get("model", ""),
            "base_url": cfg.base_url if cfg and cfg.provider == name else saved.get("base_url", ""),
            "key_env": env,
            "needs_key": bool(env),
            "configured": (not env) or bool(done.get(env))
                          or bool(os.environ.get(env, "").strip()),
            "from_env": bool(env and os.environ.get(env, "").strip()),
        })
    cfg: TransConfig | None = STATE["cfg"]
    return {"current": cfg.provider if cfg else "", "items": items}


@app.post("/api/settings")
async def set_settings(payload: dict, doc: str = "") -> dict:
    """保存密钥 / 切换服务。密钥写入用户配置文件(权限 0600)。"""
    from .settings import profile, update_translation
    old = STATE["cfg"] or TransConfig()
    provider = str(payload.get("provider", old.provider)).strip()
    if provider not in PROVIDERS:
        raise HTTPException(400, "未知翻译服务")
    saved = profile(provider)
    model = payload.get("model", old.model if provider == old.provider else saved.get("model", ""))
    base_url = payload.get("base_url", old.base_url if provider == old.provider else saved.get("base_url", ""))
    key = payload.get("key")
    if not isinstance(model, str) or not isinstance(base_url, str) or (key is not None and not isinstance(key, str)):
        raise HTTPException(400, "设置值必须为字符串")
    if key is not None and not PROVIDERS[provider]["key_env"]:
        raise HTTPException(400, "该服务无需密钥")
    try:
        cfg = replace(old, provider=provider, model=model.strip(), base_url=base_url.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if doc:
        _document(doc)
    update_translation(provider, cfg.model, cfg.base_url, key.strip() if key is not None else None)
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


@app.get("/api/page/{num}.png")
async def page_png(num: int, dpi: int = 110, doc: str = "") -> Response:
    current = _document(doc)
    if not (1 <= num <= len(current["pages"])):
        raise HTTPException(404, "页码超出范围")
    dpi = max(60, min(dpi, 300))
    try:
        png = await asyncio.get_running_loop().run_in_executor(
            _executor(), render_page, str(current["path"]), num, dpi, current["stamp"])
    except Exception:
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


@app.get("/api/raw")
async def raw_pdf(doc: str = "") -> FileResponse:
    return FileResponse(_document(doc)["path"], media_type="application/pdf", headers={"Cache-Control": "no-store"})


@app.post("/api/shutdown")
async def shutdown() -> dict:
    def stop() -> None:
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=stop, daemon=True).start()
    return {"ok": True}


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
