#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片转 PDF 的核心逻辑（不依赖 Qt）。

包含：
- 图片探测（尺寸 / EXIF 方向）；
- 页面尺寸计算（跟随每张图片 / 第一张 / 最宽图片 / A4 / 自定义宽度）；
- 单页渲染（白底画布 + 适配方式 / 对齐 / 页边距）；
- 配置中 settings 的校验与转换。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from PIL import Image, ImageOps

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
APP_TITLE = '图片转 PDF'
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tif', '.tiff', '.webp'}
DPI_PRESETS = (72, 96, 150, 300, 600)
ANCHORS = (
    ('top_left', '↖'), ('top', '↑'), ('top_right', '↗'),
    ('left', '←'), ('center', '·'), ('right', '→'),
    ('bottom_left', '↙'), ('bottom', '↓'), ('bottom_right', '↘'),
)


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------
@dataclass(slots=True)
class ImageInfo:
    """一张图片的基本信息（只读文件头，速度快）。"""
    path: str
    name: str
    size: tuple[int, int] | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Settings:
    size_mode: str = 'per_image'          # per_image | first | max | a4 | custom
    custom_width: int = 1240
    custom_height_mode: str = 'ratio'     # ratio | original | fixed
    custom_height: int = 1754
    a4_landscape: bool = False
    fit_mode: str = 'contain'             # contain | original | stretch
    anchor: str = 'center'
    margin: int = 0
    dpi: int = 300


@dataclass(slots=True)
class Page:
    info: ImageInfo
    width: int
    height: int


