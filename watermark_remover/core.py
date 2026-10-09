from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import fitz
import numpy as np
from PIL import Image, ImageDraw

try:
    import cv2
except ImportError:  # Vector protection still works when OpenCV is unavailable.
    cv2 = None


@dataclass(frozen=True)
class RemovalOptions:
    """Controls raster-based removal of light, neutral-colour watermarks."""

    dpi: int = 180
    lightness_threshold: int = 180
    neutral_tolerance: int = 20
    jpeg_quality: int = 92
    protect_tables: bool = True
    searchable_ocr: bool = True
    ocr_language: str = "chi_sim+eng"


@dataclass
class RemovalResult:
    output_path: Path
    pages: int
    removed_pixels: int
    total_pixels: int
    warnings: list[str] = field(default_factory=list)

    @property
    def removed_ratio(self) -> float:
        return self.removed_pixels / self.total_pixels if self.total_pixels else 0.0


def _validate_options(options: RemovalOptions) -> None:
    if not 96 <= options.dpi <= 300:
        raise ValueError("DPI 必须在 96 到 300 之间")
    if not 80 <= options.lightness_threshold <= 245:
        raise ValueError("水印亮度阈值必须在 80 到 245 之间")
    if not 0 <= options.neutral_tolerance <= 80:
        raise ValueError("灰色容差必须在 0 到 80 之间")
    if not 60 <= options.jpeg_quality <= 100:
        raise ValueError("JPEG 质量必须在 60 到 100 之间")


def _point_xy(point: fitz.Point, scale: float) -> tuple[int, int]:
    return round(point.x * scale), round(point.y * scale)


def _pdf_color(value: tuple[float, ...] | None, default: tuple[int, int, int] | None = None) -> tuple[int, int, int] | None:
    if value is None:
        return default
    if len(value) == 1:
        grey = round(value[0] * 255)
        return grey, grey, grey
    return tuple(round(component * 255) for component in value[:3])


def _restore_vector_drawings(
    rgb: np.ndarray,
    original_rgb: np.ndarray,
    page: fitz.Page,
    dpi: int,
) -> np.ndarray:
    """Restore flat fills and redraw vector lines without painting over text."""
    scale = dpi / 72
    page_area = max(page.rect.width * page.rect.height, 1)
    paths = page.get_drawings()

    # A filled PDF rectangle is commonly used behind inline code and code
    # blocks. Repainting that rectangle after raster cleanup would cover text
    # drawn above it. Instead, restore only pixels that were already close to
    # the vector fill colour in the original raster. Dark foreground text is
    # therefore retained, while differently coloured watermark strokes are not
    # copied back with the background.
    for path in paths:
        path_rect = fitz.Rect(path["rect"])
        fill = _pdf_color(path.get("fill"))
        if fill is None or path_rect.get_area() >= page_area * 0.30:
            continue
        shape = Image.new("L", (rgb.shape[1], rgb.shape[0]), 0)
        shape_draw = ImageDraw.Draw(shape)
        for item in path.get("items", []):
            kind = item[0]
            if kind == "re":
                rect = item[1]
                box = (round(rect.x0 * scale), round(rect.y0 * scale), round(rect.x1 * scale), round(rect.y1 * scale))
                shape_draw.rectangle(box, fill=255)
            elif kind == "qu":
                quad = item[1]
                points = [_point_xy(point, scale) for point in (quad.ul, quad.ur, quad.lr, quad.ll)]
                shape_draw.polygon(points, fill=255)
        shape_mask = np.asarray(shape) > 0
        fill_rgb = np.asarray(fill, dtype=np.int16)
        close_to_fill = np.max(np.abs(original_rgb.astype(np.int16) - fill_rgb), axis=2) <= 12
        restore = shape_mask & close_to_fill
        rgb[restore] = original_rgb[restore]

    canvas = Image.fromarray(rgb, mode="RGB")
    draw = ImageDraw.Draw(canvas)
    for path in paths:
        width = max(1, round(float(path.get("width") or 0.5) * scale))
        stroke = _pdf_color(path.get("color"))
        for item in path.get("items", []):
            kind = item[0]
            if kind == "l" and stroke is not None:
                draw.line([_point_xy(item[1], scale), _point_xy(item[2], scale)], fill=stroke, width=width)
            elif kind == "re":
                rect = item[1]
                box = (round(rect.x0 * scale), round(rect.y0 * scale), round(rect.x1 * scale), round(rect.y1 * scale))
                if stroke is not None:
                    draw.rectangle(box, outline=stroke, width=width)
            elif kind == "qu":
                quad = item[1]
                points = [_point_xy(point, scale) for point in (quad.ul, quad.ur, quad.lr, quad.ll)]
                if stroke is not None:
                    draw.polygon(points, outline=stroke)
            elif kind == "c" and stroke is not None:
                # Pillow has no direct cubic PDF path primitive. Connecting the
                # control points is sufficient to retain thin chart curves.
                points = [_point_xy(point, scale) for point in item[1:]]
                draw.line(points, fill=stroke, width=width)
    return np.asarray(canvas, dtype=np.uint8).copy()


