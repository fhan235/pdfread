"""PyInstaller 打包入口。

不能直接用 src/pdfread/app.py 作为入口脚本 —— 那样它会以顶层脚本身份运行,
包内的相对导入(from .paths import ...)会失败。
这里以绝对导入方式调用, 保证打包后行为与正常安装一致。
"""

if __name__ == "__main__":
    import multiprocessing
    import sys

    # macOS: multiprocessing 子进程(worker / resource_tracker)会重新
    # 执行本入口, 在 freeze_support 接管前先把它们的 Dock 图标隐藏。
    # freeze_support 对子进程不会返回, 因此这之后的代码只在主进程执行。
    from pdfread._macos import hide_dock_icon_if_multiprocessing_child

    hide_dock_icon_if_multiprocessing_child(sys.argv)
    multiprocessing.freeze_support()

    from pdfread.app import main
    main()
