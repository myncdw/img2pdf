#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片转 PDF —— 程序入口。

界面在 ui/ 包中，页面计算与渲染在 core.py，配置读写在 config.py。
"""
from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from core import APP_TITLE
from ui.main_window import MainWindow
from ui.theme import STYLE, make_app_icon, make_arrow_icon


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE.replace('__ARROW__', make_arrow_icon()))

    font = QFont()
    families = set(QFontDatabase.families())
    for family in ('Noto Sans CJK SC', 'Source Han Sans SC',
                   'Microsoft YaHei UI', 'Microsoft YaHei',
                   'PingFang SC', 'DejaVu Sans'):
        if family in families:
            font.setFamily(family)
            break
    font.setPointSize(10)
    app.setFont(font)
    app.setWindowIcon(make_app_icon())

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