DEFAULT_SETTINGS = Settings()


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def natural_key(name: str) -> list:
    """文件名自然排序键：file2 排在 file10 之前。"""
    parts = re.split(r'(\d+)', name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def file_ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def a4_pixels(dpi: int, landscape: bool = False) -> tuple[int, int]:
    """按 DPI 计算 A4 的像素尺寸（210 × 297 mm）。"""
    w = round(210 / 25.4 * dpi)
    h = round(297 / 25.4 * dpi)
    return (h, w) if landscape else (w, h)


def settings_from_dict(raw: object) -> Settings:
    """把配置里的 settings 字典转成经过校验的 Settings。"""
    default = DEFAULT_SETTINGS
    if not isinstance(raw, dict):
        return default

    def as_int(key: str, fallback: int, low: int, high: int) -> int:
        try:
            value = int(raw.get(key, fallback))
        except (TypeError, ValueError):
            return fallback
        return max(low, min(high, value))

    mode = raw.get('size_mode')
    if mode not in ('per_image', 'first', 'max', 'a4', 'custom'):
        mode = default.size_mode
    height_mode = raw.get('custom_height_mode')
    if height_mode not in ('ratio', 'original', 'fixed'):
        height_mode = default.custom_height_mode
    fit = raw.get('fit_mode')
    if fit not in ('contain', 'original', 'stretch'):
        fit = default.fit_mode
    anchor = raw.get('anchor')
    if anchor not in {a for a, _ in ANCHORS}:
        anchor = default.anchor
    landscape = raw.get('a4_landscape', default.a4_landscape)
    if not isinstance(landscape, bool):
        landscape = default.a4_landscape

    raw_dpi = as_int('dpi', default.dpi, 36, 1200)
    dpi = min(DPI_PRESETS, key=lambda preset: abs(preset - raw_dpi))

    return Settings(
        size_mode=str(mode),
        custom_width=as_int('custom_width', default.custom_width, 1, 100000),
        custom_height_mode=str(height_mode),
        custom_height=as_int('custom_height', default.custom_height, 1, 100000),
        a4_landscape=landscape,
        fit_mode=str(fit),
        anchor=str(anchor),
        margin=as_int('margin', default.margin, 0, 120),
        dpi=dpi,
    )


# --------------------------------------------------------------------------
# 图片探测
# --------------------------------------------------------------------------
def probe_image(path: str) -> ImageInfo:
    """读取图片尺寸（考虑 EXIF 方向），失败时记录错误而不抛出。"""
    name = os.path.basename(path)
    try:
        with Image.open(path) as im:
            w, h = im.size
            try:
                orientation = im.getexif().get(0x0112, 1)
            except Exception:
                orientation = 1
            if orientation in (5, 6, 7, 8):
                w, h = h, w
            if w <= 0 or h <= 0:
                raise ValueError('尺寸无效')
        return ImageInfo(path, name, (int(w), int(h)))
    except Exception as exc:
        return ImageInfo(path, name, None, f'{type(exc).__name__}: {exc}')


# --------------------------------------------------------------------------
# 页面尺寸
# --------------------------------------------------------------------------
def compute_pages(infos: list[ImageInfo], s: Settings) -> list[Page]:
    """按当前设置计算每一页的尺寸。

    - per_image：每页 = 该图片原始尺寸；
    - first    ：所有页 = 第一张图片尺寸；
    - max      ：宽度 = 最宽图片宽度，高度 = 该图片自身高度（长度随图片）；
    - a4       ：固定 A4（像素随 DPI）；
    - custom   ：固定宽度，高度可选 比例 / 原高 / 固定值。
    """
    items: list[tuple[ImageInfo, tuple[int, int]]] = []
    for info in infos:
        if info.size is not None:
            items.append((info, info.size))
    if not items:
        return []

    first_w, first_h = items[0][1]
    max_w = max(size[0] for _, size in items)
    a4_w, a4_h = a4_pixels(s.dpi, s.a4_landscape)

    pages: list[Page] = []
    for info, (iw, ih) in items:
        if s.size_mode == 'a4':
            w, h = a4_w, a4_h
        elif s.size_mode == 'first':
            w, h = first_w, first_h
        elif s.size_mode == 'max':
            # 宽度统一为最宽的图片，长度（高度）仍取该图片自身高度
            w, h = max_w, ih
        elif s.size_mode == 'custom':
            w = max(1, s.custom_width)
            if s.custom_height_mode == 'fixed':
                h = max(1, s.custom_height)
            elif s.custom_height_mode == 'original':
                h = ih
            else:  # ratio
                h = max(1, round(ih * w / iw)) if iw else w
        else:  # per_image
            w, h = iw, ih
        pages.append(Page(info, max(1, int(w)), max(1, int(h))))
    return pages


def anchor_offset(anchor: str, container: int, item: int, axis: str) -> int:
    if axis == 'x':
        if anchor.endswith('left'):
            return 0
        if anchor.endswith('right'):
            return container - item
        return (container - item) // 2
    if anchor.startswith('top'):
        return 0
    if anchor.startswith('bottom'):
        return container - item
    return (container - item) // 2


# --------------------------------------------------------------------------
# 单页渲染
# --------------------------------------------------------------------------
def render_page(path: str, page_w: int, page_h: int, s: Settings,
                max_dim: int = 0, scale: float | None = None) -> Image.Image:
    """把一张图片渲染到指定页面尺寸的白底画布上。

    scale 给定时按该比例缩放输出（预览用，≤1）；否则可用 max_dim 限制最长边。
    """
    factor = 1.0
    if scale is not None:
        factor = max(0.02, min(1.0, scale))
    elif max_dim and max(page_w, page_h) > max_dim:
        factor = max_dim / max(page_w, page_h)

    pw = max(1, int(round(page_w * factor)))
    ph = max(1, int(round(page_h * factor)))

    margin = max(0, int(round(s.margin * factor)))
    if margin * 2 >= pw:
        margin = max(0, (pw - 1) // 2)
    if margin * 2 >= ph:
        margin = max(0, (ph - 1) // 2)
    cw = max(1, pw - 2 * margin)
    ch = max(1, ph - 2 * margin)

    with Image.open(path) as src:
        if factor < 1.0:
            try:
                src.draft('RGB', (pw, ph))
            except Exception:
                pass
        img = ImageOps.exif_transpose(src)
        img = img.convert('RGB')
        iw, ih = img.size
        if iw <= 0 or ih <= 0:
            raise ValueError('图片尺寸无效')

        if s.fit_mode == 'stretch':
            tw, th = cw, ch
            x = y = 0
        else:
            if s.fit_mode == 'contain':
                ratio = min(cw / iw, ch / ih)
            else:  # original：保持原始像素大小（预览时随 factor 缩放）
                ratio = factor
            tw = max(1, int(round(iw * ratio)))
            th = max(1, int(round(ih * ratio)))
            x = anchor_offset(s.anchor, cw, tw, 'x')
            y = anchor_offset(s.anchor, ch, th, 'y')

        if (tw, th) != img.size:
            resample = (Image.Resampling.BILINEAR if factor < 1.0
                        else Image.Resampling.LANCZOS)
            img = img.resize((tw, th), resample)

    canvas = Image.new('RGB', (pw, ph), 'white')
    canvas.paste(img, (margin + x, margin + y))
    return canvas
