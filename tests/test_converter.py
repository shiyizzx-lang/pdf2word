from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from pdf2md.converter import ConversionOptions, convert_pdf


class ConverterTest(unittest.TestCase):
    def test_reads_two_columns_in_column_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "columns.pdf"
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            for y, text in [(100, "Left column first"), (130, "Left column second")]:
                page.insert_text((60, y), text, fontsize=11)
            for y, text in [(100, "Right column first"), (130, "Right column second")]:
                page.insert_text((330, y), text, fontsize=11)
            doc.save(pdf)
            doc.close()

            result = convert_pdf(pdf)
            markdown = result.markdown_path.read_text(encoding="utf-8")

            self.assertLess(markdown.index("Left column first"), markdown.index("Right column first"))
            self.assertLess(markdown.index("Left column second"), markdown.index("Right column first"))

    def test_extracts_ruled_table_as_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "table.pdf"
            doc = fitz.open()
            page = doc.new_page()
            for x in (72, 172, 272):
                page.draw_line((x, 100), (x, 200))
            for y in (100, 130, 160, 200):
                page.draw_line((72, y), (272, y))
            page.insert_text((80, 120), "Name")
            page.insert_text((180, 120), "Score")
            page.insert_text((80, 150), "Alice")
            page.insert_text((180, 150), "95")
            doc.save(pdf)
            doc.close()

            result = convert_pdf(pdf)
            markdown = result.markdown_path.read_text(encoding="utf-8")

            self.assertIn("| Name | Score |", markdown)
            self.assertIn("| Alice | 95 |", markdown)
            self.assertEqual(markdown.count("| Name | Score |"), 1)

    def test_extracts_borderless_aligned_table(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "borderless-table.pdf"
            doc = fitz.open()
            page = doc.new_page()
            for y, left, right in [(100, "Name", "Score"), (120, "Alice", "95"), (140, "Bob", "88")]:
                page.insert_text((72, y), left)
                page.insert_text((200, y), right)
            doc.save(pdf)
            doc.close()

            result = convert_pdf(pdf)
            markdown = result.markdown_path.read_text(encoding="utf-8")

            self.assertIn("| Name | Score |", markdown)
            self.assertIn("| Bob | 88 |", markdown)

    def test_scanned_page_reports_automatic_ocr_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "scan.pdf"
            doc = fitz.open()
            page = doc.new_page()
            pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 120, 160), False)
            pixmap.clear_with(0xFFFFFF)
            page.insert_image(fitz.Rect(40, 40, 500, 700), stream=pixmap.tobytes("png"))
            doc.save(pdf)
            doc.close()

            with patch("pdf2md.converter._available_ocr_language", return_value=None):
                result = convert_pdf(pdf)

            self.assertTrue(any("Tesseract" in warning for warning in result.warnings))
    def test_extracts_text_and_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "sample.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 70), "Sample title", fontsize=24)
            page.insert_text((72, 110), "A short paragraph.", fontsize=11)
            pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 60), False)
            pixmap.clear_with(0x3A7D5C)
            page.insert_image(fitz.Rect(72, 140, 232, 260), stream=pixmap.tobytes("png"))
            doc.save(pdf)
            doc.close()

            result = convert_pdf(pdf)
            markdown = result.markdown_path.read_text(encoding="utf-8")

            self.assertIn("# Sample title", markdown)
            self.assertIn("A short paragraph.", markdown)
            self.assertIn("![第 1 页图片 1]", markdown)
            self.assertEqual(result.images, 1)
            self.assertEqual(len(list(result.assets_dir.iterdir())), 1)

    def test_removes_repeated_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "headers.pdf"
            doc = fitz.open()
            for number in range(1, 4):
                page = doc.new_page()
                page.insert_text((72, 40), "Confidential report", fontsize=9)
                page.insert_text((72, 110), f"Body page {number}", fontsize=11)
            doc.save(pdf)
            doc.close()

            result = convert_pdf(pdf, options=ConversionOptions(remove_repeated_margins=True))
            markdown = result.markdown_path.read_text(encoding="utf-8")

            self.assertNotIn("Confidential report", markdown)
            self.assertIn("Body page 1", markdown)


if __name__ == "__main__":
    unittest.main()
