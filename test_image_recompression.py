#!/usr/bin/env python3
"""Regression tests for safe in-place PDF image recompression."""

from __future__ import annotations

import io
import unittest

import app


def _jpeg_bytes(mode: str = "RGB", size: tuple[int, int] = (400, 400)) -> bytes:
    image = app.PILImage.effect_noise(size, 100)
    if mode != "L":
        image = image.convert(mode)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=95)
    return output.getvalue()


def _image_stream(pdf, data: bytes, color_space, size=(400, 400)):
    stream = pdf.make_stream(data)
    stream["/Type"] = app.pikepdf.Name.XObject
    stream["/Subtype"] = app.pikepdf.Name.Image
    stream["/Width"] = size[0]
    stream["/Height"] = size[1]
    stream["/ColorSpace"] = color_space
    stream["/BitsPerComponent"] = 8
    stream["/Filter"] = app.pikepdf.Name.DCTDecode
    return stream


@unittest.skipIf(app.pikepdf is None or app.PILImage is None, "pikepdf/Pillow unavailable")
class ImageRecompressionTests(unittest.TestCase):
    def test_simple_device_rgb_jpeg_is_recompressed(self):
        pdf = app.pikepdf.Pdf.new()
        image = _image_stream(
            pdf, _jpeg_bytes(), app.pikepdf.Name.DeviceRGB
        )
        original_size = len(image.read_raw_bytes())

        count = app._recompress_all_images(pdf, jpeg_quality=30, scale=0.5)

        self.assertEqual(count, 1)
        self.assertEqual((int(image["/Width"]), int(image["/Height"])), (200, 200))
        self.assertLess(len(image.read_raw_bytes()), original_size)

    def test_image_and_soft_mask_are_preserved_byte_for_byte(self):
        pdf = app.pikepdf.Pdf.new()
        image = _image_stream(
            pdf, _jpeg_bytes(), app.pikepdf.Name.DeviceRGB
        )
        mask_data = bytes([255]) * (400 * 400)
        mask = pdf.make_stream(mask_data)
        mask["/Type"] = app.pikepdf.Name.XObject
        mask["/Subtype"] = app.pikepdf.Name.Image
        mask["/Width"] = 400
        mask["/Height"] = 400
        mask["/ColorSpace"] = app.pikepdf.Name.DeviceGray
        mask["/BitsPerComponent"] = 8
        image["/SMask"] = mask
        image_before = image.read_raw_bytes()
        mask_before = mask.read_raw_bytes()

        count = app._recompress_all_images(pdf, jpeg_quality=30, scale=0.5)

        self.assertEqual(count, 0)
        self.assertEqual(image.read_raw_bytes(), image_before)
        self.assertEqual(mask.read_raw_bytes(), mask_before)
        self.assertEqual((int(image["/Width"]), int(image["/Height"])), (400, 400))
        self.assertEqual((int(mask["/Width"]), int(mask["/Height"])), (400, 400))

    def test_decode_semantics_are_preserved_by_skipping_image(self):
        pdf = app.pikepdf.Pdf.new()
        image = _image_stream(
            pdf, _jpeg_bytes("CMYK"), app.pikepdf.Name.DeviceCMYK
        )
        decode = app.pikepdf.Array([1, 0, 1, 0, 1, 0, 1, 0])
        image["/Decode"] = decode
        image_before = image.read_raw_bytes()

        count = app._recompress_all_images(pdf, jpeg_quality=30, scale=0.5)

        self.assertEqual(count, 0)
        self.assertEqual(image.read_raw_bytes(), image_before)
        self.assertEqual(list(image["/Decode"]), list(decode))

    def test_non_device_colour_space_is_preserved(self):
        pdf = app.pikepdf.Pdf.new()
        image = _image_stream(
            pdf,
            _jpeg_bytes(),
            app.pikepdf.Array(
                [app.pikepdf.Name.Indexed, app.pikepdf.Name.DeviceRGB, 255, b""]
            ),
        )
        image_before = image.read_raw_bytes()

        count = app._recompress_all_images(pdf, jpeg_quality=30, scale=0.5)

        self.assertEqual(count, 0)
        self.assertEqual(image.read_raw_bytes(), image_before)
        self.assertEqual((int(image["/Width"]), int(image["/Height"])), (400, 400))


if __name__ == "__main__":
    unittest.main()