def _raster_line_protection_mask(rgb: np.ndarray, neutral_tolerance: int) -> np.ndarray:
    """Protect long horizontal and vertical lines in scanned/image tables."""
    if cv2 is None:
        return np.zeros(rgb.shape[:2], dtype=bool)
    darkest = rgb.min(axis=2)
    lightest = rgb.max(axis=2)
    neutral = ((lightest - darkest <= neutral_tolerance) & (darkest < 250)).astype(np.uint8) * 255
    height, width = neutral.shape
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(18, width // 35), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(18, height // 45)))
    horizontal = cv2.morphologyEx(neutral, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(neutral, cv2.MORPH_OPEN, vertical_kernel)
    return cv2.bitwise_or(horizontal, vertical) > 0


def _page_protection_mask(page: fitz.Page, pixmap: fitz.Pixmap, rgb: np.ndarray, options: RemovalOptions) -> np.ndarray:
    if not options.protect_tables:
        return np.zeros(rgb.shape[:2], dtype=bool)
    return _raster_line_protection_mask(rgb, options.neutral_tolerance)


def _clean_pixmap(
    pixmap: fitz.Pixmap,
    options: RemovalOptions,
    protection_mask: np.ndarray | None = None,
    page: fitz.Page | None = None,
) -> tuple[bytes, int, int]:
    channels = pixmap.n
    samples = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, channels)
    rgb = samples[:, :, :3].copy()
    darkest = rgb.min(axis=2)
    lightest = rgb.max(axis=2)
    # Neutral light pixels are typical grey text watermarks. Pure white is kept
    # out of the count so the report reflects actual changed pixels.
    mask = (
        (lightest - darkest <= options.neutral_tolerance)
        & (darkest >= options.lightness_threshold)
        & (lightest < 255)
    )
    if protection_mask is not None:
        if protection_mask.shape != mask.shape:
            raise ValueError("保护掩码尺寸与页面不一致")
        mask &= ~protection_mask
    removed = int(mask.sum())
    rgb[mask] = 255
    if page is not None and options.protect_tables:
        rgb = _restore_vector_drawings(rgb, samples[:, :, :3], page, options.dpi)

    image = Image.fromarray(rgb, mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=options.jpeg_quality, optimize=True, progressive=True)
    return buffer.getvalue(), removed, pixmap.width * pixmap.height


def _append_page(
    output: fitz.Document,
    source_page: fitz.Page,
    image_bytes: bytes,
    options: RemovalOptions,
    warnings: list[str],
) -> None:
    if options.searchable_ocr:
        try:
            pixmap = fitz.Pixmap(image_bytes)
            pixmap.set_dpi(options.dpi, options.dpi)
            ocr_bytes = pixmap.pdfocr_tobytes(language=options.ocr_language, compress=True)
            ocr_document = fitz.open("pdf", ocr_bytes)
            # Keep the source media box exactly. Tesseract derives its page size
            # from integer raster dimensions, which can otherwise introduce a
            # small rounding difference in the output PDF.
            target_page = output.new_page(width=source_page.rect.width, height=source_page.rect.height)
            target_page.show_pdf_page(target_page.rect, ocr_document, 0, keep_proportion=False)
            ocr_document.close()
            return
        except Exception as exc:
            warnings.append(f"第 {source_page.number + 1} 页 OCR 失败，已保留清理后的图像页：{exc}")

    target_page = output.new_page(width=source_page.rect.width, height=source_page.rect.height)
    target_page.insert_image(target_page.rect, stream=image_bytes, keep_proportion=False)


def remove_watermarks(
    input_path: str | Path,
    output_path: str | Path,
    *,
    options: RemovalOptions | None = None,
) -> RemovalResult:
    """Remove light grey watermarks while preserving dark text and colour content."""

    options = options or RemovalOptions()
    _validate_options(options)
    source = Path(input_path).expanduser().resolve()
    target = Path(output_path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("输入文件必须是存在的 PDF 文件")
    if target.suffix.lower() != ".pdf":
        target = target.with_suffix(".pdf")
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        document = fitz.open(source)
    except Exception as exc:
        raise ValueError(f"无法打开 PDF：{exc}") from exc
    if document.needs_pass:
        document.close()
        raise ValueError("PDF 已加密，请先移除密码保护")

    output = fitz.open()
    warnings: list[str] = []
    removed_pixels = 0
    total_pixels = 0
    matrix = fitz.Matrix(options.dpi / 72, options.dpi / 72)
    try:
        for page in document:
            pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
            samples = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
            rgb = samples[:, :, :3]
            protection_mask = _page_protection_mask(page, pixmap, rgb, options)
            image_bytes, removed, total = _clean_pixmap(pixmap, options, protection_mask, page)
            removed_pixels += removed
            total_pixels += total
            _append_page(output, page, image_bytes, options, warnings)

        metadata = dict(document.metadata or {})
        metadata["producer"] = "PDF Watermark Cleaner"
        output.set_metadata({key: value for key, value in metadata.items() if isinstance(value, str)})
        output.save(target, garbage=4, deflate=True)
        pages = len(document)
    finally:
        output.close()
        document.close()

    # Reopen to ensure the output is structurally readable before returning it.
    try:
        checked = fitz.open(target)
        if len(checked) != pages:
            raise ValueError("输出 PDF 页数与原文件不一致")
        checked.close()
    except Exception as exc:
        raise ValueError(f"输出 PDF 校验失败：{exc}") from exc

    return RemovalResult(target, pages, removed_pixels, total_pixels, warnings)
