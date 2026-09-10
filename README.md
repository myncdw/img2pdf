# 图片转 PDF

一个极简的深色主题 GUI 小工具：选一个文件夹，把它里面的图片按文件名顺序合成一个 PDF。

使用 Python 标准库 `tkinter` 绘制界面，`Pillow` 负责图片处理，`xdg_dialogs` 提供原生文件/文件夹选择对话框。

## 依赖

本项目依赖 [xdg-dialogs](https://github.com/myncdw/xdg_dialogs)（未发布到 PyPI），请先获取

## 功能

- **一键合成**：选择文件夹后自动扫描其中的图片，按文件名**自然排序**（`file2` 排在 `file10` 之前），合并为单个 PDF。
- **页面尺寸可调**：
  - 跟随第一张图片
  - 最宽的那张图片
  - A4（300 DPI，2480 × 3508 像素）
  - 自定义宽度（像素）
- **图片放置方式**：居中 / 左上 / 右上 / 左下 / 右下 / 拉伸，共 6 种。
- **后台转换**：转换过程在后台线程执行，界面不会卡死。
- **深色界面**：统一配色的深色主题，观感清爽。
- **跨平台**：Windows / Linux 均可运行。

## 界面说明

| 步骤 | 说明 |
| --- | --- |
| ① 选择图片文件夹 | 选择图片目录，列表会显示扫描到的图片（已排序） |
| ② 页面尺寸设置 | 选择页面宽度模式；选“自定义宽度”时可输入像素值 |
| ③ 图片放置方式 | 图片在页面中的位置，或拉伸填满整页 |
| ④ 输出设置 | PDF 保存路径，默认是所选文件夹下的 `output.pdf` |

> 说明：选择「自定义宽度」且文件夹中**只有一张图片**时，会弹出对话框额外询问页面高度；多张图片时，页面高度默认使用 A4 高度。

## 支持的图片格式

`.jpg` `.jpeg` `.png` `.bmp` `.gif` `.tif` `.tiff` `.webp`

无法读取或格式不受支持的图片会被自动跳过，并在完成提示中告知跳过数量。

## 环境要求

- Python 3.8+
- Linux 需要 `tkinter`：Debian/Ubuntu 下为 `sudo apt install python3-tk`，Fedora 下为 `sudo dnf install python3-tkinter`
- 文件对话框依赖：Linux 上会优先调用 `kdialog` / `zenity` / `yad`，都没有时回退到 tkinter

## 安装

1. 获取 `xdg-dialogs`（与上文的 [mp4-m4a-merger](https://github.com/myncdw/mp4-m4a-merger) 使用同一份依赖）：

   ```bash
   git clone https://github.com/myncdw/xdg_dialogs.git
   ```

2. 创建并激活虚拟环境，然后安装依赖（`xdg-dialogs` 用可编辑模式安装，便于同步修改）：

   ```bash
   cd 图片转pdf
   python3 -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate

   # 安装 xdg-dialogs（在 xdg_dialogs 仓库根目录执行）
   pip install -e /path/to/xdg_dialogs --config-settings editable_mode=compat

   # 安装本项目其余依赖
   pip install -r requirements.txt
   ```

## 使用

### Linux

```bash
./run.sh            # 前台运行（显示输出）
./run.sh --bg       # 后台运行（适合定时任务 / 桌面快捷方式）
./run.sh --check    # 只打印脚本与解释器选择结果，用于排查
```

`run.sh` 会自动选择要运行的脚本（优先 `main.py` / `main.pyw`）和解释器（优先 `.venv`，否则系统 `python3`）。

### 桌面快捷方式

把 `运行.desktop` 复制到 `~/.local/share/applications/`（或桌面）即可双击启动；注意其中的 `Exec` 必须是本项目的**绝对路径**。

```bash
cp 运行.desktop ~/.local/share/applications/
```

### Windows

双击 `启动.vbs`，它会自动定位虚拟环境并启动 `main.py`。

### 手动运行

```bash
python main.py
```

## 项目结构

```
图片转pdf/
├── main.py            # 主程序（GUI + 转换逻辑）
├── requirements.txt   # Python 依赖
├── run.sh             # Linux 启动脚本
├── 启动.vbs            # Windows 启动脚本
├── 运行.desktop        # Linux 桌面快捷方式模板
└── README.md
```

## 已知行为

- 页面尺寸对整份 PDF 统一生效，每页尺寸相同。
- 非「拉伸」模式下图片保持原始像素大小粘贴到页面上；超出页面的部分会被裁剪掉，不会自动缩放。
- 输出 PDF 的分辨率固定为 96 DPI。
- 文件名排序采用自然排序，数字部分按数值大小比较。

## 常见问题

**Q：提示“未找到支持的图片”？**
A：确认所选文件夹内有上述格式的图片，且文件扩展名正确。

**Q：选择文件夹时没有反应？**
A：确保已正确安装 `xdg-dialogs`（见「安装」一节）。可执行 `python -c "import xdg_dialogs"` 验证；若报错，说明该依赖未安装到当前虚拟环境。

**Q：生成的 PDF 图片被切掉了一部分？**
A：页面尺寸小于图片尺寸时会出现裁剪。可改用「最宽的那张图片」「A4」或更大的自定义宽度，或选择「拉伸」。

## 许可

未声明，如需使用请自行确认 [xdg-dialogs](https://github.com/myncdw/xdg_dialogs) 的许可条款。
