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
import os
import subprocess
import sys
import warnings
import shutil
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
SITE_PACKAGES = APP_SUPPORT_DIR / "site-packages"
SITE_PACKAGES.mkdir(parents=True, exist_ok=True)
if str(SITE_PACKAGES) not in sys.path:
    sys.path.insert(0, str(SITE_PACKAGES))


def _missing_modules(mods: Iterable[str]) -> list[str]:
    return [m for m in mods if importlib.util.find_spec(m) is None]


def _install_deps(mods: Iterable[str]) -> None:
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
        log_path.write_text(result.stdout + "\n" + result.stderr)
        raise RuntimeError(
            f"Installation des dépendances échouée (voir {log_path}). Commande: {' '.join(cmd)}"
        )


def ensure_deps() -> None:
    required = ["PyPDF2", "pdf2image", "reportlab", "Pillow", "streamlit", "pikepdf"]
    missing = _missing_modules(required)
    if missing:
        _install_deps(required)


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


ensure_deps()  # Installe les dépendances manquantes
warn_pdftoppm()

try:
    from PyPDF2 import PdfReader, PdfWriter  # noqa: E402
    from PyPDF2.generic import RectangleObject  # noqa: E402
    from pdf2image import convert_from_path  # noqa: E402
    from reportlab.pdfgen import canvas  # noqa: E402
    from reportlab.lib.utils import ImageReader  # noqa: E402
except ImportError as e:
    warnings.warn(f"Impossible d'importer les dépendances requises: {e}")
    PdfReader = None
    PdfWriter = None
    RectangleObject = None
    convert_from_path = None
    canvas = None
    ImageReader = None

MM_TO_PT = 72 / 25.4


@dataclass
class CompressionProfile:
    name: str
    dpi: int
    quality: int  # JPEG quality (1-95)
    use_vector_compression: bool = False  # If True, use GS compression (keeps vectors/text); if False, rasterize
    image_only: bool = False  # If True, recompress embedded images without rasterizing vectors


def _rectangle_as_tuple(rect) -> Tuple[float, float, float, float]:
    return float(rect.left), float(rect.bottom), float(rect.right), float(rect.top)


def pick_trim_box(page, bleed_mm: float) -> Tuple[Tuple[float, float, float, float], str]:
    """
    Choose the box to keep:
    - TrimBox if present (best indicator of final size)
    - else BleedBox/CropBox/MediaBox trimmed by bleed_mm on each side.
    """
    if "/TrimBox" in page:
        base = _rectangle_as_tuple(page.trimbox)
        source = "TrimBox"
        margin_pt = 0.0
    elif "/BleedBox" in page:
        base = _rectangle_as_tuple(page.bleedbox)
        source = "BleedBox"
        margin_pt = bleed_mm * MM_TO_PT
    elif "/CropBox" in page:
        base = _rectangle_as_tuple(page.cropbox)
        source = "CropBox"
        margin_pt = bleed_mm * MM_TO_PT
    else:
        base = _rectangle_as_tuple(page.mediabox)
        source = "MediaBox"
        margin_pt = bleed_mm * MM_TO_PT

    left, bottom, right, top = base
    if margin_pt:
        left += margin_pt
        bottom += margin_pt
        right -= margin_pt
        top -= margin_pt
    if right <= left or top <= bottom:
        raise ValueError(f"Fonds perdus trop large pour la page ({source})")
    return (left, bottom, right, top), source


def clean_pdf(input_pdf: Path, output_pdf: Path, bleed_mm: float) -> None:
    if RectangleObject is None or PdfReader is None or PdfWriter is None:
        raise RuntimeError("RectangleObject, PdfReader or PdfWriter not available. Check PyPDF2 import.")
    
    reader = PdfReader(str(input_pdf))
    writer = PdfWriter()

    for idx, page in enumerate(reader.pages, start=1):
        rect, source = pick_trim_box(page, bleed_mm)
        rect_obj = RectangleObject(rect)
        page.mediabox = rect_obj
        page.cropbox = rect_obj
        page.trimbox = rect_obj
        page.bleedbox = rect_obj
        writer.add_page(page)
        print(f"[clean] {input_pdf.name} page {idx}: using {source}")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as f:
        writer.write(f)
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



