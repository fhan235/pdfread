"""PDF 双栏对照阅读器 —— 后端服务。

安全说明:
- 服务默认只绑定 127.0.0.1, 不对外暴露。
- API Key 仅从环境变量读取, 不写入磁盘、不返回给前端。
- 文件访问限定在白名单目录内, 防止路径穿越。
- 上传限制扩展名与大小。
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    Response,
    StreamingResponse,
)

from .extract import extract_pages
from .paths import static_dir, uploads_dir
from .translate import PROVIDERS, Cache, TransConfig, Translator

MAX_UPLOAD = 200 * 1024 * 1024  # 200 MB

app = FastAPI(title="PDF Bilingual Reader")

STATE: dict = {
    "pdf": None,
    "doc": None,
    "pages": [],
    "sizes": [],
    "roots": [],
    "cfg": None,
    "cache": None,
    "paper": False,   # 当前文档是否按论文模式解析
    "ver": "",
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


def _load(path: Path) -> None:
    if STATE["doc"] is not None:
        try:
            STATE["doc"].close()
        except Exception:
            pass

    from . import paper as paper_mod
    from .settings import get_parse

    opt = get_parse()
    mode = str(opt.get("paper_mode", "auto"))
    use_paper = (
        mode == "on"
        or (mode == "auto" and paper_mod.looks_like_paper(str(path)))
    )

    if use_paper:
        pages, sizes = paper_mod.extract_paper(
            str(path),
            skip_refs=bool(opt.get("skip_refs", True)),
            skip_tables=bool(opt.get("skip_tables", True)),
            mask_math=bool(opt.get("mask_math", True)),
            fold_formula=bool(opt.get("fold_formula", True)),
        )
    else:
        pages, sizes = extract_pages(str(path))

    STATE["paper"] = use_paper
    STATE["pdf"] = path
    STATE["doc"] = pymupdf.open(str(path))
    STATE["pages"] = pages
    STATE["sizes"] = sizes
    # 文档版本标识: 前端以此区分浏览器缓存中的页面图片
    try:
        st = path.stat()
        STATE["ver"] = f"{st.st_mtime_ns:x}-{st.st_size:x}"
    except OSError:
        STATE["ver"] = ""


def _translatable(kind: str) -> bool:
    """该类型是否需要送去翻译。"""
    from .settings import get_parse

    opt = get_parse()
    if kind == "figtext":
        return bool(opt.get("translate_figtext", False))
    if kind == "caption":
        return bool(opt.get("translate_caption", True))
    return kind in ("body", "heading", "list")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------- 路由 ----------

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((static_dir() / "index.html").read_text("utf-8"))


@app.get("/api/info")
def info() -> dict:
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

    if STATE["doc"] is None:
        return {"loaded": False, **base}

    doc = STATE["doc"]
    # 只统计会送去翻译的内容, 便于界面显示真实工作量
    tr_chars = sum(
        len(p.text) for pg in STATE["pages"] for p in pg
        if _translatable(getattr(p, "kind", "body"))
    )
    return {
        "loaded": True,
        "name": STATE["pdf"].name,
        "path": str(STATE["pdf"]),
        "pages": doc.page_count,
        "chars": tr_chars,
        "paras": sum(
            1 for pg in STATE["pages"] for p in pg
            if _translatable(getattr(p, "kind", "body"))
        ),
        "sizes": STATE["sizes"],
        "ver": STATE.get("ver", ""),
        "paper_mode": bool(STATE.get("paper")),
        "title": doc.metadata.get("title") or "",
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
            if e.name.startswith("."):
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


@app.post("/api/open")
def open_pdf(path: str = Query(...)) -> dict:
    _load(_safe_resolve(path))
    return info()


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    """上传 PDF 并立即打开。存到用户缓存目录, 不污染安装目录。"""
    name = os.path.basename(file.filename or "")
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF 文件")

    dest_dir = uploads_dir()
    dest = dest_dir / name

    size = 0
    try:
        with dest.open("wb") as fh:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "文件过大(上限 200 MB)")
                fh.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise

    root = dest_dir.resolve()
    if root not in STATE["roots"]:
        STATE["roots"].append(root)

    _load(dest)
    return info()


@app.get("/api/settings")
def get_settings() -> dict:
    """返回可配置项与各服务密钥的"是否已配置"状态(不返回密钥内容)。"""
    from .settings import configured

    done = configured()
    items = []
    for name in sorted(PROVIDERS):
        env = PROVIDERS[name]["key_env"]
        items.append({
            "provider": name,
            "model": PROVIDERS[name]["model"],
            "key_env": env,
            "needs_key": bool(env),
            "configured": (not env) or bool(done.get(env))
                          or bool(os.environ.get(env, "").strip()),
            "from_env": bool(env and os.environ.get(env, "").strip()),
        })
    cfg: TransConfig | None = STATE["cfg"]
    return {"current": cfg.provider if cfg else "", "items": items}


@app.post("/api/settings")
async def set_settings(payload: dict) -> dict:
    """保存密钥 / 切换服务。密钥写入用户配置文件(权限 0600)。"""
    from .settings import set_key, set_provider

    provider = str(payload.get("provider", "")).strip()
    if provider:
        if provider not in PROVIDERS:
            raise HTTPException(400, f"未知服务: {provider}")
        cfg: TransConfig | None = STATE["cfg"]
        if cfg is not None:
            cfg.provider = provider
            cfg.model = ""
            cfg.base_url = ""
        set_provider(provider)

    key = payload.get("key")
    if key is not None:
        target = provider or (STATE["cfg"].provider if STATE["cfg"] else "")
        env = PROVIDERS.get(target, {}).get("key_env", "")
        if not env:
            raise HTTPException(400, "该服务无需密钥")
        set_key(env, str(key))

    return info()


@app.get("/api/parse-settings")
def get_parse_settings() -> dict:
    """论文模式与解析选项。"""
    from .settings import PARSE_DEFAULTS, get_parse

    return {
        "options": get_parse(),
        "defaults": PARSE_DEFAULTS,
        "applied_paper_mode": bool(STATE.get("paper")),
    }


@app.post("/api/parse-settings")
def set_parse_settings(payload: dict) -> dict:
    """保存解析选项。影响解析结构的项会触发当前文档重新解析。"""
    from .settings import get_parse, set_parse

    before = get_parse()
    opt = set_parse(payload or {})

    structural = (
        "paper_mode", "skip_refs", "skip_tables", "mask_math", "fold_formula",
    )
    need_reload = any(before.get(k) != opt.get(k) for k in structural)
    if need_reload and STATE["pdf"] is not None:
        _load(STATE["pdf"])

    return {
        "options": opt,
        "applied_paper_mode": bool(STATE.get("paper")),
        "reloaded": need_reload,
        "info": info(),
    }


@app.get("/api/page/{num}.png")
def page_png(num: int, dpi: int = 110) -> Response:
    doc = STATE["doc"]
    if doc is None:
        raise HTTPException(400, "尚未加载 PDF")
    if not (1 <= num <= doc.page_count):
        raise HTTPException(404, "页码超出范围")
    # 高分屏下前端会按物理像素请求较高 DPI, 上限放宽到 300
    dpi = max(60, min(dpi, 300))
    pix = doc[num - 1].get_pixmap(dpi=dpi)
    return Response(
        io.BytesIO(pix.tobytes("png")).getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/text/{num}")
def page_text(num: int) -> dict:
    pages = STATE["pages"]
    if not (1 <= num <= len(pages)):
        raise HTTPException(404, "页码超出范围")
    return {"page": num, "paras": [p.to_dict() for p in pages[num - 1]]}


@app.get("/api/translate")
async def translate_stream(
    start: int = 1,
    end: int = 0,
    lang: str = "zh",
) -> StreamingResponse:
    """逐页流式翻译(SSE)。边翻边推, 前端立即可读。"""
    pages = STATE["pages"]
    if not pages:
        raise HTTPException(400, "尚未加载 PDF")

    total = len(pages)
    end = total if end <= 0 else min(end, total)
    start = max(1, min(start, total))

    cfg: TransConfig = STATE["cfg"]
    try:
        tr = Translator(cfg, STATE["cache"])
    except Exception as e:
        raise HTTPException(400, str(e))

    async def gen():
        yield _sse("meta", {"start": start, "end": end, "total": total})

        from .paper import unmask_formulas

        queue: dict[int, list[str]] = {}
        lock = asyncio.Lock()
        pending = list(range(start, end + 1))
        batch = max(1, cfg.concurrency // 2)

        for i in range(0, len(pending), batch):
            nums = pending[i : i + batch]

            async def work(n: int) -> None:
                # 只把需要翻译的块送出去, 图内标签等按设置跳过
                blocks = pages[n - 1]
                idxs = [
                    j for j, p in enumerate(blocks)
                    if _translatable(getattr(p, "kind", "body"))
                ]
                texts = [blocks[j].text for j in idxs]
                got = await tr.translate(texts, lang) if texts else []
                out = [""] * len(blocks)
                for j, val in zip(idxs, got):
                    out[j] = val
                async with lock:
                    queue[n] = out

            await asyncio.gather(*(work(n) for n in nums))

            for n in nums:
                items = []
                for j, p in enumerate(pages[n - 1]):
                    dst = queue[n][j] if j < len(queue[n]) else ""
                    fmap = getattr(p, "formulas", None) or {}
                    if dst and fmap:
                        dst = unmask_formulas(dst, fmap)
                    src = p.text
                    if fmap:
                        src = unmask_formulas(src, fmap)
                    items.append({
                        "idx": p.idx,
                        "kind": p.kind,
                        "src": src,
                        "dst": dst,
                    })
                yield _sse("page", {"page": n, "items": items})
                queue.pop(n, None)

        yield _sse("done", {"ok": True})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/raw")
def raw_pdf() -> FileResponse:
    if STATE["pdf"] is None:
        raise HTTPException(400, "尚未加载 PDF")
    return FileResponse(STATE["pdf"], media_type="application/pdf")


# ---------- 初始化 ----------

def configure(
    pdf: Path | None,
    roots: list[Path],
    cfg: TransConfig,
    cache_path: Path,
) -> None:
    """由 CLI / 桌面入口调用, 装配运行时状态。"""
    STATE["cfg"] = cfg
    STATE["cache"] = Cache(str(cache_path))
    STATE["roots"] = list(roots)
    if pdf is not None:
        _load(pdf)
