"""打包成桌面应用后的启动入口。

与 cli.py 的区别:
- 双击启动, 没有命令行参数, 因此不预加载 PDF, 由用户在界面中选择
- 自动挑选空闲端口并打开浏览器
- Windows 下不弹控制台窗口(由 PyInstaller 的 --windowed 保证)
- 出错时用系统弹窗提示, 而不是打印到不存在的终端
"""

from __future__ import annotations

import os
import multiprocessing
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .paths import default_cache_db, home_roots
from .translate import TransConfig


def _free_port(host: str = "127.0.0.1", start: int = 8011) -> int:
    for p in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    # 全被占用则让系统随机分配
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _open_window(url: str) -> None:
    """优先以浏览器应用模式打开(无地址栏/标签页的独立窗口)。

    浏览器探测逻辑与 HTML 转 PDF 共用 browser 模块;
    找不到浏览器则回退为普通标签页。
    """
    import subprocess

    from .browser import browser_candidates

    if sys.platform == "darwin":
        for app_name in ("Google Chrome", "Microsoft Edge"):
            try:
                subprocess.Popen(
                    ["open", "-na", app_name, "--args", f"--app={url}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except Exception:
                continue
    else:
        for exe in browser_candidates():
            try:
                subprocess.Popen(
                    [exe, f"--app={url}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except Exception:
                continue
    webbrowser.open(url)


def _alert(title: str, msg: str) -> None:
    """尽力用系统弹窗提示错误, 失败则退回标准错误输出。"""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, msg)
        root.destroy()
        return
    except Exception:
        pass
    print(f"{title}: {msg}", file=sys.stderr)


def _ensure_console_streams() -> None:
    """无控制台环境下补全标准流。

    Windows 下以 windowed 方式打包(不弹控制台窗口)时 sys.stdout/sys.stderr
    为 None。uvicorn 默认的彩色日志格式化器会调用 sys.stdout.isatty(),
    进而报 ValueError: Unable to configure formatter 'default'。
    这里把它们指向空设备, 顺便也避免其他库写标准流出错。
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")


# 桌面版日志配置: 只使用标准库的 logging.Formatter,
# 不解析 uvicorn.logging.DefaultFormatter, 彻底规避打包后的日志问题。
LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "default": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stdout",
        },
    },
    "root": {"level": "WARNING", "handlers": ["default"]},
    "loggers": {
        "uvicorn": {"level": "WARNING"},
        "uvicorn.error": {"level": "WARNING"},
        "uvicorn.access": {"level": "WARNING"},
    },
}


def _run_native_window(url: str) -> bool:
    """用 pywebview 开原生窗口。

    返回 True 表示窗口成功运行并已关闭(主线程一直阻塞到用户关窗);
    返回 False 表示原生窗口不可用, 调用方应回退浏览器模式。
    """
    try:
        import webview
    except Exception:
        return False
    try:
        webview.create_window(
            "pdfread",
            url,
            width=1440,
            height=900,
            min_size=(960, 600),
            text_select=True,
        )
        webview.start()
        return True
    except Exception:
        return False


def main() -> None:
    multiprocessing.freeze_support()
    _ensure_console_streams()
    try:
        import uvicorn

        from . import server

        provider = os.environ.get("PDFREAD_PROVIDER", "deepseek")
        cfg = TransConfig(provider=provider)

        cache = default_cache_db()
        cache.parent.mkdir(parents=True, exist_ok=True)

        # 桌面版默认允许访问用户主目录下的常用位置
        server.configure(None, home_roots(), cfg, cache)

        host = "127.0.0.1"
        port = _free_port(host)
        url = f"http://{host}:{port}"

        # 服务跑在后台线程, 主线程交给原生窗口(或浏览器回退)
        config = uvicorn.Config(
            server.app,
            host=host,
            port=port,
            log_level="warning",
            log_config=LOG_CONFIG,
        )
        srv = uvicorn.Server(config)
        srv_thread = threading.Thread(target=srv.run, daemon=True)
        srv_thread.start()

        # 等服务就绪, 避免窗口先打开时连接被拒
        deadline = time.time() + 10
        while not getattr(srv, "started", False) and time.time() < deadline:
            time.sleep(0.05)

        if _run_native_window(url):
            # 原生窗口被用户关闭 -> 停服务退出, 生命周期一致
            srv.should_exit = True
            return

        # 回退: 浏览器应用模式, 主线程挂住等服务
        threading.Timer(0.5, lambda: _open_window(url)).start()
        srv_thread.join()

    except Exception as e:  # 双击启动时没有终端, 必须弹窗
        _alert("pdfread 启动失败", f"{type(e).__name__}: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
