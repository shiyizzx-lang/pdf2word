from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .converter import ConversionOptions, convert_pdf


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2md",
        description="将 PDF 转为 Markdown，并按阅读顺序自动提取和插入图片。",
    )
    parser.add_argument("input", type=Path, help="输入 PDF 文件")
    parser.add_argument("-o", "--output", type=Path, help="输出 Markdown 文件，默认与 PDF 同名")
    parser.add_argument("--ocr", metavar="LANG", help="对无文字页面启用 OCR，例如 chi_sim+eng（需安装 Tesseract）")
    parser.add_argument("--keep-margins", action="store_true", help="保留重复页眉和页脚")
    parser.add_argument("--no-page-markers", action="store_true", help="不输出 HTML 页码注释")
    parser.add_argument("--min-image-size", type=int, default=24, metavar="PX", help="忽略宽或高小于此值的图片（默认 24）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = convert_pdf(
            args.input,
            args.output,
            options=ConversionOptions(
                keep_page_markers=not args.no_page_markers,
                remove_repeated_margins=not args.keep_margins,
                min_image_width=args.min_image_size,
                min_image_height=args.min_image_size,
                ocr_language=args.ocr,
            ),
        )
    except (OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(f"已生成：{result.markdown_path}")
    print(f"图片目录：{result.assets_dir}（{result.images} 张）")
    for warning in result.warnings:
        print(f"警告：{warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

