#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主窗口：只负责界面与交互，页面计算/渲染调用 core.py。"""
from __future__ import annotations

import os
import traceback
from dataclasses import asdict

try:  # 优先使用 xdg_dialogs；缺失时回退到 Qt 对话框
    from xdg_dialogs import choose_folder as _xdg_choose_folder
    from xdg_dialogs import choose_save_file as _xdg_choose_save_file
except Exception:  # pragma: no cover - 依赖缺失时的兜底
    _xdg_choose_folder = None
    _xdg_choose_save_file = None

from PySide6.QtCore import QEvent, QSize, QThread, QUrl, Qt
from PySide6.QtGui import (QDesktopServices, QDragEnterEvent, QDropEvent,
                           QIntValidator, QKeySequence, QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidgetItem, QMainWindow,
                               QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QSlider, QToolButton, QVBoxLayout,
                               QWidget)

from config import load_config, save_config
from core import (ANCHORS, APP_TITLE, DEFAULT_SETTINGS, DPI_PRESETS,
                  IMAGE_EXTS, ImageInfo, Page, Settings, compute_pages,
                  file_ext, natural_key, probe_image, settings_from_dict)
from .widgets import (ROLE_ERROR, ROLE_PATH, ROLE_SIZE, ConvertWorker,
                      FileDelegate, OrderList, PageCanvas, PreviewScroll,
                      load_thumb)


def _read_int(edit: QLineEdit, default: int) -> int:
    text = edit.text().strip()
    try:
        value = int(text)
    except ValueError:
        return default
    return value if value > 0 else default


