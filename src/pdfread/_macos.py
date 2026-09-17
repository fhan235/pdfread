"""macOS 专用: 隐藏 multiprocessing 子进程的 Dock 图标。

打包成 .app 后, 主进程经 LaunchServices 启动, 其 spawn 子进程
(进程池 worker、resource_tracker)会被 macOS 当作应用实例,
在 Dock 各显示一个图标。

把子进程转为 UIElement(后台)即可消除。必须在子进程启动早期调用 —
— launcher.py 在 freeze_support() 之前调用, 覆盖全部子进程。
"""

from __future__ import annotations

import sys


def hide_dock_icon() -> None:
    """尽力把当前进程从 Dock 移除(仅 macOS 子进程使用)。"""
    if sys.platform != "darwin":
        return

    # 方式一: HIServices TransformProcessType(不需要创建 NSApplication)
    try:
        from ApplicationServices import (
            GetCurrentProcess,
            TransformProcessType,
            kProcessTransformToUIElementApplication,
        )

        ret = GetCurrentProcess()
        # 兼容不同 pyobjc 版本的返回形式
        psn = ret[1] if isinstance(ret, tuple) else ret
        TransformProcessType(psn, kProcessTransformToUIElementApplication)
        return
    except Exception:
        pass

    # 方式二: AppKit 附件策略(等价于 UIElement, pyobjc-Cocoa 必有)
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().setActivationPolicy_(1)  # Accessory
    except Exception:
        pass


def hide_dock_icon_if_multiprocessing_child(argv: list[str]) -> None:
    """仅当本进程是 multiprocessing 子进程时隐藏 Dock 图标。

    spawn 的子进程以 --multiprocessing-fork 参数重启自身,
    主进程的参数里没有该标记, 不受影响。
    """
    if any("multiprocessing" in str(a) for a in argv[1:]):
        hide_dock_icon()
