# -*- coding: utf-8 -*-
"""UefiAnimWizard 打包入口（PyInstaller 用）。

用独立入口而不是 wizard/__main__.py：后者依赖包上下文（相对导入），
PyInstaller 以脚本方式启动时 `from .cli import main` 会失败。
"""
import sys

from wizard.cli import main

if __name__ == '__main__':
    sys.exit(main())
