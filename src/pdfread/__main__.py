"""支持 python -m pdfread 方式启动。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
