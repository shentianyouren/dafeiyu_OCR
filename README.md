# 识屏识字

Windows 桌面截屏取字工具。桌面常驻一个悬浮球，点一下拖出区域，文字（以及印刷体英文公式）
直接进剪贴板——看视频、看 PDF、看图片时抠不出文字的场景。

单文件 exe，免安装，不联网，不上传任何截图。

## 下载

到 [Releases](../../releases) 页面下载，两个版本功能一致，只差手写兜底：

| 版本 | 体积 | 适合 |
|---|---|---|
| `识屏识字-精简.exe` | 约 144 MB | 日常截文字、截公式。启动快 2 秒 |
| `识屏识字.exe` | 约 206 MB | 还要截手写笔记、复杂排版 |

双击即可运行，不写注册表，不装服务。配置存在 exe 同目录的 `config.json`。

## 怎么用

启动后桌面右下角出现一个悬浮球（人物 + 对话气泡）：

- **点它** → 屏幕变暗，拖出要识别的区域，松开就识别
- **按 `Ctrl+Shift+O`** → 效果一样，看视频时手不用离开键盘
- **拖动它** → 换位置，位置会记住
- **右键它** → 设置、隐藏、退出

识别完弹一张卡片，内容已经自动进剪贴板，直接 `Ctrl+V` 就能粘。
卡片里可以改错字，然后点「复制」或「复制并关闭」。卡片本身也能拖着走。

## 能识别什么

| 场景 | 引擎 | 耗时 |
|---|---|---|
| 屏幕文字、文档、字幕 | Windows 系统 OCR | **0.05–0.15 秒** |
| 印刷体英文公式 | Texo（ONNX，本地） | **约 0.16 秒** |
| 手写、复杂排版 | RapidOCR（仅完整版） | 1.5–3 秒 |

公式会转成 LaTeX 再放进剪贴板，粘到 Markdown / LaTeX 里直接编译：

```
x={\frac{-b\pm{\sqrt{b^{2}-4ac}}}{2a}}
```

自测 19 张印刷体英文公式（分式、根式、矩阵、积分、级数、希腊字母），19 张全对。
中文公式不支持——本地模型词表里没有汉字，中文内容会走文字通道。

## 实现要点

**抓屏用 DXGI Desktop Duplication**，不是 BitBlt。浏览器、播放器、游戏是 GPU 直接渲染的，
GDI 抓出来是纯黑图。抓取失败才降级到 GDI。

**识别是双通道并行，最后仲裁**：

1. 系统 OCR 先跑，10 毫秒，顺便判断图里有没有汉字
2. 没有汉字就再跑公式模型（0.15 秒）
3. 两边结果都拿到再决定：出现 4 个以上汉字 → 文字；LaTeX 里有关系符或结构命令 → 公式

原来的做法是"先看文字像不像公式再决定跑不跑公式模型"，实测行不通——系统 OCR 会把
`x = (-b±√(b²-4ac))/2a` 读成 `x=-bt` 这种散字，判断直接失灵。

**ONNX 线程数固定 4**。同一套 Texo 模型，onnxruntime 默认按 24 核全开时一张公式要 4–7 秒，
限到 4 线程后 0.16 秒——小图小模型的开销全在线程调度上。

**没有深度学习框架**。公式模型走 onnxruntime，词表 687 个 WordLevel token 自己解析，
不装 torch / transformers / tokenizers / paddle。

## 从源码运行

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run.py
```

需要 Windows 10 1809+（系统 OCR 的 `Windows.Media.Ocr` 从这个版本起自带）。

## 获取模型文件

公式模型（77MB）不进版本库，从 [Releases](../../releases) 下载 `models-texo.zip`，
解压到项目根目录，得到：

```
models/texo/encoder_model.onnx
models/texo/decoder_model.onnx
models/texo/tokenizer.json
```

缺这三个文件时程序照常启动，只是没有公式通道。

## 打包

```
.venv\Scripts\python -m PyInstaller build.spec --noconfirm
```

- 完整版：`dist\识屏识字.exe`
- 精简版：`set SANS_LEAN=1` 后再执行同一条命令，产出 `dist\识屏识字-精简.exe`

`build.spec` 里的 `SANS_LEAN` 开关会把 RapidOCR 和它依赖的 opencv 一起排除（省 63MB）。

## 项目结构

```
app/
  main.py          主流程：悬浮球、热键、任务调度
  capture.py       DXGI 抓屏 + GDI 降级
  selector.py      全屏框选遮罩（脏区域重绘，拖动时不卡）
  floating_ball.py 悬浮球（不抢焦点）
  win_ocr.py       Windows 系统 OCR
  ocr_engine.py    文字双通道 + 公式仲裁
  texo_engine.py   Texo 公式模型（纯 onnxruntime）
  preview.py       结果卡片（可改错字、可拖动）
  hotkey.py        全局热键
  paths.py         路径解析（区分源码运行与打包运行）
build.spec          PyInstaller 配置（含精简版开关）
```

## 已知限制

- **中文公式不行**。本地公式模型只学英文与符号，中文内容会按文字输出。
- **手写弱**。系统 OCR 对手写本来就不稳，完整版的 RapidOCR 也只是"能看"。
- **启动要解压**。PyInstaller 单文件每次运行都要把 200MB 解到临时目录，首启 6–10 秒；
  之后不再重复。这是 onefile 的固有代价。
- 混排单行（`E = mc² roughly`）会被判成公式，把英文也包进 `\mathsf{}`。

## 许可与致谢

本仓库源码以 [MIT](LICENSE) 发布。

用到的第三方组件各自遵循其原许可：

- [Texo](https://github.com/alephpi/Texo)（公式识别，模型 20M 参数）— **AGPL-3.0**
- [RapidOCR](https://github.com/RapidAI/RapidOCR)（手写兜底）— Apache-2.0
- [dxcam](https://github.com/ra1nty/DXcam)（DXGI 抓屏）— MIT
- [PySide6](https://www.qt.io/) — LGPL-3.0

注意公式模型权重来自 Texo，若你要二次分发内置了该模型的 exe，需要一并遵守 AGPL-3.0；
只使用源码或改用其他模型则不受此约束。