def _select_combo_data(combo: QComboBox, value: object) -> None:
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 900)
        self.setMinimumSize(1040, 700)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

        # 状态
        self._config = load_config()
        self.infos: list[ImageInfo] = []
        self.pages: list[Page] = []
        self.page_index = 0
        self.folder = ''
        self.busy = False
        self.anchor = 'center'
        self.last_dir = ''

        self._thumb_cache: dict[str, QPixmap] = {}
        self._syncing = False
        self._refreshing = False

        self._thread: QThread | None = None
        self._worker: ConvertWorker | None = None

        self._build_ui()
        self._install_shortcuts()

        self.preview_scroll.viewport().installEventFilter(self)
        self.preview_scroll.verticalScrollBar().valueChanged.connect(
            self._sync_viewport)
        self.preview_scroll.verticalScrollBar().rangeChanged.connect(
            lambda *_: self._sync_viewport())

        self._apply_config()
        self._on_settings_changed()
        self._save_config()

    # ------------------------------------------------------------------
    # 界面构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName('Root')
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(18, 16, 18, 14)
        body_lay.setSpacing(14)
        body_lay.addWidget(self._build_files_panel())
        body_lay.addWidget(self._build_preview_panel(), 1)
        body_lay.addWidget(self._build_settings_panel())
        outer.addWidget(body, 1)

        outer.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName('Header')
        lay = QHBoxLayout(header)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(14)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel(APP_TITLE)
        title.setObjectName('AppTitle')
        sub = QLabel('连续预览 · 每页高度可随图片 · 批量合并')
        sub.setObjectName('AppSub')
        title_col.addWidget(title)
        title_col.addWidget(sub)
        lay.addLayout(title_col)
        lay.addStretch(1)

        self.folder_chip = QLabel('未选择文件夹')
        self.folder_chip.setObjectName('Chip')
        lay.addWidget(self.folder_chip)

        self.choose_btn = QPushButton('选择文件夹')
        self.choose_btn.clicked.connect(self._choose_folder)
        lay.addWidget(self.choose_btn)

        self.generate_btn = QPushButton('生成 PDF')
        self.generate_btn.setObjectName('Primary')
        self.generate_btn.clicked.connect(self._generate)
        lay.addWidget(self.generate_btn)
        return header

    def _build_files_panel(self) -> QWidget:
        card = QFrame()
        card.setObjectName('Card')
        card.setFixedWidth(302)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel('图片顺序')
        title.setObjectName('SectionTitle')
        head.addWidget(title)
        head.addStretch(1)
        self.count_label = QLabel('0 张')
        self.count_label.setObjectName('Muted')
        head.addWidget(self.count_label)
        lay.addLayout(head)

        self.list = OrderList()
        self.list.setItemDelegate(FileDelegate(self.list, self._thumb))
        self.list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setMouseTracking(True)
        self.list.viewport().setAttribute(
            Qt.WidgetAttribute.WA_Hover, True)
        self.list.currentRowChanged.connect(self._on_row_changed)
        self.list.orderChanged.connect(self._on_order_changed)
        lay.addWidget(self.list, 1)

        hint = QLabel('拖动条目调整顺序 · 数字按自然排序')
        hint.setObjectName('Hint')
        lay.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        for text, slot in (('上移', self._move_up),
                           ('下移', self._move_down),
                           ('移除', self._remove_selected)):
            btn = QPushButton(text)
            btn.setObjectName('Ghost')
            btn.clicked.connect(slot)
            buttons.addWidget(btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        return card

    def _build_preview_panel(self) -> QWidget:
        card = QFrame()
        card.setObjectName('Card')
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QFrame()
        bar.setObjectName('PreviewBar')
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(14, 9, 14, 9)
        bar_lay.setSpacing(8)

        title = QLabel('预览')
        title.setObjectName('SectionTitle')
        bar_lay.addWidget(title)
        bar_lay.addSpacing(6)

        self.prev_btn = QToolButton()
        self.prev_btn.setText('◀')
        self.prev_btn.setObjectName('Nav')
        self.prev_btn.setFixedSize(28, 26)
        self.prev_btn.setToolTip('上一页')
        self.prev_btn.clicked.connect(lambda: self._step_page(-1))
        bar_lay.addWidget(self.prev_btn)

        self.page_label = QLabel('第 0 / 0 页')
        self.page_label.setStyleSheet('color: #cfd8e5; font-weight: 600;')
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setMinimumWidth(84)
        bar_lay.addWidget(self.page_label)

        self.next_btn = QToolButton()
        self.next_btn.setText('▶')
        self.next_btn.setObjectName('Nav')
        self.next_btn.setFixedSize(28, 26)
        self.next_btn.setToolTip('下一页')
        self.next_btn.clicked.connect(lambda: self._step_page(1))
        bar_lay.addWidget(self.next_btn)

        bar_lay.addStretch(1)

        self.page_info = QLabel('—')
        self.page_info.setStyleSheet('color: #7b879a; font-size: 11px;')
        bar_lay.addWidget(self.page_info)
        bar_lay.addSpacing(8)

        zoom_out = QToolButton()
        zoom_out.setText('−')
        zoom_out.setObjectName('Nav')
        zoom_out.setFixedSize(26, 26)
        zoom_out.setToolTip('缩小（Ctrl + 滚轮）')
        zoom_out.clicked.connect(lambda: self._zoom_by(1 / 1.2))
        bar_lay.addWidget(zoom_out)

        self.zoom_label = QLabel('100%')
        self.zoom_label.setObjectName('Zoom')
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setFixedWidth(44)
        self.zoom_label.setToolTip('预览显示比例（相对原始像素）')
        bar_lay.addWidget(self.zoom_label)

        zoom_in = QToolButton()
        zoom_in.setText('+')
        zoom_in.setObjectName('Nav')
        zoom_in.setFixedSize(26, 26)
        zoom_in.setToolTip('放大（Ctrl + 滚轮）')
        zoom_in.clicked.connect(lambda: self._zoom_by(1.2))
        bar_lay.addWidget(zoom_in)

        fit_btn = QPushButton('适宽')
        fit_btn.setObjectName('Ghost')
        fit_btn.setToolTip('恢复到适应宽度')
        fit_btn.clicked.connect(lambda: self._set_zoom(1.0))
        bar_lay.addWidget(fit_btn)

        lay.addWidget(bar)

        self.canvas = PageCanvas()
        self.canvas.currentPageChanged.connect(self._on_canvas_page_changed)
        self.canvas.pageClicked.connect(self._on_page_clicked)

        self.preview_scroll = PreviewScroll()
        self.preview_scroll.setWidget(self.canvas)
        self.preview_scroll.setWidgetResizable(False)
        self.preview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.preview_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preview_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preview_scroll.setStyleSheet(
            'QScrollArea { background: #0c0f14; border: none; }')
        self.preview_scroll.viewport().setStyleSheet('background: #0c0f14;')
        self.preview_scroll.zoomRequested.connect(self._zoom_by)
        lay.addWidget(self.preview_scroll, 1)
        return card

    def _build_settings_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedWidth(368)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            'QScrollArea { background: transparent; border: none; }')

        content = QWidget()
        content.setStyleSheet('background: transparent;')
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(12)
        lay.addWidget(self._build_size_card())
        lay.addWidget(self._build_layout_card())
        lay.addWidget(self._build_output_card())
        lay.addWidget(self._build_config_card())
        lay.addStretch(1)
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _new_card(title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName('Card')
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)
        label = QLabel(title)
        label.setObjectName('SectionTitle')
        lay.addWidget(label)
        return card, lay

    def _build_size_card(self) -> QWidget:
        card, lay = self._new_card('页面尺寸')

        self.size_mode = QComboBox()
        self.size_mode.addItem('跟随每张图片（原尺寸）', 'per_image')
        self.size_mode.addItem('跟随第一张图片', 'first')
        self.size_mode.addItem('最宽图片（宽度统一，高度随图片）', 'max')
        self.size_mode.addItem('A4 纸张', 'a4')
        self.size_mode.addItem('自定义宽度', 'custom')
        self.size_mode.currentIndexChanged.connect(self._on_settings_changed)
        lay.addWidget(self.size_mode)

        # A4 方向
        self.a4_row = QWidget()
        a4_lay = QHBoxLayout(self.a4_row)
        a4_lay.setContentsMargins(0, 0, 0, 0)
        a4_lay.setSpacing(8)
        a4_label = QLabel('方向')
        a4_label.setStyleSheet('color: #8794a8; font-size: 12px;')
        a4_label.setMinimumWidth(64)
        self.a4_orient = QComboBox()
        self.a4_orient.addItem('纵向（210 × 297 mm）', False)
        self.a4_orient.addItem('横向（297 × 210 mm）', True)
        self.a4_orient.currentIndexChanged.connect(self._on_settings_changed)
        a4_lay.addWidget(a4_label)
        a4_lay.addWidget(self.a4_orient, 1)
        lay.addWidget(self.a4_row)

        # 自定义宽度
        self.custom_row = QWidget()
        custom_lay = QVBoxLayout(self.custom_row)
        custom_lay.setContentsMargins(0, 0, 0, 0)
        custom_lay.setSpacing(8)

        width_row = QHBoxLayout()
        width_row.setSpacing(8)
        width_label = QLabel('宽度')
        width_label.setStyleSheet('color: #8794a8; font-size: 12px;')
        width_label.setMinimumWidth(64)
        self.width_edit = QLineEdit('1240')
        self.width_edit.setValidator(QIntValidator(1, 100000, self))
        self.width_edit.setFixedWidth(96)
        self.width_edit.textChanged.connect(self._on_settings_changed)
        width_unit = QLabel('px')
        width_unit.setObjectName('Muted')
        width_row.addWidget(width_label)
        width_row.addWidget(self.width_edit)
        width_row.addWidget(width_unit)
        width_row.addStretch(1)
        custom_lay.addLayout(width_row)

        height_row = QHBoxLayout()
        height_row.setSpacing(8)
        height_label = QLabel('高度')
        height_label.setStyleSheet('color: #8794a8; font-size: 12px;')
        height_label.setMinimumWidth(64)
        self.height_mode = QComboBox()
        self.height_mode.addItem('随图片比例（按宽度缩放）', 'ratio')
        self.height_mode.addItem('保持图片原始高度', 'original')
        self.height_mode.addItem('固定高度', 'fixed')
        self.height_mode.currentIndexChanged.connect(self._on_settings_changed)
        height_row.addWidget(height_label)
        height_row.addWidget(self.height_mode, 1)
        custom_lay.addLayout(height_row)

        self.custom_height_row = QWidget()
        fixed_row = QHBoxLayout(self.custom_height_row)
        fixed_row.setContentsMargins(0, 0, 0, 0)
        fixed_row.setSpacing(8)
        fixed_label = QLabel('固定')
        fixed_label.setStyleSheet('color: #8794a8; font-size: 12px;')
        fixed_label.setMinimumWidth(64)
        self.height_edit = QLineEdit('1754')
        self.height_edit.setValidator(QIntValidator(1, 100000, self))
        self.height_edit.setFixedWidth(96)
        self.height_edit.textChanged.connect(self._on_settings_changed)
        fixed_unit = QLabel('px')
        fixed_unit.setObjectName('Muted')
        fixed_row.addWidget(fixed_label)
        fixed_row.addWidget(self.height_edit)
        fixed_row.addWidget(fixed_unit)
        fixed_row.addStretch(1)
        custom_lay.addWidget(self.custom_height_row)

        lay.addWidget(self.custom_row)

        self.size_hint = QLabel('')
        self.size_hint.setObjectName('Hint')
        self.size_hint.setWordWrap(True)
        lay.addWidget(self.size_hint)
        return card

    def _build_layout_card(self) -> QWidget:
        card, lay = self._new_card('图片布局')

        self.fit_mode = QComboBox()
        self.fit_mode.addItem('适应页内（完整显示）', 'contain')
        self.fit_mode.addItem('原始大小（超出裁剪）', 'original')
        self.fit_mode.addItem('拉伸填满（可能变形）', 'stretch')
        self.fit_mode.currentIndexChanged.connect(self._on_settings_changed)
        lay.addWidget(self.fit_mode)

        align_label = QLabel('对齐方式')
        align_label.setStyleSheet('color: #8794a8; font-size: 12px;')
        lay.addWidget(align_label)

        grid_wrap = QHBoxLayout()
        grid_wrap.addStretch(1)
        grid = QGridLayout()
        grid.setSpacing(6)
        self._anchor_group = QButtonGroup(self)
        self._anchor_group.setExclusive(True)
        self._anchor_buttons: list[QToolButton] = []
        for i, (anchor, glyph) in enumerate(ANCHORS):
            btn = QToolButton()
            btn.setText(glyph)
            btn.setObjectName('Anchor')
            btn.setCheckable(True)
            btn.setFixedSize(40, 32)
            btn.setToolTip(anchor)
            self._anchor_group.addButton(btn, i)
            self._anchor_buttons.append(btn)
            grid.addWidget(btn, i // 3, i % 3)
        self._anchor_group.button(4).setChecked(True)
        self._anchor_group.idClicked.connect(self._on_anchor_clicked)
        grid_wrap.addLayout(grid)
        grid_wrap.addStretch(1)
        lay.addLayout(grid_wrap)

        margin_row = QHBoxLayout()
        margin_row.setSpacing(10)
        margin_label = QLabel('页边距')
        margin_label.setStyleSheet('color: #8794a8; font-size: 12px;')
        margin_label.setMinimumWidth(64)
        self.margin_slider = QSlider(Qt.Orientation.Horizontal)
        self.margin_slider.setRange(0, 120)
        self.margin_slider.setSingleStep(2)
        self.margin_slider.setPageStep(10)
        self.margin_slider.valueChanged.connect(self._on_margin_changed)
        self.margin_label = QLabel('0 px')
        self.margin_label.setObjectName('Muted')
        self.margin_label.setFixedWidth(48)
        self.margin_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                       | Qt.AlignmentFlag.AlignVCenter)
        margin_row.addWidget(margin_label)
        margin_row.addWidget(self.margin_slider, 1)
        margin_row.addWidget(self.margin_label)
        lay.addLayout(margin_row)

        self.layout_hint = QLabel('拉伸模式会忽略对齐方式。')
        self.layout_hint.setObjectName('Hint')
        self.layout_hint.setWordWrap(True)
        self.layout_hint.setVisible(False)
        lay.addWidget(self.layout_hint)
        return card

    def _build_output_card(self) -> QWidget:
        card, lay = self._new_card('输出')

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText('选择 PDF 保存位置…')
        path_row.addWidget(self.output_edit, 1)
        browse = QPushButton('浏览')
        browse.setObjectName('Ghost')
        browse.clicked.connect(self._choose_output)
        path_row.addWidget(browse)
        lay.addLayout(path_row)

        dpi_row = QHBoxLayout()
        dpi_row.setSpacing(8)
        dpi_label = QLabel('分辨率')
        dpi_label.setStyleSheet('color: #8794a8; font-size: 12px;')
        dpi_label.setMinimumWidth(64)
        self.dpi_combo = QComboBox()
        for dpi in DPI_PRESETS:
            self.dpi_combo.addItem(f'{dpi} DPI', dpi)
        self.dpi_combo.setCurrentIndex(DPI_PRESETS.index(300))
        self.dpi_combo.currentIndexChanged.connect(self._on_settings_changed)
        dpi_row.addWidget(dpi_label)
        dpi_row.addWidget(self.dpi_combo, 1)
        lay.addLayout(dpi_row)

        hint = QLabel('DPI 同时决定 A4 的像素尺寸和 PDF 的物理纸张大小。')
        hint.setObjectName('Hint')
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return card

    def _build_config_card(self) -> QWidget:
        card, lay = self._new_card('默认路径')

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.default_dir_edit = QLineEdit()
        self.default_dir_edit.setPlaceholderText('留空：使用上一次选择的位置')
        self.default_dir_edit.setToolTip(
            '文件对话框的起始目录；留空时使用上一次选择的位置')
        self.default_dir_edit.editingFinished.connect(self._save_config)
        path_row.addWidget(self.default_dir_edit, 1)
        pick = QPushButton('浏览')
        pick.setObjectName('Ghost')
        pick.clicked.connect(self._browse_default_dir)
        path_row.addWidget(pick)
        lay.addLayout(path_row)

        hint = QLabel('默认路径为空时，文件对话框会打开上一次选择的位置。')
        hint.setObjectName('Hint')
        hint.setWordWrap(True)
        lay.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        reset = QPushButton('恢复默认')
        reset.setObjectName('Ghost')
        reset.setToolTip('清空默认路径与上次目录，并重置全部设置和已选图片')
        reset.clicked.connect(self._restore_defaults)
        buttons.addWidget(reset)
        lay.addLayout(buttons)
        return card

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName('Footer')
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(20, 9, 20, 9)
        lay.setSpacing(12)

        self.status = QLabel('就绪 · 选择文件夹或拖入图片')
        self.status.setObjectName('Muted')
        self.status.setStyleSheet('color: #7b879a; font-size: 11px;')
        lay.addWidget(self.status, 1)

        self.progress = QProgressBar()
        self.progress.setFixedWidth(200)
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        return footer

    def _install_shortcuts(self) -> None:
        open_shortcut = QShortcut(QKeySequence('Ctrl+O'), self)
        open_shortcut.activated.connect(self._choose_folder)
        save_shortcut = QShortcut(QKeySequence('Ctrl+G'), self)
        save_shortcut.activated.connect(self._generate)

    # ------------------------------------------------------------------
    # 配置（config.json）
    # ------------------------------------------------------------------
    def _apply_config(self) -> None:
        self._apply_settings_to_ui(
            settings_from_dict(self._config.get('settings')))
        default_dir = self._config.get('default_dir')
        if isinstance(default_dir, str):
            self.default_dir_edit.setText(default_dir)
        last_dir = self._config.get('last_dir')
        if isinstance(last_dir, str) and os.path.isdir(last_dir):
            self.last_dir = last_dir

    def _apply_settings_to_ui(self, s: Settings) -> None:
        _select_combo_data(self.size_mode, s.size_mode)
        self.width_edit.setText(str(s.custom_width))
        _select_combo_data(self.height_mode, s.custom_height_mode)
        self.height_edit.setText(str(s.custom_height))
        _select_combo_data(self.a4_orient, s.a4_landscape)
        _select_combo_data(self.fit_mode, s.fit_mode)

        self.anchor = s.anchor
        index = next((i for i, (anchor, _) in enumerate(ANCHORS)
                      if anchor == s.anchor), 4)
        button = self._anchor_group.button(index)
        if button is not None:
            button.setChecked(True)

        self.margin_slider.setValue(s.margin)
        _select_combo_data(self.dpi_combo, s.dpi)

    def _save_config(self) -> None:
        save_config({
            'default_dir': self.default_dir_edit.text().strip(),
            'last_dir': self.last_dir,
            'settings': asdict(self.settings()),
        })

    def _dialog_start_dir(self) -> str:
        for candidate in (self.default_dir_edit.text().strip(), self.last_dir):
            if not candidate:
                continue
            folder = os.path.expanduser(candidate)
            if os.path.isdir(folder):
                return folder
        return os.path.expanduser('~')

    def _browse_default_dir(self) -> None:
        folder = self._pick_folder('选择默认路径', self._dialog_start_dir())
        if folder:
            self.default_dir_edit.setText(folder)
            self._save_config()

    def _restore_defaults(self) -> None:
        if self.busy:
            return
        ret = QMessageBox.question(
            self, APP_TITLE,
            '将清空默认路径与上次目录，并重置全部设置和已选图片。\n\n是否继续？')
        if ret != QMessageBox.StandardButton.Yes:
            return

        self.default_dir_edit.clear()
        self.infos = []
        self.pages = []
        self.folder = ''
        self.page_index = 0
        self.last_dir = ''
        self.output_edit.clear()
        self._thumb_cache.clear()
        self.canvas.set_zoom(1.0)
        self._apply_settings_to_ui(DEFAULT_SETTINGS)
        self._rebuild_list()
        self._recompute_pages()
        self._set_status('已恢复默认设置')
        self._save_config()

    # ------------------------------------------------------------------
    # 设置读取
    # ------------------------------------------------------------------
    def settings(self) -> Settings:
        return Settings(
            size_mode=str(self.size_mode.currentData()),
            custom_width=_read_int(self.width_edit, 1240),
            custom_height_mode=str(self.height_mode.currentData()),
            custom_height=_read_int(self.height_edit, 1754),
            a4_landscape=bool(self.a4_orient.currentData()),
            fit_mode=str(self.fit_mode.currentData()),
            anchor=self.anchor,
            margin=int(self.margin_slider.value()),
            dpi=int(self.dpi_combo.currentData()),
        )

    def _on_settings_changed(self, *_args) -> None:
        s = self.settings()
        self.a4_row.setVisible(s.size_mode == 'a4')
        self.custom_row.setVisible(s.size_mode == 'custom')
        self.custom_height_row.setVisible(
            s.size_mode == 'custom' and s.custom_height_mode == 'fixed')

        stretch = s.fit_mode == 'stretch'
        for btn in self._anchor_buttons:
            btn.setEnabled(not stretch)
        self.layout_hint.setVisible(stretch)

        self._recompute_pages()

    def _on_margin_changed(self, value: int) -> None:
        self.margin_label.setText(f'{value} px')
        self._on_settings_changed()

    def _on_anchor_clicked(self, index: int) -> None:
        if 0 <= index < len(ANCHORS):
            self.anchor = ANCHORS[index][0]
            self._on_settings_changed()

    # ------------------------------------------------------------------
    # 预览视口同步
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event) -> bool:
        if obj is self.preview_scroll.viewport() \
                and event.type() == QEvent.Type.Resize:
            self._sync_viewport()
        return super().eventFilter(obj, event)

    def _sync_viewport(self, *_args) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            vbar = self.preview_scroll.verticalScrollBar()
            viewport = self.preview_scroll.viewport()
            self.canvas.set_viewport(vbar.value(), viewport.height(),
                                     viewport.width())
            self._update_zoom_label()
        finally:
            self._syncing = False

    def _refresh_canvas(self) -> None:
        target = self.page_index
        self._refreshing = True
        try:
            self.canvas.set_document(self.pages, self.settings())
            self.canvas.set_current(target)
            self._sync_viewport()
            self.preview_scroll.verticalScrollBar().setValue(
                self.canvas.page_top(target))
        finally:
            self._refreshing = False
        self._sync_page_labels()
        self._update_zoom_label()

    def _on_canvas_page_changed(self, index: int) -> None:
        if self._refreshing:
            return
        if not (0 <= index < len(self.pages)):
            return
        if index == self.page_index:
            return
        self.page_index = index
        self._sync_page_labels()
        self._sync_list_selection()
        self._update_zoom_label()

    def _on_page_clicked(self, index: int) -> None:
        self._go_to_page(index, scroll=False)

    def _go_to_page(self, index: int, scroll: bool = True) -> None:
        if not self.pages:
            return
        index = max(0, min(index, len(self.pages) - 1))
        self.page_index = index
        self.canvas.set_current(index)
        self._sync_page_labels()
        self._sync_list_selection()
        self._update_zoom_label()
        if scroll:
            self.preview_scroll.verticalScrollBar().setValue(
                self.canvas.page_top(index))

    def _step_page(self, delta: int) -> None:
        if not self.pages:
            return
        self._go_to_page(self.page_index + delta)

    def _zoom_by(self, factor: float) -> None:
        self._set_zoom(self.canvas.zoom() * factor)

    def _set_zoom(self, zoom: float) -> None:
        target = self.page_index
        self._refreshing = True
        try:
            self.canvas.set_zoom(zoom)
            self.canvas.set_current(target)
            self._sync_viewport()
            self.preview_scroll.verticalScrollBar().setValue(
                self.canvas.page_top(target))
        finally:
            self._refreshing = False
        self._sync_page_labels()
        self._update_zoom_label()

    def _update_zoom_label(self) -> None:
        scale = self.canvas.page_scale(self.page_index)
        self.zoom_label.setText(f'{scale * 100:.0f}%' if scale else '—')

    # ------------------------------------------------------------------
    # 图片加载与排序
    # ------------------------------------------------------------------
    def _pick_folder(self, title: str, initial: str) -> str:
        start = os.path.expanduser(initial) if initial else ''
        if start and not os.path.isdir(start):
            start = ''
        if _xdg_choose_folder is not None:
            try:
                path = _xdg_choose_folder(title, initialdir=start or None)
                if path:
                    return path
                return ''
            except Exception:
                traceback.print_exc()
        return QFileDialog.getExistingDirectory(
            self, title, start or os.path.expanduser('~'))

    def _pick_save(self, title: str, default_name: str, initial: str) -> str:
        start = os.path.expanduser(initial) if initial else ''
        if start and not os.path.isdir(start):
            start = ''
        if _xdg_choose_save_file is not None:
            try:
                path = _xdg_choose_save_file(
                    title, default_name=default_name,
                    initialdir=start or None)
                if path:
                    return path
                return ''
            except Exception:
                traceback.print_exc()
        path, _ = QFileDialog.getSaveFileName(
            self, title, os.path.join(start or os.path.expanduser('~'),
                                      default_name),
            'PDF 文件 (*.pdf)')
        return path

    def _choose_folder(self) -> None:
        if self.busy:
            return
        folder = self._pick_folder('选择图片文件夹', self._dialog_start_dir())
        if not folder:
            return
        self._load_folder(folder)

    def _load_folder(self, folder: str) -> None:
        try:
            names = sorted(os.listdir(folder), key=natural_key)
        except OSError as exc:
            self._warn(f'无法读取文件夹：\n{exc}')
            return

        paths = []
        for name in names:
            path = os.path.join(folder, name)
            if file_ext(name) in IMAGE_EXTS and os.path.isfile(path):
                paths.append(path)
        if not paths:
            self._warn('该文件夹中没有支持的图片。')
            return

        infos = [probe_image(p) for p in paths]
        self.folder = folder
        self.last_dir = folder
        self.output_edit.setText(os.path.join(folder, 'output.pdf'))
        self._apply_infos(infos)
        self._save_config()

    def _add_paths(self, paths: list[str]) -> None:
        folders = [p for p in paths if os.path.isdir(p)]
        if folders:
            self.last_dir = folders[0]
            self._load_folder(folders[0])
            return

        existing = {info.path for info in self.infos}
        new_infos: list[ImageInfo] = []
        for path in sorted(paths, key=natural_key):
            if path in existing:
                continue
            if os.path.isfile(path) and file_ext(path) in IMAGE_EXTS:
                new_infos.append(probe_image(path))
        if not new_infos:
            return

        if not self.infos:
            self.folder = ''
            if not self.output_edit.text().strip():
                self.output_edit.setText(
                    os.path.join(os.path.dirname(new_infos[0].path),
                                 'output.pdf'))
        self._apply_infos(self.infos + new_infos)

    def _apply_infos(self, infos: list[ImageInfo]) -> None:
        current = self._current_path()
        self.infos = infos
        self.page_index = 0
        self._rebuild_list(current)
        self._recompute_pages(keep_path=current)
        self._update_folder_chip()
        if not infos:
            self.canvas.set_empty_text('还没有图片。\n\n'
                                      '点击右上角「选择文件夹」，\n'
                                      '或把图片 / 文件夹拖到这里。')

    def _rebuild_list(self, select_path: str | None = None) -> None:
        current = select_path or self._current_path()
        self.list.blockSignals(True)
        self.list.clear()
        for info in self.infos:
            item = QListWidgetItem()
            item.setData(ROLE_PATH, info.path)
            item.setData(Qt.ItemDataRole.DisplayRole, info.name)
            item.setData(ROLE_SIZE, info.size)
            item.setData(ROLE_ERROR, info.error)
            item.setToolTip(info.error or info.name)
            item.setSizeHint(QSize(0, 62))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled
                          | Qt.ItemFlag.ItemIsSelectable
                          | Qt.ItemFlag.ItemIsDragEnabled
                          | Qt.ItemFlag.ItemIsDropEnabled)
            self.list.addItem(item)
        if current:
            for row, info in enumerate(self.infos):
                if info.path == current:
                    self.list.setCurrentRow(row)
                    break
        elif self.infos:
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        self.count_label.setText(f'{len(self.infos)} 张')

    def _thumb(self, path: str) -> QPixmap:
        cached = self._thumb_cache.get(path)
        if cached is not None:
            return cached
        pixmap = load_thumb(path)
        self._thumb_cache[path] = pixmap
        return pixmap

    def _on_order_changed(self) -> None:
        order = [self.list.item(i).data(ROLE_PATH)
                 for i in range(self.list.count())]
        by_path = {info.path: info for info in self.infos}
        new_infos = [by_path[p] for p in order if p in by_path]
        if len(new_infos) != len(self.infos):
            self._rebuild_list()
            return

        row = self.list.currentRow()
        selected_path = None
        if 0 <= row < self.list.count():
            selected_path = self.list.item(row).data(ROLE_PATH)
        current = selected_path or self._current_path()
        self.infos = new_infos
        self._recompute_pages(keep_path=current)

    def _move_up(self) -> None:
        row = self.list.currentRow()
        if row <= 0 or row >= len(self.infos):
            return
        path = self.infos[row].path
        self.infos[row - 1], self.infos[row] = self.infos[row], self.infos[row - 1]
        self._rebuild_list(path)
        self._recompute_pages(keep_path=path)

    def _move_down(self) -> None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.infos) - 1:
            return
        path = self.infos[row].path
        self.infos[row + 1], self.infos[row] = self.infos[row], self.infos[row + 1]
        self._rebuild_list(path)
        self._recompute_pages(keep_path=path)

    def _remove_selected(self) -> None:
        row = self.list.currentRow()
        if not (0 <= row < len(self.infos)):
            return
        path = self.infos[row].path
        self.infos = self.infos[:row] + self.infos[row + 1:]
        if not self.infos:
            self.folder = ''
        self._rebuild_list()
        self._recompute_pages()
        self._thumb_cache.pop(path, None)

    # ------------------------------------------------------------------
    # 页面计算与同步
    # ------------------------------------------------------------------
    def _current_path(self) -> str | None:
        if 0 <= self.page_index < len(self.pages):
            return self.pages[self.page_index].info.path
        return None

    def _recompute_pages(self, keep_path: str | None = None) -> None:
        path = keep_path or self._current_path()
        self.pages = compute_pages(self.infos, self.settings())

        if self.pages:
            target = 0
            if path:
                for i, page in enumerate(self.pages):
                    if page.info.path == path:
                        target = i
                        break
            self.page_index = max(0, min(target if path else self.page_index,
                                         len(self.pages) - 1))
        else:
            self.page_index = 0

        self._sync_page_labels()
        self._sync_list_selection()
        self._update_size_hint()
        self._update_status()
        self._update_folder_chip()
        self.generate_btn.setEnabled(bool(self.pages) and not self.busy)
        if not self.pages:
            self.canvas.set_empty_text(
                '所选图片都无法读取。\n\n请检查文件格式或重新选择图片。'
                if self.infos else
                '还没有图片。\n\n点击右上角「选择文件夹」，\n'
                '或把图片 / 文件夹拖到这里。')
        self._refresh_canvas()

    def _sync_page_labels(self) -> None:
        total = len(self.pages)
        if not total:
            self.page_label.setText('第 0 / 0 页')
            self.page_info.setText('—')
            self.page_info.setToolTip('')
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return

        index = max(0, min(self.page_index, total - 1))
        self.page_label.setText(f'第 {index + 1} / {total} 页')
        page = self.pages[index]
        dpi = max(1, self.settings().dpi)
        mm_w = page.width / dpi * 25.4
        mm_h = page.height / dpi * 25.4
        self.page_info.setText(f'{page.width} × {page.height} px')
        self.page_info.setToolTip(f'{mm_w:.0f} × {mm_h:.0f} mm @ {dpi} DPI')
        self.prev_btn.setEnabled(index > 0)
        self.next_btn.setEnabled(index < total - 1)

    def _sync_list_selection(self) -> None:
        path = self._current_path()
        if not path:
            return
        for row, info in enumerate(self.infos):
            if info.path == path:
                self.list.blockSignals(True)
                self.list.setCurrentRow(row)
                self.list.blockSignals(False)
                return

    def _update_size_hint(self) -> None:
        s = self.settings()
        if not self.pages:
            self.size_hint.setText('选择图片后，这里会显示当前页面尺寸说明。')
            return

        if s.size_mode == 'per_image':
            text = ('每页 = 该图片的原始像素尺寸（例如 100×100 与 200×200 会得到'
                    '两个不同尺寸的页面）。')
        elif s.size_mode == 'first':
            page = self.pages[0]
            text = f'所有页面统一为第一张图片尺寸 {page.width} × {page.height} px。'
        elif s.size_mode == 'max':
            page = self.pages[0]
            text = (f'宽度统一为最宽图片 {page.width} px，每页高度 = 该图片自身高度'
                    '（长度随图片）。')
        elif s.size_mode == 'a4':
            page = self.pages[0]
            mm_w = page.width / s.dpi * 25.4
            mm_h = page.height / s.dpi * 25.4
            orient = '横向' if s.a4_landscape else '纵向'
            text = (f'A4 {orient}：{page.width} × {page.height} px'
                    f'（{mm_w:.0f} × {mm_h:.0f} mm @ {s.dpi} DPI），每页同尺寸。')
        else:
            if s.custom_height_mode == 'fixed':
                text = (f'固定 {s.custom_width} × {s.custom_height} px，'
                        '每页尺寸相同。')
            elif s.custom_height_mode == 'original':
                text = (f'宽度固定 {s.custom_width} px，'
                        '每页高度 = 该图片原始高度（长度随图片）。')
            else:
                text = (f'宽度固定 {s.custom_width} px，'
                        '每页高度按图片比例计算（长度随图片）。')
        self.size_hint.setText(text)

    def _update_status(self) -> None:
        if self.busy:
            return
        if not self.infos:
            text = '就绪 · 选择文件夹或拖入图片'
        elif not self.pages:
            text = f'{len(self.infos)} 个文件均无法读取'
        else:
            base = os.path.basename(self.folder) if self.folder else '已选择图片'
            sizes = {(p.width, p.height) for p in self.pages}
            if len(sizes) == 1:
                w, h = next(iter(sizes))
                text = f'{base} · {len(self.pages)} 页 · 统一页面 {w} × {h} px'
            else:
                text = f'{base} · {len(self.pages)} 页 · 每页尺寸随图片变化'
            skipped = len(self.infos) - len(self.pages)
            if skipped:
                text += f' · 跳过 {skipped} 张'
        self.status.setText(text)

    def _update_folder_chip(self) -> None:
        if self.folder:
            self.folder_chip.setText(os.path.basename(self.folder))
            self.folder_chip.setToolTip(self.folder)
        elif self.infos:
            self.folder_chip.setText(f'{len(self.infos)} 个文件')
            self.folder_chip.setToolTip(os.path.dirname(self.infos[0].path))
        else:
            self.folder_chip.setText('未选择文件夹')
            self.folder_chip.setToolTip('')

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    # ------------------------------------------------------------------
    # 列表交互
    # ------------------------------------------------------------------
    def _on_row_changed(self, row: int) -> None:
        if not (0 <= row < len(self.infos)):
            return
        info = self.infos[row]
        index = None
        for i, page in enumerate(self.pages):
            if page.info.path == info.path:
                index = i
                break
        if index is None:
            self._set_status(f'无法读取该图片：{info.name}')
            return
        self._go_to_page(index)

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def _choose_output(self) -> None:
        if self.busy:
            return
        start = self._dialog_start_dir()
        suggested = self.output_edit.text().strip()
        if not suggested:
            suggested = os.path.join(start, 'output.pdf')
        path = self._pick_save('选择 PDF 保存位置',
                               os.path.basename(suggested),
                               start)
        if not path:
            return
        if not path.lower().endswith('.pdf'):
            path += '.pdf'
        self.output_edit.setText(path)
        self.last_dir = os.path.dirname(path) or self.last_dir
        self._save_config()

    def _generate(self) -> None:
        if self.busy:
            return
        if not self.pages:
            self._warn('请先选择包含图片的文件夹（或拖入图片）。')
            return
        out_path = self.output_edit.text().strip()
        if not out_path:
            self._warn('请先设置 PDF 保存路径。')
            return
        if not out_path.lower().endswith('.pdf'):
            out_path += '.pdf'
            self.output_edit.setText(out_path)
        if os.path.exists(out_path):
            ret = QMessageBox.question(
                self, APP_TITLE, f'文件已存在，是否覆盖？\n\n{out_path}')
            if ret != QMessageBox.StandardButton.Yes:
                return

        settings = self.settings()
        pages = [(p.info.path, p.width, p.height) for p in self.pages]
        skipped = len(self.infos) - len(self.pages)

        self._set_busy(True)
        self.progress.setRange(0, len(pages))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._set_status(f'正在生成… 0 / {len(pages)} 页')

        self._thread = QThread(self)
        self._worker = ConvertWorker(pages, settings, out_path, skipped)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_convert_finished)
        self._worker.failed.connect(self._on_convert_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self._set_status(f'正在生成… {done} / {total} 页')

    def _on_convert_finished(self, path: str, count: int, skipped: int) -> None:
        self._set_busy(False)
        self._update_status()
        message = f'已生成 {count} 页'
        if skipped:
            message += f'（跳过 {skipped} 张无法读取的图片）'
        message += f'\n\n{path}'

        box = QMessageBox(self)
        box.setWindowTitle(APP_TITLE)
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(message)
        open_btn = box.addButton('打开所在文件夹',
                                 QMessageBox.ButtonRole.ActionRole)
        box.addButton('好', QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(os.path.dirname(path) or '.'))

    def _on_convert_failed(self, message: str) -> None:
        self._set_busy(False)
        self._update_status()
        self._warn(f'转换失败：\n{message}')

    def _on_thread_finished(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.generate_btn.setEnabled(not busy and bool(self.pages))
        self.choose_btn.setEnabled(not busy)
        if not busy:
            self.progress.setVisible(False)

    # ------------------------------------------------------------------
    # 杂项
    # ------------------------------------------------------------------
    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, APP_TITLE, message)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        paths = [p for p in paths if p]
        if paths:
            self._add_paths(paths)
            event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        self._save_config()
        self.canvas.shutdown()
        super().closeEvent(event)
