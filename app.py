#!/usr/bin/env python3
"""
Roto PDF Converter – standalone helper to clean press-ready PDFs
(no crop marks, no bleed) and output two compressed variants (HQ + Light).

Usage (from repo root):
    python LightPDF/app.py input1.pdf input2.pdf \
        --bleed-mm 3 --hq-dpi 300 --lite-dpi 150
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable, Tuple
import tempfile

# --- bootstrap: ensure deps are present (auto-installs into Application Support) ---

# Ensure Homebrew PATH is visible for poppler
os.environ["PATH"] = "/usr/local/bin:/opt/homebrew/bin:" + os.environ.get("PATH", "")


def _detect_app_name() -> str:
    # If run inside a .app bundle, use its name; otherwise fallback to RotoConverter.
    for parent in Path(__file__).resolve().parents:
        if parent.suffix == ".app":
            return parent.stem
    return "RotoConverter"


def _default_app_support() -> Path:
    app_name = os.environ.get("ROTO_APP_NAME") or _detect_app_name()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    return base / app_name


APP_SUPPORT_DIR = Path(os.environ.get("ROTO_APP_SUPPORT_DIR", _default_app_support()))
try:
    SITE_PACKAGES = APP_SUPPORT_DIR / "site-packages"
    SITE_PACKAGES.mkdir(parents=True, exist_ok=True)
    if str(SITE_PACKAGES) not in sys.path:
        sys.path.insert(0, str(SITE_PACKAGES))
except OSError:
    # Streamlit Cloud or read-only filesystem — deps are in requirements.txt
    SITE_PACKAGES = None


def _missing_modules(mods: Iterable[str]) -> list[str]:
    return [m for m in mods if importlib.util.find_spec(m) is None]


def _install_deps(mods: Iterable[str]) -> None:
    if SITE_PACKAGES is None:
        # On Cloud, deps come from requirements.txt — skip install
        return
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--no-warn-script-location",
        "--target",
        str(SITE_PACKAGES),
        *mods,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log_path = APP_SUPPORT_DIR / "install_error.log"
        try:
            log_path.write_text(result.stdout + "\n" + result.stderr)
        except OSError:
            pass
        raise RuntimeError(
            f"Installation des dépendances échouée (voir {log_path}). Commande: {' '.join(cmd)}"
        )


def ensure_deps() -> None:
    required = ["pdf2image", "reportlab", "Pillow", "pikepdf"]
    missing = _missing_modules(required)
    if missing:
        _install_deps(missing)


def warn_pdftoppm() -> None:
    if sys.platform == "darwin" and not shutil.which("pdftoppm"):
        warnings.warn("pdftoppm (poppler) est absent du PATH. Installez poppler via brew si nécessaire.")


def find_ghostscript() -> Path | None:
    candidates = [
        shutil.which("gs"),
        "/usr/bin/gs",
        "/opt/homebrew/bin/gs",
        "/usr/local/bin/gs",
    ]
    for cand in candidates:
        if cand and Path(cand).exists():
            return Path(cand)
    return None


def has_ghostscript() -> bool:
    return find_ghostscript() is not None


def find_pdftops() -> Path | None:
    candidates = [
        shutil.which("pdftops"),
        "/usr/bin/pdftops",
        "/opt/homebrew/bin/pdftops",
        "/usr/local/bin/pdftops",
    ]
    for cand in candidates:
        if cand and Path(cand).exists():
            return Path(cand)
    return None


def find_qpdf() -> Path | None:
    candidates = [
        shutil.which("qpdf"),
        "/usr/bin/qpdf",
        "/opt/homebrew/bin/qpdf",
        "/usr/local/bin/qpdf",
    ]
    for cand in candidates:
        if cand and Path(cand).exists():
            return Path(cand)
    return None


try:
    ensure_deps()  # Installe les dépendances manquantes (local macOS uniquement)
except Exception:
    pass  # Sur Streamlit Cloud, les deps viennent de requirements.txt

if sys.platform == "darwin":
    warn_pdftoppm()

try:
    from pdf2image import convert_from_path  # noqa: E402
    from reportlab.pdfgen import canvas  # noqa: E402
    from reportlab.lib.utils import ImageReader  # noqa: E402
except ImportError as e:
    warnings.warn(f"Impossible d'importer les dépendances requises: {e}")
    convert_from_path = None
    canvas = None
    ImageReader = None

try:
    import pikepdf  # noqa: E402
    from PIL import Image as PILImage  # noqa: E402
except Exception:
    pikepdf = None
    PILImage = None

MM_TO_PT = 72 / 25.4


@dataclass
class CompressionProfile:
    name: str
    dpi: int
    quality: int  # JPEG quality (1-95)
    use_vector_compression: bool = False  # If True, use GS compression (keeps vectors/text); if False, rasterize
    image_only: bool = False  # If True, recompress embedded images without rasterizing vectors
    max_bytes: int | None = None
    target_bytes: int | None = None
    output_suffix: str | None = None


class OutputConstraintError(RuntimeError):
    """Raised when a generated file cannot be delivered for a business reason."""


def _rectangle_as_tuple(rect) -> Tuple[float, float, float, float]:
    """Extract (left, bottom, right, top) from a pikepdf rectangle array."""
    # pikepdf.Array or list-like [left, bottom, right, top]
    return float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])


def _pikepdf_pick_trim_box(page, bleed_mm: float) -> Tuple[Tuple[float, float, float, float], str]:
    """
    Choose the box to keep (pikepdf page dict API):
    - TrimBox if present (best indicator of final size)
    - otherwise preserve CropBox/BleedBox/MediaBox as-is.

    The historical implementation removed ``bleed_mm`` blindly when TrimBox
    was missing. Reprocessing an already cropped PDF could therefore remove
    another 5 mm on every side. The argument is kept for API compatibility but
    is deliberately not used as an inferred crop.
    """
    del bleed_mm
    if "/TrimBox" in page:
        base = _rectangle_as_tuple(page["/TrimBox"])
        source = "TrimBox"
    elif "/CropBox" in page:
        base = _rectangle_as_tuple(page["/CropBox"])
        source = "CropBox"
    elif "/BleedBox" in page:
        base = _rectangle_as_tuple(page["/BleedBox"])
        source = "BleedBox"
    else:
        base = _rectangle_as_tuple(page["/MediaBox"])
        source = "MediaBox"

    left, bottom, right, top = base
    if right <= left or top <= bottom:
        raise ValueError(f"Format de page invalide ({source})")
    return (left, bottom, right, top), source


def clean_pdf(input_pdf: Path, output_pdf: Path, bleed_mm: float) -> None:
    """
    Crop PDF to TrimBox using pikepdf (zero corruption).
    Sets MediaBox and CropBox to the computed trim rectangle.
    pikepdf preserves ALL streams byte-for-byte — no re-encoding,
    no JPEG corruption, no font/vector loss.
    """
    if pikepdf is None:
        raise RuntimeError("pikepdf is not available. Install with: pip install pikepdf")

    pdf = pikepdf.Pdf.open(input_pdf)

    for idx, page in enumerate(pdf.pages, start=1):
        rect, source = _pikepdf_pick_trim_box(page, bleed_mm)
        rect_array = pikepdf.Array([float(rect[0]), float(rect[1]),
                                     float(rect[2]), float(rect[3])])
        # Keeping every page box explicit and equal makes the operation
        # idempotent: running LightPDF again cannot crop the page a second time.
        for box_key in ("/MediaBox", "/CropBox", "/TrimBox", "/BleedBox", "/ArtBox"):
            page[box_key] = pikepdf.Array(rect_array)
        print(f"[clean] {input_pdf.name} page {idx}: using {source}")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(output_pdf)
    pdf.close()
    print(f"[clean] written {output_pdf}")


def flatten_transparency_pdf(input_pdf: Path, output_pdf: Path, allow_fallback_14: bool = True) -> str:
    """
    Flatten transparencies using Ghostscript with MINIMAL, SAFE parameters.
    Returns the label of the method that succeeded.
    """
    gs_bin = find_ghostscript()
    if not gs_bin:
        raise RuntimeError("Ghostscript (commande 'gs') est requis pour aplatir les transparences.")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    
    # SINGLE STRATEGY: Minimal, safe Ghostscript parameters
    # No color conversion, no advanced device properties
    cmd = [
        str(gs_bin),
        "-dBATCH",
        "-dNOPAUSE",
        "-dSAFER",
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        "-dAutoRotatePages=/None",
        f"-sOutputFile={output_pdf}",
        str(input_pdf),
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[FLAT] written {output_pdf}")
        return "gs basic"
    
    error_msg = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
    raise RuntimeError(f"Ghostscript a échoué: {error_msg}")



def _recompress_all_images(pdf, jpeg_quality: int = 55, scale: float = 1.0) -> int:
    """Recompress ALL raster images in the PDF using pikepdf + Pillow.

    Iterates every object in the PDF (not just page-level XObjects) to catch
    images nested inside Form XObjects (common in InDesign exports).

    Uses obj.write(data, filter=DCTDecode) for IN-PLACE modification.
    This is the only correct pikepdf API for pre-encoded data.
    (pikepdf.Stream(pdf, data) treats data as DECODED content, which
    double-encodes JPEG bytes and corrupts the file.)

    Parameters:
      jpeg_quality: JPEG quality 1-95 (lower = smaller)
      scale: Downscale factor for images (1.0 = no resize, 0.5 = half)

    Safety rules:
      • Images with /SMask or /Mask are NEVER recompressed. Their pixels,
        colour space and mask form one compositing unit and must remain
        byte-for-byte compatible.
      • Images used by soft masks are NEVER recompressed.
      • Images < 100×100 px are SKIPPED (icons, logos).
      • If recompressed data ≥ original size → SKIPPED.
      • Images with /Decode or /DecodeParms are SKIPPED because those
        entries can be required for correct colour interpretation.
      • Only DeviceRGB and DeviceGray images matching the decoded Pillow
        mode are eligible. CMYK, ICCBased, Indexed and Separation spaces
        are preserved.
      • When scale < 1.0, Width/Height are updated to match new dimensions.

    Returns the number of images successfully recompressed.
    """
    if pikepdf is None or PILImage is None:
        return 0

    # ── Step 1: collect all object IDs that must NOT be recompressed ──
    # Two categories of protected images:
    #
    # A) /SMask and /Mask references on Image objects:
    #    These are alpha/stencil masks that MUST stay lossless.
    #
    # B) Images inside ExtGState Soft Mask /G Form XObjects:
    #    InDesign uses Luminosity soft masks (ExtGState → /SMask dict
    #    → /G Form XObject → /Im0 grayscale image). These masks control
    #    which parts of an element are visible. If we downscale/JPEG
    #    the image but the Form's placement matrix (cm) stays fixed,
    #    the mask no longer covers the right area → renders BLACK.
    #    These images must be skipped entirely.
    smask_objgens: set = set()
    n_objects = len(pdf.objects)

    # A) Direct /SMask references on image streams
    for idx in range(n_objects):
        try:
            obj = pdf.objects[idx]
            if not isinstance(obj, pikepdf.Stream):
                continue
            for mask_key in ("/SMask", "/Mask"):
                mask_ref = obj.get(mask_key, None)
                if isinstance(mask_ref, pikepdf.Stream):
                    smask_objgens.add(mask_ref.objgen)
        except Exception:
            continue

    # B) Images inside ExtGState → /SMask → /G Form XObjects
    for idx in range(n_objects):
        try:
            obj = pdf.objects[idx]
            if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
                continue
            smask_dict = obj.get("/SMask", None)
            if smask_dict is None or isinstance(smask_dict, pikepdf.Name):
                continue
            if not isinstance(smask_dict, pikepdf.Dictionary):
                continue
            g_form = smask_dict.get("/G", None)
            if g_form is None:
                continue
            if g_form.get("/Subtype") != pikepdf.Name.Form:
                continue
            g_res = g_form.get("/Resources", {})
            if not g_res:
                continue
            g_xobjs = g_res.get("/XObject", {})
            for _xname, xobj in g_xobjs.items():
                if xobj.get("/Subtype") == pikepdf.Name.Image:
                    smask_objgens.add(xobj.objgen)
        except Exception:
            continue

    count = 0
    for idx in range(n_objects):
        try:
            obj = pdf.objects[idx]
            if not isinstance(obj, pikepdf.Stream):
                continue
            if obj.get("/Subtype") != pikepdf.Name.Image:
                continue

            # Skip SMask images (alpha masks — must stay lossless)
            if obj.objgen in smask_objgens:
                continue

            # The image and its transparency mask are a compositing unit.
            # Re-encoding/downscaling either side can produce black boxes
            # or halos in InDesign exports, even when dimensions still match.
            if "/SMask" in obj or "/Mask" in obj:
                continue

            w = int(obj.get("/Width", 0))
            h = int(obj.get("/Height", 0))
            if w < 100 or h < 100:
                continue

            # Skip FlateDecode images — only recompress already-JPEG.
            # Converting FlateDecode→JPEG corrupts rendering of CMYK
            # images composited with Soft Masks (Luminosity masks in
            # ExtGState). The JPEG re-encoding changes how the PDF viewer
            # interprets the colour data in the transparency blend,
            # causing elements to render black. Already-JPEG images are
            # safe to re-encode at lower quality.
            cur_filter = obj.get("/Filter", None)
            if str(cur_filter) != "/DCTDecode":
                continue

            # Decode settings can carry essential inversion or JPEG colour
            # transform semantics. Never discard or reinterpret them.
            if "/Decode" in obj or "/DecodeParms" in obj:
                continue

            # Recompress only simple device spaces. Pillow may colour-convert
            # ICCBased/Indexed/Separation images while decoding them, so
            # writing those pixels back under the original PDF colour space
            # would change their appearance.
            color_space = obj.get("/ColorSpace", None)
            if color_space == pikepdf.Name.DeviceRGB:
                expected_mode = "RGB"
            elif color_space == pikepdf.Name.DeviceGray:
                expected_mode = "L"
            else:
                continue

            # Decode the image pixels
            try:
                pil_img = pikepdf.PdfImage(obj).as_pil_image()
            except Exception:
                continue

            if pil_img.mode != expected_mode:
                continue

            # Downscale if requested
            if scale < 1.0:
                new_w = max(1, int(pil_img.width * scale))
                new_h = max(1, int(pil_img.height * scale))
                if new_w < pil_img.width:
                    pil_img = pil_img.resize((new_w, new_h), PILImage.LANCZOS)

            # Encode as JPEG. CMYK is deliberately excluded above because
            # Adobe-style JPEG inversion is interpreted inconsistently by
            # PDF readers and can make images render as colour negatives.
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
            jpeg_data = buf.getvalue()

            # Only replace if the result is actually smaller
            try:
                old_size = len(obj.read_raw_bytes())
            except Exception:
                old_size = len(jpeg_data) + 1
            if len(jpeg_data) >= old_size:
                continue

            # ── IN-PLACE replacement with correct API ──────────────
            obj.write(jpeg_data, filter=pikepdf.Name.DCTDecode)

            # Update dimensions if downscaled
            if scale < 1.0 and pil_img.width != w:
                obj["/Width"] = pil_img.width
                obj["/Height"] = pil_img.height

            count += 1
        except Exception as exc:
            print(f"  [pikepdf] skipping obj {idx}: {exc}")
            continue

    return count


def vector_compress_pdf(input_pdf: Path, output_pdf: Path, profile: CompressionProfile, image_format: str = "jpeg") -> None:
    """
    Execute the safe engine behind each delivery profile.

    Image XObjects are never rewritten individually here. The screen and
    DIAPAR profiles use lossless structural compression; the explicitly light
    profile rasterizes complete pages to a simple RGB PDF.
    """
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    if profile.name == "Nettoyer":
        shutil.copy2(input_pdf, output_pdf)
        print(f"[{profile.name}] copied (no compression) -> {output_pdf}")
    elif profile.name == "Moyen":
        compress_images_only_pdf(input_pdf, output_pdf, profile)
    elif profile.name == "DIAPAR":
        compress_diapar_pdf(input_pdf, output_pdf, profile)
    elif profile.name == "Très légers":
        raster_profile = CompressionProfile(
            name=profile.name,
            dpi=profile.dpi or 150,
            quality=profile.quality or 75,
            max_bytes=profile.max_bytes,
        )
        raster_compress_pdf(input_pdf, output_pdf, raster_profile, image_format=image_format)
    else:
        raise RuntimeError(f"Unknown profile: {profile.name}")

    if profile.max_bytes is not None and output_pdf.stat().st_size > profile.max_bytes:
        actual_size = output_pdf.stat().st_size
        output_pdf.unlink(missing_ok=True)
        raise OutputConstraintError(
            f"Le fichier obtenu fait {actual_size / 1_000_000:.2f} Mo et dépasse la limite de "
            f"{profile.max_bytes / 1_000_000:.0f} Mo. Il doit être repris dans Acrobat avant livraison."
        )


def compress_images_only_pdf(input_pdf: Path, output_pdf: Path, profile: CompressionProfile) -> None:
    """
    Compress PDF structures without decoding or rewriting image pixels.
    """
    qpdf_bin = find_qpdf()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    if qpdf_bin:
        commands = [
            [
                str(qpdf_bin),
                "--stream-data=compress",
                "--recompress-flate",
                "--compression-level=9",
                "--object-streams=generate",
                "--",
                str(input_pdf),
                str(output_pdf),
            ],
            [
                str(qpdf_bin),
                "--stream-data=compress",
                "--",
                str(input_pdf),
                str(output_pdf),
            ],
        ]
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode in (0, 3) and output_pdf.exists():
                if pikepdf is not None:
                    with pikepdf.Pdf.open(output_pdf) as check:
                        page_count = len(check.pages)
                else:
                    page_count = "?"
                print(f"[{profile.name}] lossless qpdf ({page_count} pages) -> {output_pdf}")
                return
            errors.append(result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}")
            output_pdf.unlink(missing_ok=True)

    if pikepdf is not None:
        with pikepdf.Pdf.open(input_pdf) as pdf:
            page_count = len(pdf.pages)
            pdf.save(
                output_pdf,
                compress_streams=True,
                recompress_flate=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
            )
        print(f"[{profile.name}] lossless pikepdf ({page_count} pages) -> {output_pdf}")
        return

    detail = "; ".join(error for error in errors if error)
    raise RuntimeError(f"Compression sans perte indisponible. {detail}".strip())


def compress_diapar_pdf(input_pdf: Path, output_pdf: Path, profile: CompressionProfile) -> None:
    """Create a DIAPAR-ready PDF, preferring a text-preserving result.

    Structural compression is attempted first. Only documents still above the
    delivery target are flattened as complete RGB pages. This avoids the unsafe
    image-by-image rewriting that caused negative CMYK images in the previous
    implementation.
    """
    target_bytes = profile.target_bytes or profile.max_bytes
    delivery_limit = profile.max_bytes or target_bytes
    compress_images_only_pdf(input_pdf, output_pdf, profile)
    # Keep the text-preserving result whenever it already satisfies the real
    # delivery rule. The lower target is headroom for the raster fallback.
    if delivery_limit is None or output_pdf.stat().st_size <= delivery_limit:
        return

    original_size = output_pdf.stat().st_size
    best_path: Path | None = None
    best_size = original_size
    attempts = ((150, 80), (135, 75), (120, 70), (96, 65))

    with tempfile.TemporaryDirectory() as tmpdir:
        for index, (dpi, quality) in enumerate(attempts, start=1):
            candidate = Path(tmpdir) / f"diapar-{index}.pdf"
            raster_profile = CompressionProfile("Très légers", dpi=dpi, quality=quality)
            try:
                raster_compress_pdf(input_pdf, candidate, raster_profile)
            except Exception as exc:
                print(f"[DIAPAR] fallback {dpi} dpi failed: {exc}", file=sys.stderr)
                continue
            candidate_size = candidate.stat().st_size

            if candidate_size < best_size:
                best_size = candidate_size
                best_path = candidate
            if candidate_size <= target_bytes:
                shutil.copy2(candidate, output_pdf)
                print(
                    f"[DIAPAR] target reached at {dpi} dpi, q={quality} "
                    f"({candidate_size / 1_000_000:.2f} MB)"
                )
                return

        if best_path is not None:
            shutil.copy2(best_path, output_pdf)
            print(f"[DIAPAR] smallest automatic result: {best_size / 1_000_000:.2f} MB")



def raster_compress_pdf(input_pdf: Path, output_pdf: Path, profile: CompressionProfile, image_format: str = "jpeg") -> None:
    """
    Rasterize each page then rebuild a PDF with image-compressed pages.
    Keeps page sizes intact so any format is supported.
    ⚠️ This converts pages to images - use when you accept rasterization for compression.
    
    image_format: "jpeg" or "webp"
    """
    if pikepdf is None:
        raise RuntimeError("pikepdf is not available. Install with: pip install pikepdf")
    if convert_from_path is None:
        raise RuntimeError("pdf2image module is not available. Check import.")
    if canvas is None:
        raise RuntimeError("canvas is not available. Check reportlab import.")
    if ImageReader is None:
        raise RuntimeError("ImageReader is not available. Check reportlab import.")
    
    _tmp_pdf = pikepdf.Pdf.open(input_pdf)
    page_count = len(_tmp_pdf.pages)
    _tmp_pdf.close()
    
    # sRGB conversion for file size reduction
    use_srgb = True
    
    # If sRGB conversion needed for rasterized PDF, pre-process with Ghostscript first
    temp_pdf_path = input_pdf
    temp_dir = None
    
    if use_srgb:
        # Use Ghostscript to convert CMYK→RGB before rasterization
        temp_dir = tempfile.TemporaryDirectory()
        temp_pdf_path = Path(temp_dir.name) / "temp_rgb.pdf"
        
        gs_bin = find_ghostscript()
        
        if gs_bin:
            cmd = [
                str(gs_bin),
                "-dBATCH",
                "-dNOPAUSE",
                "-dSAFER",
                "-sDEVICE=pdfwrite",
                "-dProcessColorModel=/DeviceRGB",
                "-dColorConversionStrategy=/RGB",
                f"-sOutputFile={temp_pdf_path}",
                str(input_pdf),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                # Fallback: use original if conversion fails
                temp_pdf_path = input_pdf
    
    _tmp_pdf2 = pikepdf.Pdf.open(temp_pdf_path)
    page_count = len(_tmp_pdf2.pages)
    page_sizes = []
    for page in _tmp_pdf2.pages:
        rect, _source = _pikepdf_pick_trim_box(page, bleed_mm=0)
        page_sizes.append((rect[2] - rect[0], rect[3] - rect[1]))
    _tmp_pdf2.close()
    can = canvas.Canvas(str(output_pdf))

    for idx in range(page_count):
        images = convert_from_path(
            str(temp_pdf_path),
            dpi=profile.dpi,
            use_cropbox=True,
            first_page=idx + 1,
            last_page=idx + 1,
        )
        if not images:
            continue
        img = images[0].convert("RGB")

        source_width, source_height = page_sizes[idx]
        rendered_ratio = img.width / img.height
        normal_delta = abs(rendered_ratio - (source_width / source_height))
        rotated_delta = abs(rendered_ratio - (source_height / source_width))
        if rotated_delta < normal_delta:
            width_pt, height_pt = source_height, source_width
        else:
            width_pt, height_pt = source_width, source_height
        can.setPageSize((width_pt, height_pt))

        buff = BytesIO()
        if image_format.lower() == "webp":
            img.save(buff, format="WEBP", quality=profile.quality, method=6)
        else:
            img.save(buff, format="JPEG", quality=profile.quality, optimize=True)
        buff.seek(0)
        can.drawImage(ImageReader(buff), 0, 0, width=width_pt, height=height_pt)
        can.showPage()
        print(f"[{profile.name}] {input_pdf.name} page {idx + 1}/{page_count} at {profile.dpi} dpi, {image_format.upper()}, q={profile.quality}")
    
    can.save()
    print(f"[{profile.name}] written {output_pdf}")
    
    # Cleanup temporary PDF if created
    if temp_dir:
        temp_dir.cleanup()


def merge_pdfs(pdf_paths: list[Path], merged_path: Path) -> None:
    if pikepdf is None:
        raise RuntimeError("pikepdf is not available. Install with: pip install pikepdf")

    merged = pikepdf.Pdf.new()
    for p in pdf_paths:
        with pikepdf.Pdf.open(p) as src:
            merged.pages.extend(src.pages)
    merged.save(merged_path)
    merged.close()


def process_one(input_pdf: Path, out_dir: Path, bleed_mm: float, profiles: Iterable[CompressionProfile]) -> None:
    base_name = input_pdf.stem
    clean_path = out_dir / f"{base_name}-net.pdf"
    clean_pdf(input_pdf, clean_path, bleed_mm=bleed_mm)

    for profile in profiles:
        output_pdf = out_dir / f"{base_name}-net-{profile.name}.pdf"
        vector_compress_pdf(clean_path, output_pdf, profile)
