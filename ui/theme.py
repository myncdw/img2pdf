#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面主题：QSS、图标生成。"""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap

STYLE = """
QWidget {
    color: #dfe5ef;
    font-size: 13px;
}
QWidget#Root { background: #0e1218; }

QFrame#Header { background: #12171f; border-bottom: 1px solid #1f2732; }
QFrame#Footer { background: #12171f; border-top: 1px solid #1f2732; }

QLabel#AppTitle { font-size: 19px; font-weight: 700; color: #f3f6fb; }
QLabel#AppSub { font-size: 11px; color: #7b879a; }
QLabel#Chip {
    background: #171f2b; border: 1px solid #263041; border-radius: 11px;
    padding: 5px 12px; color: #9fb0c9; font-size: 11px;
}
QFrame#Card {
    background: #151b24; border: 1px solid #222b38; border-radius: 14px;
}
QFrame#PreviewBar {
    background: #121821; border-bottom: 1px solid #1f2834;
    border-top-left-radius: 14px; border-top-right-radius: 14px;
}
QLabel#SectionTitle { font-size: 11px; font-weight: 700; color: #8e9cb2; }
QLabel#Hint { font-size: 11px; color: #6f7c90; }
QLabel#Muted { color: #7b879a; }
QLabel#Zoom { color: #8b98ab; font-size: 11px; }

QPushButton {
    background: #1c2430; border: 1px solid #2b3646; border-radius: 9px;
    padding: 7px 14px; color: #d9e0ea;
}
QPushButton:hover { background: #232d3b; border-color: #38465b; }
QPushButton:pressed { background: #18202b; }
QPushButton:disabled { color: #5b6678; background: #161c25; border-color: #222b38; }

QPushButton#Primary {
    background: #4f7dff; border: 1px solid #5b87ff; border-radius: 10px;
    padding: 9px 20px; color: #ffffff; font-weight: 700;
}
QPushButton#Primary:hover { background: #668fff; }
QPushButton#Primary:pressed { background: #4169dd; }
QPushButton#Primary:disabled { background: #2a3345; border-color: #2a3345; color: #6b7789; }

QPushButton#Ghost {
    background: transparent; border: 1px solid transparent; color: #8b98ab;
    padding: 4px 9px; font-size: 11px;
}
QPushButton#Ghost:hover { background: #1d2632; color: #dbe2ec; }
QPushButton#Ghost:disabled { color: #4d5768; background: transparent; }

QComboBox, QLineEdit {
    background: #0f141b; border: 1px solid #28323f; border-radius: 9px;
    padding: 7px 10px; color: #dfe5ef;
    selection-background-color: #4f7dff;
}
QComboBox:hover, QLineEdit:hover { border-color: #37445a; }
QComboBox:focus, QLineEdit:focus { border-color: #4f7dff; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow {
    image: url("__ARROW__"); width: 10px; height: 6px;
}
QComboBox QAbstractItemView {
    background: #141b25; border: 1px solid #2b3646;
    color: #dfe5ef; outline: 0; padding: 4px;
}
QComboBox QAbstractItemView::item { padding: 6px 8px; border-radius: 6px; }
QComboBox QAbstractItemView::item:selected { background: #253352; color: #ffffff; }

QListWidget { background: transparent; border: none; outline: 0; }
QListWidget::item { background: transparent; border: none; margin: 0; }

QToolButton#Nav {
    background: #1b2330; border: 1px solid #28323f; border-radius: 8px;
    color: #aab6c8; font-size: 12px;
}
QToolButton#Nav:hover { background: #232d3b; border-color: #38465b; color: #ffffff; }
QToolButton#Nav:disabled { color: #4d5768; border-color: #222b38; }

QToolButton#Anchor {
    background: #0f141b; border: 1px solid #28323f; border-radius: 8px;
    color: #7f8ca0; font-size: 14px;
}
QToolButton#Anchor:hover { border-color: #38465b; color: #dbe2ec; }
QToolButton#Anchor:checked {
    background: #1a2a4a; border-color: #4f7dff; color: #9dc0ff;
}
QToolButton#Anchor:disabled { color: #3f4859; border-color: #1d2530; }

QSlider::groove:horizontal { height: 4px; background: #232c3a; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #4f7dff; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
    background: #e8edf6; border: 2px solid #4f7dff;
}

QProgressBar { background: #1a222e; border: none; border-radius: 3px; }
QProgressBar::chunk { background: #4f7dff; border-radius: 3px; }

QScrollBar:vertical { background: #0c0f14; width: 10px; margin: 2px; }
QScrollBar::handle:vertical {
    background: #2c3646; border-radius: 5px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #3a475b; }
QScrollBar:horizontal { background: #0c0f14; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: #2c3646; border-radius: 5px; min-width: 30px;
}
QScrollBar::handle:horizontal:hover { background: #3a475b; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QToolTip {
    background: #1b2330; color: #dbe2ec; border: 1px solid #2c3848;
    padding: 5px 8px;
}
"""


def make_arrow_icon() -> str:
    """生成下拉箭头图片供 QSS 使用，返回路径。"""
    path = os.path.join(tempfile.gettempdir(), 'img2pdf_arrow.png')
    if os.path.exists(path):
        return path.replace('\\', '/')
    pm = QPixmap(12, 8)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor('#8b98ab'), 1.6, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPolyline([QPointF(1.5, 2.0), QPointF(6.0, 6.0), QPointF(10.5, 2.0)])
    p.end()
    pm.save(path, 'PNG')
    return path.replace('\\', '/')


def make_app_icon() -> QPixmap:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor('#4f7dff'))
    p.drawRoundedRect(2, 2, 60, 60, 16, 16)
    p.setPen(QColor('#ffffff'))
    f = QFont()
    f.setPointSize(13)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, 'PDF')
    p.end()
    return pm
