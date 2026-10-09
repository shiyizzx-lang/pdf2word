from __future__ import annotations

import io
import shutil
import tempfile
import unittest
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

from watermark_remover.core import RemovalOptions, _clean_pixmap, remove_watermarks


class WatermarkRemoverTest(unittest.TestCase):
    def test_pixel_filter_removes_light_neutral_pixels_only(self) -> None:
        samples = np.array([[[20, 20, 20], [190, 190, 190], [210, 170, 170]]], dtype=np.uint8)
        source = io.BytesIO()
        Image.fromarray(samples, mode="RGB").save(source, format="PNG")
        pixmap = fitz.Pixmap(source.getvalue())
        image_bytes, removed, total = _clean_pixmap(pixmap, RemovalOptions(lightness_threshold=180))
        cleaned = fitz.Pixmap(image_bytes)
        pixels = np.frombuffer(cleaned.samples, dtype=np.uint8).reshape(1, 3, 3)

        self.assertEqual((removed, total), (1, 3))
        self.assertLess(int(pixels[0, 0].max()), 80)
        self.assertGreater(int(pixels[0, 1].min()), 245)
        self.assertLess(int(pixels[0, 2, 0]), 240)

    def test_creates_valid_pdf_with_same_page_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pdf"
            target = root / "cleaned.pdf"
            doc = fitz.open()
            page = doc.new_page(width=400, height=600)
            page.insert_text((60, 100), "Main content", color=(0, 0, 0), fontsize=18)
            page.insert_text((80, 300), "WATERMARK", color=(0.78, 0.78, 0.78), fontsize=34)
            doc.save(source)
            doc.close()

            result = remove_watermarks(source, target, options=RemovalOptions(dpi=120, searchable_ocr=False))
            checked = fitz.open(target)

            self.assertEqual(result.pages, 1)
            self.assertGreater(result.removed_pixels, 0)
            self.assertAlmostEqual(checked[0].rect.width, 400, delta=1)
            self.assertAlmostEqual(checked[0].rect.height, 600, delta=1)
            checked.close()

    def test_preserves_light_grey_vector_table_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "table.pdf"
            target = root / "cleaned.pdf"
            doc = fitz.open()
            page = doc.new_page(width=300, height=300)
            grey = (0.76, 0.76, 0.76)
            for x in (50, 150, 250):
                page.draw_line((x, 80), (x, 220), color=grey, width=1)
            for y in (80, 150, 220):
                page.draw_line((50, y), (250, y), color=grey, width=1)
            page.insert_text((70, 130), "WATERMARK", color=grey, fontsize=20)
            doc.save(source)
            doc.close()

            remove_watermarks(
                source,
                target,
                options=RemovalOptions(dpi=120, lightness_threshold=180, searchable_ocr=False),
            )
            checked = fitz.open(target)
            pixmap = checked[0].get_pixmap(matrix=fitz.Matrix(120 / 72, 120 / 72), colorspace=fitz.csRGB)
            pixels = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)[:, :, :3]
            scale = 120 / 72
            vertical_line = pixels[round(100 * scale):round(200 * scale), round(50 * scale) - 2:round(50 * scale) + 3]
            watermark_area = pixels[round(105 * scale):round(135 * scale), round(68 * scale):round(190 * scale)]

            self.assertLess(float(vertical_line.mean()), 245)
            self.assertGreater(float(watermark_area.mean()), 245)
            checked.close()

    def test_vector_fill_does_not_cover_foreground_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "code-block.pdf"
            target = root / "cleaned.pdf"
            doc = fitz.open()
            page = doc.new_page(width=360, height=220)
            page.draw_rect((40, 60, 320, 170), color=(0.86, 0.86, 0.86), fill=(0.95, 0.95, 0.95), width=0.5)
            page.insert_text((70, 115), "KEEP CODE 123", color=(0, 0, 0), fontsize=24)
            doc.save(source)
            doc.close()

            remove_watermarks(
                source,
                target,
                options=RemovalOptions(dpi=180, lightness_threshold=180, searchable_ocr=False),
            )
            checked = fitz.open(target)
            pixmap = checked[0].get_pixmap(matrix=fitz.Matrix(180 / 72, 180 / 72), colorspace=fitz.csRGB)
            pixels = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)[:, :, :3]
            scale = 180 / 72
            background = pixels[round(75 * scale):round(90 * scale), round(80 * scale):round(280 * scale)]
            text_area = pixels[round(92 * scale):round(120 * scale), round(65 * scale):round(260 * scale)]

            self.assertLess(float(background.mean()), 250)
            self.assertLess(int(text_area.min()), 80)
            checked.close()

    @unittest.skipUnless(shutil.which("tesseract"), "需要安装 Tesseract")
    def test_ocr_layer_is_selectable_and_searchable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pdf"
            target = root / "searchable.pdf"
            doc = fitz.open()
            page = doc.new_page(width=500, height=300)
            page.insert_text((55, 130), "Selectable text 123", color=(0, 0, 0), fontsize=28)
            doc.save(source)
            doc.close()

            result = remove_watermarks(
                source,
                target,
                options=RemovalOptions(dpi=180, searchable_ocr=True, ocr_language="eng"),
            )
            checked = fitz.open(target)
            extracted = checked[0].get_text()

            self.assertEqual(result.warnings, [])
            self.assertIn("Selectable", extracted)
            self.assertIn("123", extracted)
            self.assertTrue(checked[0].search_for("Selectable"))
            self.assertAlmostEqual(checked[0].rect.width, 500, delta=0.1)
            self.assertAlmostEqual(checked[0].rect.height, 300, delta=0.1)
            checked.close()


if __name__ == "__main__":
    unittest.main()
