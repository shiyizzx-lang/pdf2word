from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from pdf2md.converter import ConversionOptions, convert_pdf


class ConverterTest(unittest.TestCase):
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
