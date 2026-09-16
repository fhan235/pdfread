#!/usr/bin/env bash
# PDF 双栏对照阅读器 启动脚本
#
# 用法:
#   export DEEPSEEK_API_KEY='sk-...'
#   ./run.sh /path/to/paper.pdf
#   ./run.sh /path/to/paper.pdf --provider silicon --port 8012
#
# 可选: 用 PDFREAD_PYTHON 指定解释器(例如某个 conda 环境)
#   export PDFREAD_PYTHON=/path/to/env/bin/python
#
# 注意: API Key 只通过环境变量传入, 不要写进本文件或提交到仓库。

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PDFREAD_PYTHON:-python3}"

if [[ $# -lt 1 ]]; then
  echo "用法: $0 <PDF路径> [其他参数...]"
  echo "例如: $0 ~/papers/paper.pdf --provider deepseek --port 8011"
  exit 1
fi

PDF="$1"; shift

if [[ ! -f "$PDF" ]]; then
  echo "找不到文件: $PDF" >&2
  exit 1
fi

# 若使用 conda 环境, 确保其 lib 优先于系统库(规避 libstdc++ 版本冲突)
PY_PREFIX="$("$PY" -c 'import sys; print(sys.prefix)')"
if [[ -d "$PY_PREFIX/lib" ]]; then
  export LD_LIBRARY_PATH="$PY_PREFIX/lib:${LD_LIBRARY_PATH:-}"
fi

exec "$PY" "$APP_DIR/server.py" --pdf "$PDF" "$@"
