"""命令行入口。

    pdfread paper.pdf
    pdfread                      # 不带文件, 在界面里选
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
from .paths import default_cache_db, default_roots
from .translate import PROVIDERS, TransConfig


def _free_port(host: str, port: int, tries: int = 30) -> int:
    for i in range(tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port + i))
                return port + i
            except OSError:
                continue
    raise SystemExit(f"{host}:{port} 起连续 {tries} 个端口均被占用")


def build_parser():
    import argparse

    ap = argparse.ArgumentParser(
        prog="pdfread",
        description="PDF 双栏对照翻译阅读器 —— 左栏原文, 右栏流式中文译文",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "环境变量:\n"
            "  DEEPSEEK_API_KEY / SILICON_API_KEY / DASHSCOPE_API_KEY /\n"
            "  OPENAI_API_KEY      对应各服务的密钥\n"
            "  PDFREAD_ROOTS       允许访问的目录(路径分隔符分隔)\n"
            "  PDFREAD_CACHE_DIR   缓存目录\n"
        ),
    )
    ap.add_argument("pdf", nargs="?", help="要阅读的 PDF(可省略, 在界面中选择)")
    ap.add_argument("--pdf", dest="pdf_opt", help="同上(兼容旧写法)")
    ap.add_argument("--root", action="append", default=[],
                    help="追加允许访问的目录(可多次指定)")
    ap.add_argument("--provider",
                    default=os.environ.get("PDFREAD_PROVIDER", "deepseek"),
                    choices=sorted(PROVIDERS), help="翻译服务")
    ap.add_argument("--model", default="", help="覆盖默认模型名")
    ap.add_argument("--base-url", default="", help="覆盖默认 API 地址")
    ap.add_argument("--concurrency", type=int, default=8, help="并发请求数")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址")
    ap.add_argument("--port", type=int, default=8011, help="监听端口(占用则顺延)")
    ap.add_argument("--cache", default="", help="翻译缓存数据库路径")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    ap.add_argument("--version", action="version", version=f"pdfread {__version__}")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    target = args.pdf or args.pdf_opt

    from . import server

    cfg = TransConfig(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        concurrency=args.concurrency,
    )

    roots = [
        Path(r).expanduser().resolve()
        for r in args.root
        if Path(r).expanduser().is_dir()
    ]
    pdf: Path | None = None
    if target:
        pdf = Path(target).expanduser().resolve()
        if not pdf.is_file():
            raise SystemExit(f"找不到文件: {pdf}")
        roots.append(pdf.parent)
    if not roots:
        roots = default_roots()

    seen, uniq = set(), []
    for r in roots:
        if r not in seen:
            seen.add(r)
            uniq.append(r)

    cache = Path(args.cache).expanduser() if args.cache else default_cache_db()
    cache.parent.mkdir(parents=True, exist_ok=True)

    server.configure(pdf, uniq, cfg, cache)

    port = _free_port(args.host, args.port)
    shown = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    url = f"http://{shown}:{port}"

    if pdf is not None:
        print(f"已加载  {pdf.name}  ({server.STATE['doc'].page_count} 页)")
    else:
        print("未指定 PDF, 请在页面中选择或拖入文件")
    try:
        _, model, _ = cfg.resolve()
        print(f"翻译服务  {cfg.provider} / {model}")
    except Exception as e:
        print(f"提示  {e}")
    print(f"缓存    {cache}")
    print(f"访问    {url}")

    if args.open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    import uvicorn

    uvicorn.run(server.app, host=args.host, port=port, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
