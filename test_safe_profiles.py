from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app


@unittest.skipIf(app.pikepdf is None, "pikepdf unavailable")
class SafeProfileTests(unittest.TestCase):
    def _make_pdf(self, path: Path) -> None:
        pdf = app.pikepdf.Pdf.new()
        pdf.add_blank_page(page_size=(595, 842))
        pdf.save(path)
        pdf.close()

    def test_screen_profile_never_calls_direct_image_recompression(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.pdf"
            output = Path(tmpdir) / "screen.pdf"
            self._make_pdf(source)
            profile = app.CompressionProfile("Moyen", dpi=0, quality=0)

            with mock.patch.object(
                app,
                "_recompress_all_images",
                side_effect=AssertionError("unsafe image rewriting was called"),
            ):
                app.vector_compress_pdf(source, output, profile)

            self.assertTrue(output.exists())

    def test_hard_size_limit_removes_undeliverable_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.pdf"
            output = Path(tmpdir) / "screen.pdf"
            self._make_pdf(source)
            profile = app.CompressionProfile("Moyen", dpi=0, quality=0, max_bytes=1)

            with self.assertRaises(app.OutputConstraintError):
                app.vector_compress_pdf(source, output, profile)

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
