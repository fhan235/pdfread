#!/usr/bin/env bash
# 从 assets/icon.png 生成 macOS 所需的 assets/icon.icns。
# 仅在 macOS 上运行（依赖系统自带的 sips 和 iconutil）。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/assets/icon.png"
SET="$ROOT/assets/icon.iconset"
OUT="$ROOT/assets/icon.icns"

[[ -f "$SRC" ]] || { echo "缺少图标源文件: $SRC" >&2; exit 1; }

rm -rf "$SET"
mkdir -p "$SET"

make() {
  local px="$1" name="$2"
  sips -z "$px" "$px" "$SRC" --out "$SET/$name" >/dev/null
}

make 16 icon_16x16.png
make 32 icon_16x16@2x.png
make 32 icon_32x32.png
make 64 icon_32x32@2x.png
make 128 icon_128x128.png
make 256 icon_128x128@2x.png
make 256 icon_256x256.png
make 512 icon_256x256@2x.png
make 512 icon_512x512.png
make 1024 icon_512x512@2x.png

iconutil -c icns "$SET" -o "$OUT"
rm -rf "$SET"
echo "已生成: $OUT"
