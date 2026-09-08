# PDF 转 Markdown

一个完全在本机运行的 PDF 转 Markdown 工具。它会按页面阅读顺序提取文字，把 PDF 中的位图保存到独立资源目录，并在 Markdown 的相应位置自动插入相对链接。

## 功能

- 同时提供命令行和本地网页界面
- 自动提取并插入 PNG、JPEG、WebP 等 PDF 内嵌图片
- 根据字号和字重识别基础标题层级
- 自动过滤在多页重复出现的页眉、页脚和页码
- 支持可选 OCR（系统需安装 Tesseract）
- 不上传文件；网页界面也只监听本机 `127.0.0.1`

## 安装

使用 Python 3.10 或更高版本，在项目目录运行：

```bash
python3 -m pip install --user .
```

如果 `pdf2md` 命令不在系统的 `PATH` 中，下面的示例也可以写成 `python3 -m pdf2md example.pdf`。

## 命令行使用

```bash
pdf2md example.pdf
pdf2md example.pdf -o output/document.md
```

默认会生成：

```text
example.md
example_assets/
  page-001-img-001.png
  page-002-img-001.jpg
```

常用选项：

```bash
pdf2md --help
pdf2md scan.pdf --ocr chi_sim+eng
pdf2md report.pdf --keep-margins --no-page-markers
```

OCR 需要额外安装 Tesseract 及相应语言包。在 Ubuntu/Debian 上可运行：

```bash
sudo apt install tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng
```

## 网页界面

```bash
pdf2md-web
```

也可以运行 `python3 -m pdf2md.web`。然后打开 <http://127.0.0.1:7000>。转换结果会以 ZIP 文件下载，其中包含 Markdown、图片资源，以及必要时生成的转换提示。

## 当前边界

PDF 保存的是页面绘制指令，并不天然包含段落结构，因此复杂多栏排版、公式和表格只能尽量按阅读顺序还原。矢量图形通常不会被当作普通图片提取；扫描页会保留原始页面图像，若还要识别图中的文字请启用 OCR。
