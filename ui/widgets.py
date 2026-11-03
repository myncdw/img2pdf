#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面组件：图片列表、连续滚动预览、导出线程。

这里只负责“显示与交互”，页面尺寸 / 渲染逻辑在 core.py。
"""
from __future__ import annotations

import bisect
import math
import os
import traceback
from collections import OrderedDict
from dataclasses import dataclass

from PIL import Image

from PySide6.QtCore import (QMimeData, QObject, QPoint, QPointF, QRectF,
                            QRunnable, QSize, QThreadPool, Qt, Signal)
from PySide6.QtGui import (QColor, QDrag, QDragEnterEvent, QDropEvent, QImage,
                           QImageReader, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QListWidget,
                               QScrollArea, QStyle, QStyledItemDelegate,
                               QWidget)

from core import Page, Settings, render_page

# --------------------------------------------------------------------------
# 预览参数
# --------------------------------------------------------------------------
CANVAS_GUTTER = 46          # 预览画布左右留白
PAGE_GAP = 28               # 页面之间的间距
PAGE_LABEL_H = 22           # 页面顶部标签高度
PREFETCH_PX = 800           # 视口外预加载范围
MAX_CACHE_BYTES = 120 * 1024 * 1024
MAX_PREVIEW_PIXELS = 4_500_000


# --------------------------------------------------------------------------
# 图片 <-> Qt 转换
# --------------------------------------------------------------------------
def pil_to_qimage(img: Image.Image) -> QImage:
    rgb = img.convert('RGB')
    data = rgb.tobytes()
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3,
                  QImage.Format.Format_RGB888)
    return qimg.copy()


def load_thumb(path: str, size: int = 88) -> QPixmap:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    orig = reader.size()
    if orig.isValid():
        reader.setScaledSize(orig.scaled(QSize(size, size),
                                         Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        return QPixmap()
    return QPixmap.fromImage(image)


# --------------------------------------------------------------------------
# 图片列表（delegate 绘制，避免 item widget 在拖动排序时错位）
# --------------------------------------------------------------------------
ROLE_PATH = int(Qt.ItemDataRole.UserRole)
ROLE_SIZE = ROLE_PATH + 1
ROLE_ERROR = ROLE_PATH + 2


class FileDelegate(QStyledItemDelegate):
    def __init__(self, parent=None, thumb_provider=None) -> None:
        super().__init__(parent)
        self._thumb_provider = thumb_provider

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, 62)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(option.rect).adjusted(2, 3, -2, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            bg, border = QColor('#17233a'), QColor('#3f66b0')
        elif hover:
            bg, border = QColor('#151d28'), QColor('#2d3b50')
        else:
            bg, border = QColor('#121821'), QColor('#1f2834')
        painter.setBrush(bg)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(rect, 10, 10)

        font = painter.font()
        font.setPointSizeF(8.0)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor('#5f6a7d'))
        painter.drawText(
            QRectF(rect.x() + 8, rect.y(), 20, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            f'{index.row() + 1:02d}')

        thumb_rect = QRectF(rect.x() + 34,
                            rect.y() + (rect.height() - 44) / 2, 44, 44)
        thumb = QPixmap()
        if self._thumb_provider is not None:
            path = index.data(ROLE_PATH)
            if path:
                thumb = self._thumb_provider(path)
        error = index.data(ROLE_ERROR)
        clip = QPainterPath()
        clip.addRoundedRect(thumb_rect, 8, 8)
        painter.save()
        painter.setClipPath(clip)
        if isinstance(thumb, QPixmap) and not thumb.isNull():
            painter.fillRect(thumb_rect, QColor('#0d1117'))
            scaled = thumb.scaled(
                QSize(44, 44), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(
                QPointF(thumb_rect.x() + (44 - scaled.width()) / 2,
                        thumb_rect.y() + (44 - scaled.height()) / 2),
                scaled)
            frame = QColor('#222a36')
        else:
            painter.fillRect(thumb_rect, QColor('#2a1d20'))
            font = painter.font()
            font.setPointSizeF(11)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor('#ff8080'))
            painter.drawText(thumb_rect, int(Qt.AlignmentFlag.AlignCenter), '!')
            frame = QColor('#4a2b30')
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(frame, 1))
        painter.drawRoundedRect(thumb_rect, 8, 8)

        text_x = thumb_rect.right() + 10
        text_w = max(10.0, rect.right() - text_x - 8)
        name = index.data(Qt.ItemDataRole.DisplayRole) or ''
        font = painter.font()
        font.setPointSizeF(9.5)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor('#e3e8f1'))
        elided = painter.fontMetrics().elidedText(
            str(name), Qt.TextElideMode.ElideMiddle, int(text_w))
        painter.drawText(
            QRectF(text_x, rect.y() + 12, text_w, 18),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            elided)

        size = index.data(ROLE_SIZE)
        font.setPointSizeF(8.0)
        painter.setFont(font)
        if error:
            painter.setPen(QColor('#c96a6a'))
            detail = '无法读取'
        elif isinstance(size, (tuple, list)) and len(size) == 2:
            painter.setPen(QColor('#6f7c90'))
            detail = f'{size[0]} × {size[1]} px'
        else:
            painter.setPen(QColor('#6f7c90'))
            detail = ''
        painter.drawText(
            QRectF(text_x, rect.y() + 32, text_w, 16),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            detail)
        painter.restore()


class OrderList(QListWidget):
    """自定义拖动排序的图片列表。

    Qt 内置的 InternalMove 会重新序列化/搬运 item（配合自定义绘制时容易出现
    错位、重叠），这里改为自己处理拖拽：原 item 对象直接 takeItem/insertItem。
    """

    orderChanged = Signal()
    MIME_TYPE = 'application/x-img2pdf-row'

    def __init__(self) -> None:
        super().__init__()
        self._press_pos: QPoint | None = None
        self._drag_row = -1
        self._drop_row = -1

        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.setAutoScroll(False)

    # ---- 拖动指示线 ----
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_row < 0 or self.count() == 0:
            return
        painter = QPainter(self.viewport())
        if self._drop_row >= self.count():
            rect = self.visualRect(
                self.model().index(self.count() - 1, 0))
            y = rect.bottom()
        else:
            rect = self.visualRect(self.model().index(self._drop_row, 0))
            y = rect.top()
        painter.setPen(QPen(QColor('#4f7dff'), 2))
        painter.drawLine(0, y, self.viewport().width(), y)

    # ---- 发起拖动 ----
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            self._press_pos = pos
            index = self.indexAt(pos)
            self._drag_row = index.row() if index.isValid() else -1
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (event.buttons() & Qt.MouseButton.LeftButton) \
                and self._drag_row >= 0 and self._press_pos is not None:
            moved = (event.position().toPoint()
                     - self._press_pos).manhattanLength()
            if moved >= QApplication.startDragDistance():
                self._begin_drag(self._drag_row)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _begin_drag(self, row: int) -> None:
        self._press_pos = None
        item = self.item(row)
        if item is None:
            return
        self.setCurrentRow(row)
        rect = self.visualRect(self.model().index(row, 0))
        pixmap = self.viewport().grab(rect)

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self.MIME_TYPE, str(row).encode('ascii'))
        drag.setMimeData(mime)
        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_row = -1
        self._drop_row = -1
        self.viewport().update()

    # ---- 接收拖动 ----
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(self.MIME_TYPE):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not event.mimeData().hasFormat(self.MIME_TYPE):
            event.ignore()
            return
        pos = event.position().toPoint()
        row = self._drop_row_at(pos)
        if row != self._drop_row:
            self._drop_row = row
            self.viewport().update()
        self._auto_scroll(pos)
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def dragLeaveEvent(self, event) -> None:
        self._drop_row = -1
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if not event.mimeData().hasFormat(self.MIME_TYPE):
            event.ignore()
            return
        dst = self._drop_row_at(event.position().toPoint())
        src = self._drag_row
        self._drop_row = -1
        self.viewport().update()
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        if src >= 0 and 0 <= dst <= self.count():
            self._move_row(src, dst)

    def _drop_row_at(self, pos: QPoint) -> int:
        index = self.indexAt(pos)
        if not index.isValid():
            return self.count()
        rect = self.visualRect(index)
        if pos.y() < rect.center().y():
            return index.row()
        return index.row() + 1

    def _auto_scroll(self, pos: QPoint) -> None:
        margin = 24
        bar = self.verticalScrollBar()
        if pos.y() < margin:
            bar.setValue(bar.value() - 12)
        elif pos.y() > self.viewport().height() - margin:
            bar.setValue(bar.value() + 12)

    def _move_row(self, src: int, dst: int) -> None:
        if dst in (src, src + 1):
            return
        # 屏蔽 move 过程中的选中变化，避免主窗口用旧页序处理中间的 row 变化
        self.blockSignals(True)
        try:
            item = self.takeItem(src)
            if item is None:
                return
            if dst > src:
                dst -= 1
            dst = max(0, min(dst, self.count()))
            self.insertItem(dst, item)
            self.setCurrentRow(dst)
        finally:
            self.blockSignals(False)
        self.orderChanged.emit()


# --------------------------------------------------------------------------
# 连续滚动预览（虚拟化 + 动态加载）
# --------------------------------------------------------------------------
@dataclass(slots=True)
class _Slot:
    index: int
    label_y: int
    page_y: int
    height: int


class _PageSignals(QObject):
    ready = Signal(int, object, QImage)
    failed = Signal(int, str)


class _PageTask(QRunnable):
    def __init__(self, index: int, key: tuple, path: str,
                 page_w: int, page_h: int, settings: Settings,
                 scale: float, signals: _PageSignals) -> None:
        super().__init__()
        self.index = index
        self.key = key
        self.path = path
        self.page_w = page_w
        self.page_h = page_h
        self.settings = settings
        self.scale = scale
        self.signals = signals

    def run(self) -> None:
        try:
            image = render_page(self.path, self.page_w, self.page_h,
                                self.settings, scale=self.scale)
            self.signals.ready.emit(self.index, self.key, pil_to_qimage(image))
        except Exception as exc:
            traceback.print_exc()
            self.signals.failed.emit(self.index, f'{type(exc).__name__}: {exc}')


class PageCanvas(QWidget):
    """把所有页面纵向排列的画布，只渲染视口附近的页面。"""

    currentPageChanged = Signal(int)
    pageClicked = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.pages: list[Page] = []
        self.settings = Settings()
        self._zoom = 1.0
        self._page_w = 400
        self._slots: list[_Slot] = []
        self._starts: list[int] = []
        self._current = 0
        self._vp_top = 0
        self._vp_h = 0
        self._vp_w = 0
        self._empty_text = ('还没有图片。\n\n'
                            '点击右上角「选择文件夹」，\n'
                            '或把图片 / 文件夹拖到这里。')

        self._cache: OrderedDict[int, tuple[tuple, QPixmap]] = OrderedDict()
        self._cache_bytes = 0
        self._inflight: dict[int, tuple] = {}
        self._failed: dict[int, str] = {}

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._signals = _PageSignals(self)
        self._signals.ready.connect(self._on_ready)
        self._signals.failed.connect(self._on_failed)

        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(False)

    # ---- 对外接口 ----
    def set_empty_text(self, text: str) -> None:
        self._empty_text = text
        self.update()

    def set_document(self, pages: list[Page], settings: Settings) -> None:
        self.pages = pages
        self.settings = settings
        self._failed.clear()
        if not pages:
            self._slots = []
            self._starts = []
            self._cache.clear()
            self._cache_bytes = 0
            self._inflight.clear()
            self._current = 0
            self.setMinimumSize(1, 1)
            self.resize(max(1, self._vp_w), max(1, self._vp_h))
            self.update()
            return
        self._current = max(0, min(self._current, len(pages) - 1))
        self._relayout()

    def set_current(self, index: int) -> None:
        if not self.pages:
            return
        index = max(0, min(index, len(self.pages) - 1))
        if index != self._current:
            self._current = index
            self.update()

    def set_viewport(self, top: int, height: int, width: int) -> None:
        if width <= 0 or height <= 0:
            return
        width = max(240, width)
        relayout = width != self._vp_w
        self._vp_top = top
        self._vp_h = height
        self._vp_w = width
        if not self.pages:
            self.resize(width, height)
            self.update()
            return
        if relayout:
            self._relayout()
            return
        self._request()
        self._update_current()

    def set_zoom(self, zoom: float) -> None:
        zoom = max(0.4, min(3.0, zoom))
        if abs(zoom - self._zoom) < 1e-4:
            return
        self._zoom = zoom
        if self.pages:
            self._relayout()

    def zoom(self) -> float:
        return self._zoom

    def page_top(self, index: int) -> int:
        if not (0 <= index < len(self._slots)):
            return 0
        return max(0, self._slots[index].label_y - 8)

    def page_scale(self, index: int) -> float:
        if not (0 <= index < len(self.pages)):
            return 0.0
        width = self.pages[index].width
        return self._page_w / width if width else 0.0

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(2000)

    # ---- 布局 ----
    def _relayout(self) -> None:
        if not self.pages or self._vp_w <= 0:
            return
        base_w = max(200, self._vp_w - CANVAS_GUTTER * 2)
        page_w = max(80, int(base_w * self._zoom))
        content_w = max(self._vp_w, page_w + CANVAS_GUTTER * 2)

        y = 18
        slots: list[_Slot] = []
        for i, page in enumerate(self.pages):
            h = max(40, int(round(page_w * page.height / max(1, page.width))))
            slots.append(_Slot(i, y, y + PAGE_LABEL_H, h))
            y += PAGE_LABEL_H + h + PAGE_GAP
        content_h = y + 12

        self._page_w = page_w
        self._slots = slots
        self._starts = [s.page_y for s in slots]
        self.setMinimumSize(content_w, max(content_h, self._vp_h))
        self.resize(content_w, max(content_h, self._vp_h))
        self._request()
        self._update_current()
        self.update()

    def _indices_between(self, top: int, bottom: int) -> list[int]:
        if not self._slots:
            return []
        i = bisect.bisect_right(self._starts, top) - 1
        i = max(0, i)
        while i < len(self._slots) and \
                self._slots[i].page_y + self._slots[i].height < top:
            i += 1
        result: list[int] = []
        while i < len(self._slots) and self._slots[i].page_y <= bottom:
            result.append(i)
            i += 1
        return result

    # ---- 动态加载 ----
    def _key(self, index: int) -> tuple:
        page = self.pages[index]
        try:
            mtime = os.path.getmtime(page.info.path)
        except OSError:
            mtime = 0.0
        s = self.settings
        return (page.info.path, mtime, page.width, page.height,
                s.margin, s.fit_mode, s.anchor,
                round(self._render_scale(page), 3))

    def _render_scale(self, page: Page) -> float:
        target_w = self._page_w * 1.35
        scale = target_w / max(1, page.width)
        area = max(1, page.width * page.height)
        if area * scale * scale > MAX_PREVIEW_PIXELS:
            scale = math.sqrt(MAX_PREVIEW_PIXELS / area)
        return max(0.02, min(1.0, scale))

    def _request(self) -> None:
        if not self.pages or not self._slots:
            return
        top = max(0, self._vp_top - PREFETCH_PX)
        bottom = self._vp_top + self._vp_h + PREFETCH_PX
        wanted = self._indices_between(top, bottom)
        wanted_set = set(wanted)

        for index in wanted:
            key = self._key(index)
            cached = self._cache.get(index)
            if cached is not None and cached[0] == key:
                continue
            if self._inflight.get(index) == key:
                continue
            page = self.pages[index]
            self._inflight[index] = key
            task = _PageTask(index, key, page.info.path, page.width,
                             page.height, self.settings,
                             self._render_scale(page), self._signals)
            self._pool.start(task)

        self._evict(wanted_set)

    def _evict(self, wanted: set[int]) -> None:
        if self._cache_bytes <= MAX_CACHE_BYTES:
            return
        candidates = [i for i in self._cache if i not in wanted]
        candidates.sort(key=lambda i: abs(i - self._current), reverse=True)
        for index in candidates:
            if self._cache_bytes <= MAX_CACHE_BYTES:
                break
            entry = self._cache.pop(index, None)
            if entry is not None:
                pm = entry[1]
                self._cache_bytes -= pm.width() * pm.height() * 4

    def _on_ready(self, index: int, key: tuple, image: QImage) -> None:
        if self._inflight.get(index) == key:
            self._inflight.pop(index, None)
        if not (0 <= index < len(self.pages)):
            return
        if self._key(index) != key:
            return  # 设置已变化，丢弃过期结果
        self._failed.pop(index, None)
        pm = QPixmap.fromImage(image)
        old = self._cache.pop(index, None)
        if old is not None:
            self._cache_bytes -= old[1].width() * old[1].height() * 4
        self._cache[index] = (key, pm)
        self._cache_bytes += pm.width() * pm.height() * 4
        self.update()

    def _on_failed(self, index: int, message: str) -> None:
        self._inflight.pop(index, None)
        self._failed[index] = message
        self.update()

    def _update_current(self) -> None:
        if not self._slots:
            return
        probe = self._vp_top + 30
        i = bisect.bisect_right(self._starts, probe) - 1
        index = self._slots[max(0, i)].index
        if index != self._current:
            self._current = index
            self.update()
            self.currentPageChanged.emit(index)

    # ---- 交互 ----
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            y = int(event.position().y())
            for slot in self._slots:
                if slot.page_y <= y < slot.page_y + slot.height:
                    self.pageClicked.emit(slot.index)
                    break
        super().mousePressEvent(event)

    # ---- 绘制 ----
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor('#0c0f14'))

        if not self.pages or not self._slots:
            painter.setPen(QColor('#59657a'))
            flags = (int(Qt.AlignmentFlag.AlignCenter)
                     | int(Qt.TextFlag.TextWordWrap))
            painter.drawText(self.rect().adjusted(40, 40, -40, -40),
                             flags, self._empty_text)
            return

        top = self._vp_top - 120
        bottom = self._vp_top + self._vp_h + 120
        visible = self._indices_between(top, bottom)
        if not visible:
            return

        label_font = painter.font()
        label_font.setPointSizeF(8.5)
        painter.setFont(label_font)

        x = (self.width() - self._page_w) / 2.0
        total = len(self.pages)
        for index in visible:
            slot = self._slots[index]
            page = self.pages[index]

            painter.setPen(QColor('#6f7c90'))
            painter.drawText(
                QRectF(x, slot.label_y, self._page_w, PAGE_LABEL_H - 4),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                f'{index + 1} / {total}')
            painter.drawText(
                QRectF(x, slot.label_y, self._page_w, PAGE_LABEL_H - 4),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f'{page.width} × {page.height} px')

            rect = QRectF(x, slot.page_y, self._page_w, slot.height)
            painter.setPen(Qt.PenStyle.NoPen)
            for offset, alpha in ((10, 24), (6, 20), (3, 16)):
                painter.setBrush(QColor(0, 0, 0, alpha))
                painter.drawRoundedRect(
                    QRectF(rect.x() - offset, rect.y() + 4,
                           rect.width() + offset * 2,
                           rect.height() + offset + 4), 5, 5)

            cached = self._cache.get(index)
            if cached is not None and cached[0] == self._key(index) \
                    and not cached[1].isNull():
                painter.drawPixmap(rect, cached[1],
                                   QRectF(cached[1].rect()))
            else:
                painter.setBrush(QColor('#141b25'))
                painter.setPen(QPen(QColor('#222b38'), 1))
                painter.drawRect(rect)
                painter.setPen(QColor('#4f5b6d'))
                text = '无法渲染' if index in self._failed else '渲染中…'
                painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)

            selected = index == self._current
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor('#4f7dff') if selected
                                else QColor('#333c4b'),
                                2 if selected else 1))
            painter.drawRect(rect)


class PreviewScroll(QScrollArea):
    """支持 Ctrl + 滚轮缩放的滚动区域。"""

    zoomRequested = Signal(float)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.zoomRequested.emit(1.12 if delta > 0 else 1 / 1.12)
            event.accept()
            return
        super().wheelEvent(event)


# --------------------------------------------------------------------------
# 导出任务
# --------------------------------------------------------------------------
class ConvertWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(str, int, int)
    failed = Signal(str)

    def __init__(self, pages: list[tuple[str, int, int]], settings: Settings,
                 out_path: str, skipped: int) -> None:
        super().__init__()
        self.pages = pages
        self.settings = settings
        self.out_path = out_path
        self.skipped = skipped

    def run(self) -> None:
        try:
            images: list[Image.Image] = []
            total = len(self.pages)
            for i, (path, width, height) in enumerate(self.pages, start=1):
                images.append(render_page(path, width, height, self.settings))
                self.progress.emit(i, total)
            if not images:
                raise RuntimeError('没有可用的图片（无法读取或格式不受支持）。')

            out_dir = os.path.dirname(self.out_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            first, *rest = images
            first.save(self.out_path, 'PDF',
                       resolution=float(self.settings.dpi),
                       save_all=True, append_images=rest)
            self.finished.emit(self.out_path, len(images), self.skipped)
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(str(exc))
