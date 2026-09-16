"""PDF 双栏对照阅读器 —— 后端服务。

启动:
    export DEEPSEEK_API_KEY='sk-...'
    python server.py --pdf /path/to/paper.pdf

安全说明:
- 服务默认只绑定 127.0.0.1, 不对外暴露。
- API Key 仅从环境变量读取, 不写入磁盘、不返回给前端。
- 文件访问限定在启动时指定的白名单目录内, 防止路径穿越。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
from pathlib import Path

import pymupdf
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    Response,
    StreamingResponse,
)

from extract import extract_pages
from translate import PROVIDERS, Cache, TransConfig, Translator

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="PDF Bilingual Reader")

# 运行时状态
STATE: dict = {
    "pdf": None,        # Path
    "doc": None,        # pymupdf.Document
    "pages": [],        # list[list[Para]]
    "sizes": [],        # list[(w, h)]
    "roots": [],        # 允许访问的目录白名单
    "cfg": None,        # TransConfig
    "cache": None,      # Cache
}


# ---------- 工具 ----------

def _safe_resolve(raw: str) -> Path:
    """校验路径在白名单目录内, 防止路径穿越读取任意文件。"""
    p = Path(raw).expanduser().resolve()
    roots: list[Path] = STATE["roots"]
    if not any(str(p).startswith(str(r) + os.sep) or p == r for r in roots):
        raise HTTPException(403, "该路径不在允许访问的目录内")
    if not p.is_file() or p.suffix.lower() != ".pdf":
        raise HTTPException(404, "PDF 文件不存在")
    return p


def _load(path: Path) -> None:
    """加载并解析 PDF。"""
    if STATE["doc"] is not None:
        try:
            STATE["doc"].close()
        except Exception:
            pass
    pages, sizes = extract_pages(str(path))
    STATE["pdf"] = path
    STATE["doc"] = pymupdf.open(str(path))
    STATE["pages"] = pages
    STATE["sizes"] = sizes


# ---------- 路由 ----------

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((BASE_DIR / "static" / "index.html").read_text("utf-8"))


@app.get("/api/info")
def info() -> dict:
    """当前文档的元信息。"""
    if STATE["doc"] is None:
        return {"loaded": False}
    doc = STATE["doc"]
    chars = sum(len(p.text) for pg in STATE["pages"] for p in pg)

    # Key 缺失不应影响界面加载, 仅作为状态提示返回
    model, ready, warn = "", False, ""
    cfg: TransConfig | None = STATE["cfg"]
    if cfg is not None:
        try:
            _, model, _ = cfg.resolve()
            ready = True
        except Exception as e:
            warn = str(e)
            model = PROVIDERS.get(cfg.provider, {}).get("model", "")

    return {
        "loaded": True,
        "name": STATE["pdf"].name,
        "path": str(STATE["pdf"]),
        "pages": doc.page_count,
        "chars": chars,
        "paras": sum(len(pg) for pg in STATE["pages"]),
        "sizes": STATE["sizes"],
        "provider": cfg.provider if cfg else "",
        "model": model,
        "ready": ready,
        "warn": warn,
        "title": doc.metadata.get("title") or "",
    }


@app.post("/api/open")
def open_pdf(path: str = Query(...)) -> dict:
    """切换文档。"""
    p = _safe_resolve(path)
    _load(p)
    return info()


@app.get("/api/page/{num}.png")
def page_png(num: int, dpi: int = 110) -> Response:
    """渲染指定页为 PNG(左栏原文展示)。"""
    doc = STATE["doc"]
    if doc is None:
        raise HTTPException(400, "尚未加载 PDF")
    if not (1 <= num <= doc.page_count):
        raise HTTPException(404, "页码超出范围")
    dpi = max(60, min(dpi, 200))
    pix = doc[num - 1].get_pixmap(dpi=dpi)
    buf = io.BytesIO(pix.tobytes("png"))
    return Response(
        buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/text/{num}")
def page_text(num: int) -> dict:
    """某页的原文段落(不翻译)。"""
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

        # 并行预取: 同时跑多页, 但按页序推送, 保证阅读顺序
        queue: dict[int, list[str]] = {}
        lock = asyncio.Lock()
        pending = list(range(start, end + 1))
        # 每轮并行处理若干页
        batch = max(1, cfg.concurrency // 2)

        for i in range(0, len(pending), batch):
            nums = pending[i : i + batch]

            async def work(n: int) -> None:
                texts = [p.text for p in pages[n - 1]]
                out = await tr.translate(texts, lang) if texts else []
                async with lock:
                    queue[n] = out

            await asyncio.gather(*(work(n) for n in nums))

            for n in nums:
                paras = pages[n - 1]
                items = [
                    {
                        "idx": p.idx,
                        "kind": p.kind,
                        "src": p.text,
                        "dst": queue[n][j] if j < len(queue[n]) else "",
                    }
                    for j, p in enumerate(paras)
                ]
                yield _sse("page", {"page": n, "items": items})
                queue.pop(n, None)

        yield _sse("done", {"ok": True})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/raw")
def raw_pdf() -> FileResponse:
    """原始 PDF 文件(可选, 供前端用 pdf.js 时使用)。"""
    if STATE["pdf"] is None:
        raise HTTPException(400, "尚未加载 PDF")
    return FileResponse(STATE["pdf"], media_type="application/pdf")


# ---------- 启动 ----------

def main() -> None:
    ap = argparse.ArgumentParser(description="PDF 双栏对照翻译阅读器")
    ap.add_argument("--pdf", help="启动时加载的 PDF 路径")
    ap.add_argument(
        "--root",
        action="append",
        default=[],
        help="允许访问的目录(可多次指定), 默认为 PDF 所在目录",
    )
    ap.add_argument("--provider", default="deepseek",
                    help="deepseek | silicon | qwen | openai | ollama")
    ap.add_argument("--model", default="", help="覆盖默认模型名")
    ap.add_argument("--base-url", default="", help="覆盖默认 API 地址")
    ap.add_argument("--concurrency", type=int, default=8, help="并发请求数")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址")
    ap.add_argument("--port", type=int, default=8011, help="监听端口")
    ap.add_argument("--cache", default=str(BASE_DIR / "cache.db"),
                    help="翻译缓存数据库路径")
    args = ap.parse_args()

    STATE["cfg"] = TransConfig(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        concurrency=args.concurrency,
    )
    STATE["cache"] = Cache(args.cache)

    roots = [Path(r).expanduser().resolve() for r in args.root]
    if args.pdf:
        pdf = Path(args.pdf).expanduser().resolve()
        if not pdf.is_file():
            raise SystemExit(f"找不到文件: {pdf}")
        roots.append(pdf.parent)
        STATE["roots"] = roots
        _load(pdf)
        print(f"已加载: {pdf.name} ({STATE['doc'].page_count} 页)")
    else:
        STATE["roots"] = roots or [Path.cwd().resolve()]

    import uvicorn

    print(f"打开浏览器访问  http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
