# PyInstaller 打包配置
#
# 构建:
#   pyinstaller pdfread.spec
#
# 产物:
#   Windows  dist/pdfread.exe
#   macOS    dist/pdfread.app
#   Linux    dist/pdfread

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

BASE = Path(SPECPATH)
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# 前端资源必须打进去, 运行时通过 sys._MEIPASS 定位
datas = [(str(BASE / "src" / "pdfread" / "static"), "pdfread/static")]

# PyMuPDF 带 C 扩展, 显式收集其动态库
binaries = collect_dynamic_libs("pymupdf")

hiddenimports = [
    "pdfread.server",
    "pdfread.extract",
    "pdfread.translate",
    "pdfread.paths",
    "pdfread.settings",
    "pdfread.app",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "multipart",
]

# 剔除无用大件, 显著减小体积
excludes = [
    "tkinter.test", "test", "unittest", "pydoc_data",
    "numpy", "pandas", "matplotlib", "scipy", "PIL",
    "IPython", "notebook", "setuptools", "pip",
]

a = Analysis(
    [str(BASE / "launcher.py")],
    pathex=[str(BASE / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

# 仅 macOS / Windows 支持应用图标参数；Linux 不传 icon，避免构建器差异。
icon = None
if IS_MAC:
    candidate = BASE / "assets" / "icon.icns"
elif IS_WIN:
    candidate = BASE / "assets" / "icon.ico"
else:
    candidate = None
if candidate and candidate.is_file():
    icon = str(candidate)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pdfread",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 不弹控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=IS_MAC,  # macOS 支持把文件拖到图标上打开
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pdfread",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="pdfread.app",
        icon=icon,
        bundle_identifier="io.github.fhan235.pdfread",
        info_plist={
            "CFBundleName": "pdfread",
            "CFBundleDisplayName": "PDF 对照阅读器",
            "CFBundleShortVersionString": "0.3.5",
            "CFBundleVersion": "0.3.5",
            "NSHighResolutionCapable": True,
            # 后台服务型应用, 不在 Dock 常驻图标可改为 True
            "LSBackgroundOnly": False,
            "LSMinimumSystemVersion": "11.0",
        },
    )
