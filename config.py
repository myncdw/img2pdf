#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""config.json 的读写。

文件与 main.py 同目录，记录：
- default_dir：默认路径（默认空）
- last_dir：上一次文件对话框所在目录（只作为下次打开对话框的起点）
- settings：上一次的页面设置

不会记录已选图片 / 输出文件，也不会自动恢复上次任务。
"""
from __future__ import annotations

import json
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, 'config.json')


def load_config() -> dict:
    """读取 config.json；文件不存在或格式错误时返回空字典。"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict) -> None:
    """原子写入 config.json；失败时静默忽略。"""
    tmp = CONFIG_PATH + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except OSError:
        pass
