"""PyInstaller 打包入口。

不能直接用 src/pdfread/app.py 作为入口脚本 —— 那样它会以顶层脚本身份运行,
包内的相对导入(from .paths import ...)会失败。
这里以绝对导入方式调用, 保证打包后行为与正常安装一致。
"""

from pdfread.app import main

if __name__ == "__main__":
    main()
