from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app


@unittest.skipIf(app.pikepdf is None, "pikepdf unavailable")
class PageBoxTests(unittest.TestCase):
    def _make_pdf(self, path: Path, *, media, crop=None, trim=None, bleed=None) -> None:
        pdf = app.pikepdf.Pdf.new()
        page = pdf.add_blank_page(page_size=(media[2] - media[0], media[3] - media[1]))
        page["/MediaBox"] = app.pikepdf.Array(media)
        if crop is not None:
            page["/CropBox"] = app.pikepdf.Array(crop)
        if trim is not None:
            page["/TrimBox"] = app.pikepdf.Array(trim)
        if bleed is not None:
            page["/BleedBox"] = app.pikepdf.Array(bleed)
        pdf.save(path)
        pdf.close()

    def test_trimbox_is_the_final_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.pdf"
            output = Path(tmpdir) / "output.pdf"
            self._make_pdf(
                source,
                media=(0, 0, 650, 900),
                crop=(0, 0, 650, 900),
                trim=(25, 25, 625, 875),
                bleed=(10, 10, 640, 890),
            )

            app.clean_pdf(source, output, bleed_mm=5)

            with app.pikepdf.Pdf.open(output) as pdf:
                page = pdf.pages[0]
                for key in ("/MediaBox", "/CropBox", "/TrimBox", "/BleedBox", "/ArtBox"):
                    self.assertEqual(list(page[key]), [25, 25, 625, 875])

    def test_second_pass_does_not_crop_again(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.pdf"
            first = Path(tmpdir) / "first.pdf"
            second = Path(tmpdir) / "second.pdf"
            self._make_pdf(source, media=(0, 0, 600, 800), crop=(0, 0, 600, 800))

            app.clean_pdf(source, first, bleed_mm=5)
            app.clean_pdf(first, second, bleed_mm=5)

            with app.pikepdf.Pdf.open(second) as pdf:
                self.assertEqual(list(pdf.pages[0]["/MediaBox"]), [0, 0, 600, 800])


if __name__ == "__main__":
    unittest.main()