def vector_compress_pdf(input_pdf: Path, output_pdf: Path, profile: CompressionProfile, image_format: str = "jpeg") -> None:
    """
    Handle profiles:
    - "Nettoyer": just copy the cleaned PDF (no compression at all)
    - "Moyen": use qpdf for safe compression (no rasterization, no aberrations)
    - "Très légers": rasterize to JPEG images (variable DPI)
    """
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    
    if profile.name == "Nettoyer":
        # Just copy - no compression, preserve full quality
        shutil.copy2(input_pdf, output_pdf)
        print(f"[{profile.name}] copied (no compression) -> {output_pdf}")
        return
    
    if profile.name == "Moyen":
        # Use Ghostscript for moderate image compression (150 DPI, quality 80)
        gs_bin = find_ghostscript()
        if gs_bin:
            cmd = [
                str(gs_bin),
                "-dBATCH", "-dNOPAUSE", "-dSAFER",
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.4",
                "-dDetectDuplicateImages=true",
                "-dCompressFonts=true",
                "-dSubsetFonts=true",
                "-dColorImageDownsampleType=/Bicubic",
                "-dGrayImageDownsampleType=/Bicubic",
                "-dColorImageResolution=150",
                "-dGrayImageResolution=150",
                "-dMonoImageResolution=150",
                "-dDownsampleColorImages=true",
                "-dDownsampleGrayImages=true",
                "-dDownsampleMonoImages=false",
                "-dJPEGQ=80",
                "-dCompressStreams=true",
                "-dAutoRotatePages=/None",
                f"-sOutputFile={output_pdf}",
                str(input_pdf),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                print(f"[{profile.name}] Ghostscript moderate compression (150 DPI, quality 80) -> {output_pdf}")
                return
            print(f"[{profile.name}] Ghostscript failed: {result.stderr.strip()}, trying qpdf fallback")
        
        # Fallback: qpdf if available
        qpdf_bin = find_qpdf()
        if qpdf_bin:
            qpdf_cmd = [
                str(qpdf_bin),
                "--stream-data=compress",
                "--",
                str(input_pdf),
                str(output_pdf),
            ]
            result = subprocess.run(qpdf_cmd, capture_output=True, text=True)
            if result.returncode in (0, 3):  # 3 = warnings (OK)
                print(f"[{profile.name}] qpdf fallback compression -> {output_pdf}")
                return
        
        raise RuntimeError(f"Compression Moyen impossible : ni Ghostscript ni qpdf disponible.")
    
    if profile.name == "Très légers":
        # Use Ghostscript for aggressive image downsampling (NOT rasterization)
        # This compresses embedded images in the PDF without converting to raster
        # Result: very light file size, acceptable pixellation, ZERO aberrations
        gs_bin = find_ghostscript()
        if gs_bin:
            cmd = [
                str(gs_bin),
                "-dBATCH",
                "-dNOPAUSE",
                "-dSAFER",
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.4",
                "-dDetectDuplicateImages=true",
                "-dCompressFonts=true",
                "-dSubsetFonts=true",
                # Image downsampling - aggressive for small file size
                "-dColorImageDownsampleType=/Bicubic",
                "-dGrayImageDownsampleType=/Bicubic",
                "-dMonoImageDownsampleType=/Bicubic",
                "-dColorImageResolution=96",
                "-dGrayImageResolution=96",
                "-dMonoImageResolution=96",
                "-dDownsampleColorImages=true",
                "-dDownsampleGrayImages=true",
                "-dDownsampleMonoImages=false",
                # JPEG compression - aggressive quality for small file
                "-dJPEGQ=60",
                # Stream compression
                "-dCompressStreams=true",
                "-dAutoRotatePages=/None",
                f"-sOutputFile={output_pdf}",
                str(input_pdf),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                print(f"[{profile.name}] Ghostscript aggressive compression (96 DPI, quality 60) -> {output_pdf}")
                return
            print(f"[{profile.name}] Ghostscript failed: {result.stderr.strip()}, trying qpdf fallback")
        
        # Fallback if Ghostscript unavailable or fails: use qpdf if available
        qpdf_bin = find_qpdf()
        if qpdf_bin:
            qpdf_cmd = [
                str(qpdf_bin),
                "--stream-data=compress",
                "--",
                str(input_pdf),
                str(output_pdf),
            ]
            result = subprocess.run(qpdf_cmd, capture_output=True, text=True)
            if result.returncode in (0, 3):  # 3 = warnings (OK)
                print(f"[{profile.name}] qpdf fallback compression -> {output_pdf}")
                return
        
        raise RuntimeError(f"Compression Très légers impossible : ni Ghostscript ni qpdf disponible.")
    
    # Fallback: should not reach here
    raise RuntimeError(f"Unknown profile: {profile.name}")


def compress_images_only_pdf(input_pdf: Path, output_pdf: Path, profile: CompressionProfile) -> None:
    """
    Recompress PDF while preserving vectors using qpdf.
    Simple and safe - no Ghostscript tricks.
    """
    qpdf_bin = find_qpdf()
    if not qpdf_bin:
        raise RuntimeError("qpdf est requis pour ce profil. Installez via: brew install qpdf")
    
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    
    # Single, stable qpdf command
    qpdf_cmd = [
        str(qpdf_bin),
        "--stream-data=compress",
        "--",
        str(input_pdf),
        str(output_pdf),
    ]
    
    result = subprocess.run(qpdf_cmd, capture_output=True, text=True)
    if result.returncode in (0, 3):  # 3 = warnings (OK)
        print(f"[{profile.name}] qpdf compress")
        print(f"[{profile.name}] written {output_pdf}")
        return
    
    error_msg = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
    raise RuntimeError(f"qpdf compression failed: {error_msg}")



def raster_compress_pdf(input_pdf: Path, output_pdf: Path, profile: CompressionProfile, image_format: str = "jpeg") -> None:
    """
    Rasterize each page then rebuild a PDF with image-compressed pages.
    Keeps page sizes intact so any format is supported.
    ⚠️ This converts pages to images - use when you accept rasterization for compression.
    
    image_format: "jpeg" or "webp"
    """
    if PdfReader is None:
        raise RuntimeError("PdfReader is not available. Check PyPDF2 import.")
    if convert_from_path is None:
        raise RuntimeError("pdf2image module is not available. Check import.")
    if canvas is None:
        raise RuntimeError("canvas is not available. Check reportlab import.")
    if ImageReader is None:
        raise RuntimeError("ImageReader is not available. Check reportlab import.")
    
    reader = PdfReader(str(input_pdf))
    page_count = len(reader.pages)
    
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
    
    reader = PdfReader(str(temp_pdf_path))
    page_count = len(reader.pages)
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

        width_pt = img.width / profile.dpi * 72
        height_pt = img.height / profile.dpi * 72
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
    if PdfReader is None or PdfWriter is None:
        raise RuntimeError("PdfReader or PdfWriter not available. Check PyPDF2 import.")
    
    writer = PdfWriter()
    for p in pdf_paths:
        reader = PdfReader(str(p))
        for page in reader.pages:
            writer.add_page(page)
    with merged_path.open("wb") as f:
        writer.write(f)


def process_one(input_pdf: Path, out_dir: Path, bleed_mm: float, profiles: Iterable[CompressionProfile]) -> None:
    base_name = input_pdf.stem
    clean_path = out_dir / f"{base_name}-net.pdf"
    clean_pdf(input_pdf, clean_path, bleed_mm=bleed_mm)

    for profile in profiles:
        output_pdf = out_dir / f"{base_name}-net-{profile.name}.pdf"
        vector_compress_pdf(clean_path, output_pdf, profile)
