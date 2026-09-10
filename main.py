#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片转 PDF —— 简易深色 GUI 工具。

功能：
- 选择一个文件夹，按文件名排序合成一个 PDF；
- 默认页面宽度跟随第一张图片；
- 支持自定义宽度：A4 / 最宽图片 / 自定义数值（像素）；
- 跨平台（Windows / Linux），使用 xdg_dialogs.py 完成原生文件对话框。
"""
import os
import threading
import traceback

import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image

import xdg_dialogs

# ---------- 常量 ----------
APP_TITLE = "图片转 PDF"
PAGE_SIZES = {
    "A4": (2480, 3508),      # 300 DPI 的 A4 像素尺寸
}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tif', '.tiff', '.webp'}

# ---------- 深色主题 ----------
COLOR_BG = "#1e1f22"
COLOR_BG_ALT = "#2a2d31"
COLOR_FG = "#e8eaed"
COLOR_ACCENT = "#4f8cff"
COLOR_MUTED = "#9aa0a6"

# ---------- 工具函数 ----------
def _natural_key(name):
    """自然排序键：把文件名里的数字按数值比较（file2 排在 file10 前）。"""
    import re
    parts = re.split(r'(\d+)', name)
    return [int(part) if part.isdigit() else part.lower() for part in parts]


def _ext(path):
    return os.path.splitext(path)[1].lower()


class ImageToPdfApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("640x700")
        root.minsize(560, 560)
        root.configure(bg=COLOR_BG)
        self._setup_style()

        # 状态
        self.folder = ''
        self.image_files = []
        self.last_dir = os.path.expanduser('~')
        self.busy = False

        self._build_ui()
        self._update_status()

    # ---------- UI 构建 ----------
    def _setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('TFrame', background=COLOR_BG)
        style.configure('TLabel', background=COLOR_BG, foreground=COLOR_FG)
        style.configure('TButton', background=COLOR_BG_ALT, foreground=COLOR_FG,
                        bordercolor=COLOR_BG_ALT, focuscolor=COLOR_ACCENT,
                        padding=6)
        style.map('TButton',
                  background=[('active', COLOR_ACCENT), ('disabled', COLOR_BG_ALT)],
                  foreground=[('disabled', COLOR_MUTED)])
        style.configure('Accent.TButton', background=COLOR_ACCENT,
                        foreground='#ffffff', font=('Sans', 10, 'bold'))
        style.map('Accent.TButton',
                  background=[('active', '#6b9fff'), ('disabled', COLOR_BG_ALT)],
                  foreground=[('disabled', COLOR_MUTED)])
        style.configure('TRadiobutton', background=COLOR_BG, foreground=COLOR_FG)
        style.map('TRadiobutton',
                  background=[('active', COLOR_BG)],
                  foreground=[('active', COLOR_FG)])
        style.configure('TEntry', fieldbackground=COLOR_BG_ALT, foreground=COLOR_FG,
                        insertcolor=COLOR_FG)
        style.configure('TScrollbar', background=COLOR_BG_ALT, troughcolor=COLOR_BG)

    def _build_ui(self):
        # --- 顶部：文件夹选择 ---
        header = ttk.Frame(self.root)
        header.pack(fill='x', padx=16, pady=8)
        ttk.Label(header, text="① 选择图片文件夹",
                  font=('Sans', 12, 'bold')).pack(side='left')
        ttk.Button(header, text="选择文件夹…",
                   command=self.choose_folder).pack(side='right')

        # 图片列表
        list_frame = ttk.Frame(self.root)
        list_frame.pack(fill='both', expand=True, padx=16, pady=8)
        self.listbox = tk.Listbox(
            list_frame, bg=COLOR_BG_ALT, fg=COLOR_FG,
            selectbackground=COLOR_ACCENT, selectforeground='#ffffff',
            highlightthickness=0, relief='flat', activestyle='none',
        )
        sb = ttk.Scrollbar(list_frame, orient='vertical', command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        # --- 页面尺寸设置 ---
        width_frame = ttk.LabelFrame(self.root, text="② 页面尺寸设置")
        width_frame.pack(fill='x', padx=16, pady=8)

        self.width_mode = tk.StringVar(value='first')
        self.custom_width = tk.StringVar(value='1024')
        self.custom_height = tk.StringVar(value='1024')
        row = ttk.Frame(width_frame)
        row.pack(fill='x', padx=10, pady=6)
        for text, value in (
            ("跟随第一张图片", 'first'),
            ("最宽的那张图片", 'max'),
            ("A4", 'a4'),
            ("自定义宽度", 'custom'),
        ):
            ttk.Radiobutton(row, text=text, value=value,
                            variable=self.width_mode,
                            command=self._sync_custom_entry).pack(side='left', padx=(0, 14))
        custom_row = ttk.Frame(width_frame)
        custom_row.pack(fill='x', padx=10, pady=(0, 8))
        ttk.Label(custom_row, text="像素宽：").pack(side='left')
        self.custom_entry = ttk.Entry(custom_row, textvariable=self.custom_width,
                                      width=10)
        self.custom_entry.pack(side='left')
        ttk.Label(custom_row, text="（输入大于 0 的整数）",
                  foreground=COLOR_MUTED).pack(side='left', padx=8)
        self._sync_custom_entry()

        # --- 图片放置方式 ---
        place_frame = ttk.LabelFrame(self.root, text="③ 图片放置方式")
        place_frame.pack(fill='x', padx=16, pady=8)
        self.place_mode = tk.StringVar(value='center')
        place_row = ttk.Frame(place_frame)
        place_row.pack(fill='x', padx=10, pady=6)
        for text, value in (
            ("居中", 'center'),
            ("左上角", 'topleft'),
            ("右上角", 'topright'),
            ("左下角", 'bottomleft'),
            ("右下角", 'bottomright'),
            ("拉伸", 'stretch'),
        ):
            ttk.Radiobutton(place_row, text=text, value=value,
                            variable=self.place_mode).pack(side='left', padx=(0, 14))

        # --- 输出设置 ---
        out_frame = ttk.LabelFrame(self.root, text="④ 输出设置")
        out_frame.pack(fill='x', padx=16, pady=8)
        self.pdf_path = tk.StringVar()
        out_row = ttk.Frame(out_frame)
        out_row.pack(fill='x', padx=10, pady=6)
        ttk.Label(out_row, text="保存为：").pack(side='left')
        ttk.Entry(out_row, textvariable=self.pdf_path).pack(
            side='left', fill='x', expand=True, padx=(0, 8))
        ttk.Button(out_row, text="浏览…",
                   command=self.choose_output).pack(side='right')

        # --- 底部：状态与执行 ---
        bottom = ttk.Frame(self.root)
        bottom.pack(fill='x', padx=16, pady=8)
        self.status_var = tk.StringVar()
        self.status_label = ttk.Label(bottom, textvariable=self.status_var,
                                      foreground=COLOR_MUTED, wraplength=420)
        self.status_label.pack(side='left')
        self.run_btn = ttk.Button(bottom, text="生成 PDF", style='Accent.TButton',
                                  command=self.run_convert)
        self.run_btn.pack(side='right')

    def _sync_custom_entry(self):
        state = 'normal' if self.width_mode.get() == 'custom' else 'disabled'
        self.custom_entry.configure(state=state)

    # ---------- 对话框 ----------
    def choose_folder(self):
        if self.busy:
            return
        folder = xdg_dialogs.choose_folder("选择包含图片的文件夹")
        if not folder:
            return
        self.folder = folder
        self.last_dir = folder
        self._load_folder()
        # 默认保存路径：所选文件夹 + output.pdf
        self.pdf_path.set(os.path.join(folder, 'output.pdf'))

    def choose_output(self):
        if self.busy:
            return
        path = xdg_dialogs.choose_save_file("选择 PDF 保存位置", 'output.pdf',
                                        initialdir=self.last_dir)
        if not path:
            return
        if not path.lower().endswith('.pdf'):
            path += '.pdf'
        self.pdf_path.set(path)
        self.last_dir = os.path.dirname(path)

    # ---------- 核心逻辑 ----------
    def _load_folder(self):
        """扫描文件夹，收集图片，按文件名自然排序。"""
        files = []
        try:
            names = os.listdir(self.folder)
        except OSError as e:
            messagebox.showerror(APP_TITLE, f"无法读取文件夹：\n{e}")
            self.folder = ''
            return
        for name in names:
            if _ext(name) in IMAGE_EXTS:
                files.append(name)
        files.sort(key=_natural_key)
        self.image_files = files
        self.listbox.delete(0, 'end')
        for name in files:
            self.listbox.insert('end', name)
        self._update_status()

    def _compute_page_size(self):
        """计算目标页面尺寸 (width, height)。

        height 为 None 表示高度随每张图片等比缩放（多图场景）；
        A4 / 自定义且仅一张图片时，返回固定 (width, height)。
        非法输入时弹窗提示并返回 None。
        """
        mode = self.width_mode.get()
        single = len(self.image_files) == 1

        if mode == 'a4':
            # A4 固定纸张尺寸（无论几张图片都应用高度）
            return (PAGE_SIZES['A4'][0], PAGE_SIZES['A4'][1])

        if mode == 'custom':
            text = self.custom_width.get().strip()
            if not text.isdigit() or int(text) <= 0:
                messagebox.showwarning(APP_TITLE,
                                       "请为自定义宽度输入大于 0 的整数。")
                return None
            width = int(text)
            if single:
                height = self._ask_height()
                if height is None:
                    return None  # 用户取消
                return (width, height)
            # 多图自定义：页面高度默认使用 A4 高度
            return (width, PAGE_SIZES['A4'][1])

        # first / max：读取图片实际尺寸
        sizes = []
        for path in self.image_files:
            try:
                with Image.open(os.path.join(self.folder, path)) as img:
                    sizes.append(img.size)
            except Exception:
                continue
        if not sizes:
            messagebox.showwarning(APP_TITLE, "无法读取任何图片的尺寸。")
            return None
        if mode == 'first':
            return (sizes[0][0], sizes[0][1])
        # max：宽度取最宽、高度取最高
        width = max(s[0] for s in sizes)
        height = max(s[1] for s in sizes)
        return (width, height)

    def _ask_height(self):
        """自定义模式仅一张图片时，询问页面高度（像素）。返回 None 表示取消。"""
        from tkinter import simpledialog
        initial = self.custom_height.get().strip()
        try:
            initial_int = int(initial)
        except ValueError:
            initial_int = 1024
        value = simpledialog.askinteger(
            "自定义高度",
            "当前仅有一张图片，请输入页面高度（像素）：",
            parent=self.root,
            initialvalue=initial_int,
            minvalue=1,
        )
        if value is None:
            return None
        self.custom_height.set(str(value))
        return value

    def _paste_position(self, canvas_w, canvas_h, img_w, img_h):
        """根据放置方式计算图片左上角粘贴坐标。"""
        mode = self.place_mode.get()
        if mode == 'topleft':
            return 0, 0
        if mode == 'topright':
            return canvas_w - img_w, 0
        if mode == 'bottomleft':
            return 0, canvas_h - img_h
        if mode == 'bottomright':
            return canvas_w - img_w, canvas_h - img_h
        # 默认居中
        return (canvas_w - img_w) // 2, (canvas_h - img_h) // 2

    def _prepare_pages(self, width, height):
        """把每张图片按放置方式放到统一尺寸的白色页面上。

        - 拉伸：把图片拉伸填满整页（可能变形）；
        - 其他：保持图片原有大小，按所选位置放置，超出页面部分居中裁剪。
        """
        pages = []
        skipped = 0
        stretch = self.place_mode.get() == 'stretch'
        for name in self.image_files:
            path = os.path.join(self.folder, name)
            try:
                with Image.open(path) as img:
                    img = img.convert('RGB')
                    canvas = Image.new('RGB', (width, height), 'white')
                    if stretch:
                        img = img.resize((width, height),
                                         Image.Resampling.LANCZOS)
                        x, y = 0, 0
                    else:
                        x, y = self._paste_position(width, height,
                                                    img.size[0], img.size[1])
                    canvas.paste(img, (x, y))
                    pages.append(canvas)
            except Exception:
                skipped += 1
        return pages, skipped

    def run_convert(self):
        if self.busy:
            return
        if not self.folder or not self.image_files:
            messagebox.showwarning(APP_TITLE, "请先选择包含图片的文件夹。")
            return
        out_path = self.pdf_path.get().strip()
        if not out_path:
            messagebox.showwarning(APP_TITLE, "请先设置 PDF 保存路径。")
            return
        if not out_path.lower().endswith('.pdf'):
            out_path += '.pdf'
            self.pdf_path.set(out_path)

        # 在主线程计算页面尺寸（自定义+单图时需要弹窗询问高度）
        size = self._compute_page_size()
        if size is None:
            return
        width, height = size

        self._set_busy(True)
        threading.Thread(target=self._convert_worker,
                         args=(out_path, width, height), daemon=True).start()

    def _convert_worker(self, out_path, width, height):
        """后台线程：执行实际转换，通过 after 回主线程更新界面。"""
        try:
            pages, skipped = self._prepare_pages(width, height)
            if not pages:
                self.root.after(0, self._show_error,
                                "没有可用的图片（无法读取或不受支持）。")
                return
            os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
            pages[0].save(
                out_path,
                'PDF',
                resolution=96.0,
                save_all=True,
                append_images=pages[1:],
            )
            self.root.after(0, self._show_success, out_path, len(pages), skipped)
        except Exception as e:
            traceback.print_exc()
            self.root.after(0, self._show_error, f"转换失败：\n{e}")

    # ---------- 结果与状态 ----------
    def _update_status(self):
        if not self.folder:
            text = "未选择文件夹"
        elif not self.image_files:
            text = f"{os.path.basename(self.folder)}：未找到支持的图片"
        else:
            text = f"{os.path.basename(self.folder)}：共 {len(self.image_files)} 张图片（按文件名排序）"
        self.status_var.set(text)

    def _set_busy(self, busy):
        self.busy = busy
        self.run_btn.configure(state='disabled' if busy else 'normal')

    def _show_error(self, msg):
        self._set_busy(False)
        self.status_var.set(msg)
        messagebox.showerror(APP_TITLE, msg)

    def _show_success(self, out_path, page_count, skipped):
        self._set_busy(False)
        self.last_dir = os.path.dirname(out_path)
        self._update_status()
        msg = f"已生成 {page_count} 页"
        if skipped:
            msg += f"（跳过 {skipped} 张无法读取的图片）"
        msg += f"\n\n{out_path}"
        self.status_var.set(f"完成：{out_path}")
        messagebox.showinfo(APP_TITLE, msg)


def main():
    root = tk.Tk()
    ImageToPdfApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()