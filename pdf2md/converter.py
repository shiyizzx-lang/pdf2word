from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz


@dataclass(frozen=True)
class ConversionOptions:
    """Controls Markdown generation."""

    keep_page_markers: bool = True
    remove_repeated_margins: bool = True
    min_image_width: int = 24
    min_image_height: int = 24
    ocr_language: str | None = None
    auto_ocr: bool = True
    detect_tables: bool = True


@dataclass
class ConversionResult:
    markdown_path: Path
    assets_dir: Path
    pages: int
    images: int
    warnings: list[str] = field(default_factory=list)


def _normalise_margin_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip().lower())
    return re.sub(r"\d+", "#", text)


def _repeated_margin_texts(pages: list[dict[str, Any]]) -> set[str]:
    occurrences: Counter[str] = Counter()
    for page in pages:
        height = float(page["height"])
        seen_on_page: set[str] = set()
        for block in page["blocks"]:
            if block.get("type") != 0:
                continue
            y0, y1 = block["bbox"][1], block["bbox"][3]
            if y0 <= height * 0.09 or y1 >= height * 0.91:
                key = _normalise_margin_text(_block_plain_text(block))
                if key:
                    seen_on_page.add(key)
        occurrences.update(seen_on_page)
    threshold = max(2, (len(pages) + 1) // 2)
    return {text for text, count in occurrences.items() if count >= threshold}


def _block_plain_text(block: dict[str, Any]) -> str:
    return " ".join(
        "".join(span.get("text", "") for span in line.get("spans", []))
        for line in block.get("lines", [])
    ).strip()


def _body_font_size(pages: list[dict[str, Any]]) -> float:
    weighted_sizes: Counter[float] = Counter()
    for page in pages:
        for block in page["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        weighted_sizes[round(float(span.get("size", 0)), 1)] += len(text)
    if not weighted_sizes:
        return 11.0
    return float(weighted_sizes.most_common(1)[0][0])


def _escape_markdown(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = re.sub(r"(?<!\\)([\[\]`])", r"\\\1", text)
    return text.strip()


def _line_text(line: dict[str, Any], *, ocr: bool = False) -> str:
    spans = line.get("spans", [])
    if not spans:
        return ""
    result = ""
    previous_x1: float | None = None
    previous_text = ""
    for span in spans:
        current = span.get("text", "")
        if not current:
            continue
        x0 = float(span.get("bbox", (0, 0, 0, 0))[0])
        size = max(float(span.get("size", 10)), 1.0)
        if result and not previous_text.endswith((" ", "-")) and not current.startswith((" ", ",", ".", ":", ";", "!", "?", "，", "。", "：", "；", "！", "？", "、")):
            if ocr:
                if not (_is_cjk(previous_text[-1]) or _is_cjk(current[0])):
                    result += " "
            elif previous_x1 is not None and x0 - previous_x1 > size * 0.18:
                result += " "
        result += current
        previous_x1 = float(span.get("bbox", (0, 0, 0, 0))[2])
        previous_text = current
    return result.strip()


def _is_cjk(character: str) -> bool:
    return bool(character) and (
        "\u3400" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        or "\u3040" <= character <= "\u30ff"
        or "\uac00" <= character <= "\ud7af"
    )


def _join_paragraph_line(previous: str, current: str) -> str:
    if not previous:
        return current
    if previous.endswith("-") and current and current[0].islower():
        return previous[:-1] + current
    if current and (_is_cjk(previous[-1]) or _is_cjk(current[0])):
        return previous + current
    return previous + " " + current


def _is_bold(span: dict[str, Any]) -> bool:
    name = str(span.get("font", "")).lower()
    return "bold" in name or "black" in name or "semibold" in name


def _text_block_to_markdown(block: dict[str, Any], body_size: float, *, ocr: bool = False) -> str:
    lines = [line for line in block.get("lines", []) if _line_text(line, ocr=ocr)]
    if not lines:
        return ""

    texts = [_line_text(line, ocr=ocr) for line in lines]
    spans = [span for line in lines for span in line.get("spans", []) if span.get("text", "").strip()]
    max_size = max((float(span.get("size", body_size)) for span in spans), default=body_size)
    all_bold = bool(spans) and all(_is_bold(span) for span in spans)
    compact = len(" ".join(texts)) <= 180 and len(lines) <= 3

    ratio = max_size / max(body_size, 1)
    heading_level: int | None = None
    if compact and ratio >= 1.75:
        heading_level = 1
    elif compact and ratio >= 1.42:
        heading_level = 2
    elif compact and (ratio >= 1.20 or (all_bold and ratio >= 1.05)):
        heading_level = 3

    escaped = [_escape_markdown(text) for text in texts]
    if heading_level:
        return f"{'#' * heading_level} {' '.join(escaped)}"

    if all(re.match(r"^\s*(?:[-•●▪◦]|\d+[.)])\s+", text) for text in texts):
        return "\n".join(
            re.sub(r"^\s*[•●▪◦]\s+", "- ", text) for text in escaped
        )

    # A line ending in a hyphen usually continues without a space.
    paragraph = escaped[0]
    for text in escaped[1:]:
        paragraph = _join_paragraph_line(paragraph, text)
    return paragraph


def _table_markdown(table: Any) -> str:
    """Return a compact Markdown table, dropping empty trailing rows."""
    rows = table.extract() or []
    cleaned: list[list[str]] = []
    for row in rows:
        values = [str(value or "").replace("|", r"\|").replace("\n", " ").strip() for value in row]
        if any(values):
            cleaned.append(values)
    if not cleaned or not any(cleaned[0]):
        return ""
    columns = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (columns - len(row)) for row in cleaned]
    header = cleaned[0]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in cleaned[1:])
    return "\n".join(lines)


def _extract_tables(page: fitz.Page, warnings: list[str]) -> list[dict[str, Any]]:
    """Find ruled or aligned-text tables with PyMuPDF's native detector."""
    try:
        # PyMuPDF may print an advisory about pymupdf_layout during detection.
        # It is not a conversion warning and should not pollute CLI output.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            finder = page.find_tables(strategy="lines")
            if not finder.tables:
                # The text strategy catches borderless tables whose columns line up.
                finder = page.find_tables(strategy="text")
    except Exception as exc:
        warnings.append(f"第 {page.number + 1} 页表格识别失败：{exc}")
        return []
    tables: list[dict[str, Any]] = []
    for table in finder.tables:
        markdown = _table_markdown(table)
        if markdown:
            tables.append({"type": "table", "bbox": tuple(table.bbox), "markdown": markdown})
    return tables


def _overlaps_table(block: dict[str, Any], table: dict[str, Any]) -> bool:
    x0, y0, x1, y1 = block["bbox"]
    tx0, ty0, tx1, ty1 = table["bbox"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return tx0 - 2 <= cx <= tx1 + 2 and ty0 - 2 <= cy <= ty1 + 2


def _detect_column_groups(blocks: list[dict[str, Any]], page_width: float) -> list[tuple[float, float]]:
    """Detect columns from strong gaps between the left edges of narrow blocks."""
    candidates = [
        block for block in blocks
        if block.get("type") in (0, 1) and block["bbox"][2] - block["bbox"][0] < page_width * 0.72
    ]
    starts = sorted(float(block["bbox"][0]) for block in candidates)
    if len(starts) < 4:
        return []
    groups: list[list[float]] = [[starts[0]]]
    for value in starts[1:]:
        if value - groups[-1][-1] > page_width * 0.14:
            groups.append([value])
        else:
            groups[-1].append(value)
    groups = [group for group in groups if len(group) >= 2]
    if len(groups) < 2 or len(groups) > 3:
        return []
    boundaries: list[tuple[float, float]] = []
    for index, group in enumerate(groups):
        left = min(group) - page_width * 0.05
        right = (min(groups[index + 1]) + min(group)) / 2 if index + 1 < len(groups) else page_width
        boundaries.append((left, right))
    return boundaries


def _ordered_content_blocks(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Order blocks column-by-column while keeping full-width content around them."""
    tables = page.get("_tables", [])
    blocks = [
        block for block in page.get("blocks", [])
        if not any(_overlaps_table(block, table) for table in tables)
    ]
    blocks.extend(tables)
    blocks = [block for block in blocks if block.get("bbox")]
    if not blocks:
        return []
    columns = _detect_column_groups(blocks, float(page["width"]))
    natural = sorted(blocks, key=lambda block: (round(block["bbox"][1], 1), block["bbox"][0]))
    if not columns:
        return natural

    column_blocks: list[list[dict[str, Any]]] = [[] for _ in columns]
    full_width: list[dict[str, Any]] = []
    for block in blocks:
        x0, _, x1, _ = block["bbox"]
        center = (x0 + x1) / 2
        assigned = next((index for index, (left, right) in enumerate(columns) if left <= center < right and x1 - x0 < page["width"] * 0.78), None)
        if assigned is None:
            full_width.append(block)
        else:
            column_blocks[assigned].append(block)
    column_blocks = [sorted(group, key=lambda block: (round(block["bbox"][1], 1), block["bbox"][0])) for group in column_blocks]
    if any(len(group) < 2 for group in column_blocks):
        return natural
    first_column_y = min(group[0]["bbox"][1] for group in column_blocks if group)
    last_column_y = max(group[-1]["bbox"][3] for group in column_blocks if group)
    prefix = sorted([block for block in full_width if block["bbox"][3] <= first_column_y], key=lambda block: block["bbox"][1])
    suffix = sorted([block for block in full_width if block["bbox"][1] >= last_column_y], key=lambda block: block["bbox"][1])
    middle = sorted([block for block in full_width if block not in prefix and block not in suffix], key=lambda block: block["bbox"][1])
    return prefix + [block for group in column_blocks for block in group] + middle + suffix


def _available_ocr_language(requested: str | None) -> str | None:
    if requested:
        return requested
    if not shutil.which("tesseract"):
        return None
    try:
        completed = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=5, check=False)
        languages = set(completed.stdout.splitlines()[1:])
    except (OSError, subprocess.SubprocessError):
        return "eng"
    if "chi_sim" in languages and "eng" in languages:
        return "chi_sim+eng"
    if "eng" in languages:
        return "eng"
    return next(iter(languages), None)


def _page_dict(page: fitz.Page, options: ConversionOptions, page_number: int, warnings: list[str], ocr_language: str | None) -> dict[str, Any]:
    flags = fitz.TEXTFLAGS_DICT | fitz.TEXT_PRESERVE_IMAGES
    page_dict = page.get_text("dict", flags=flags, sort=True)
    text_length = sum(
        len(_block_plain_text(block))
        for block in page_dict.get("blocks", [])
        if block.get("type") == 0
    )

    if text_length < 12 and options.auto_ocr and ocr_language:
        try:
            text_page = page.get_textpage_ocr(language=ocr_language, dpi=200, full=False)
            ocr_dict = page.get_text("dict", textpage=text_page, sort=True)
            image_blocks = [block for block in page_dict.get("blocks", []) if block.get("type") == 1]
            ocr_dict["blocks"] = sorted(
                [*ocr_dict.get("blocks", []), *image_blocks],
                key=lambda block: (round(block["bbox"][1], 1), block["bbox"][0]),
            )
            page_dict = ocr_dict
            page_dict["_ocr"] = True
        except Exception as exc:  # PyMuPDF raises a RuntimeError when Tesseract is absent.
            warnings.append(f"第 {page_number} 页 OCR 失败：{exc}")
    elif text_length < 12:
        warnings.append(f"第 {page_number} 页几乎没有可提取文字，可能是扫描页；未检测到可用 OCR。")

    if options.detect_tables:
        page_dict["_tables"] = _extract_tables(page, warnings)

    return page_dict


def convert_pdf(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    options: ConversionOptions | None = None,
) -> ConversionResult:
    """Convert one PDF to Markdown, extracting raster images beside the document."""

    options = options or ConversionOptions()
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"PDF 文件不存在：{source}")
    if source.suffix.lower() != ".pdf":
        raise ValueError("输入文件必须是 .pdf 文件")

    target = Path(output_path).expanduser() if output_path else source.with_suffix(".md")
    target = target.resolve()
    if target.suffix.lower() != ".md":
        target = target.with_suffix(".md")
    target.parent.mkdir(parents=True, exist_ok=True)
    assets = target.parent / f"{target.stem}_assets"
    assets.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    try:
        document = fitz.open(source)
    except Exception as exc:
        raise ValueError(f"无法打开 PDF：{exc}") from exc
    if document.needs_pass:
        document.close()
        raise ValueError("PDF 已加密，需要先移除密码保护")

    try:
        ocr_language = _available_ocr_language(options.ocr_language)
        if options.auto_ocr and not ocr_language and any(len(page.get_text("text").strip()) < 12 for page in document):
            if options.ocr_language:
                warnings.append("未检测到 Tesseract，无法执行 OCR；请安装 Tesseract 后重试。")
            else:
                warnings.append("检测到疑似扫描页，但未检测到 Tesseract；安装后将自动进行 OCR。")
        pages = [_page_dict(page, options, index + 1, warnings, ocr_language) for index, page in enumerate(document)]
        repeated = _repeated_margin_texts(pages) if options.remove_repeated_margins else set()
        body_size = _body_font_size(pages)
        chunks: list[str] = []
        image_count = 0

        for page_index, page in enumerate(pages, start=1):
            if options.keep_page_markers:
                chunks.append(f"<!-- 第 {page_index} 页 -->")
            page_image_index = 0
            page_height = float(page["height"])
            for block in _ordered_content_blocks(page):
                block_type = block.get("type")
                if block_type == 0:
                    plain = _block_plain_text(block)
                    y0, y1 = block["bbox"][1], block["bbox"][3]
                    in_margin = y0 <= page_height * 0.09 or y1 >= page_height * 0.91
                    if in_margin and _normalise_margin_text(plain) in repeated:
                        continue
                    markdown = _text_block_to_markdown(block, body_size, ocr=bool(page.get("_ocr")))
                    if markdown:
                        chunks.append(markdown)
                elif block_type == "table":
                    chunks.append(block["markdown"])
                elif block_type == 1:
                    width = int(block.get("width", 0))
                    height = int(block.get("height", 0))
                    if width < options.min_image_width or height < options.min_image_height:
                        continue
                    image_bytes = block.get("image")
                    if not image_bytes:
                        continue
                    page_image_index += 1
                    image_count += 1
                    ext = str(block.get("ext") or "png").lower()
                    if ext not in {"png", "jpg", "jpeg", "webp", "tiff", "bmp", "gif", "jp2", "jbig2"}:
                        ext = "png"
                    filename = f"page-{page_index:03d}-img-{page_image_index:03d}.{ext}"
                    image_path = assets / filename
                    image_path.write_bytes(image_bytes)
                    relative = image_path.relative_to(target.parent).as_posix()
                    chunks.append(f"![第 {page_index} 页图片 {page_image_index}](<{relative}>)")

        markdown_text = "\n\n".join(chunks).strip() + "\n"
        target.write_text(markdown_text, encoding="utf-8")
        return ConversionResult(target, assets, len(document), image_count, warnings)
    finally:
        document.close()
