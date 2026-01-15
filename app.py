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
    required = ["PyPDF2", "pdf2image", "reportlab", "Pillow", "streamlit"]
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


ensure_deps()
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
    Flatten transparencies using Ghostscript while keeping vector data.
    Returns the label of the method that succeeded.
    Implements multiple fallback strategies to handle problematic PDFs.
    """
    gs_bin = find_ghostscript()
    if not gs_bin:
        raise RuntimeError("Ghostscript (commande 'gs') est requis pour aplatir les transparences.")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    
    def _run(cmd: list[str]) -> tuple[int, str]:
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode, (res.stderr.strip() or res.stdout.strip() or "")

    errors: list[str] = []
    
    # Strategy 1: Standard compression parameters
    base_cmd_standard = [
        str(gs_bin),
        "-dBATCH",
        "-dNOPAUSE",
        "-dSAFER",
        "-sDEVICE=pdfwrite",
        "-dDetectDuplicateImages=true",
        "-dCompressFonts=true",
        "-dSubsetFonts=true",
        "-dColorImageDownsampleType=/None",
        "-dGrayImageDownsampleType=/None",
        "-dMonoImageDownsampleType=/None",
        "-dAutoRotatePages=/None",
    ]
    
    attempts_standard: list[tuple[str, list[str]] | None] = [
        ("gs compat 1.3", ["-dCompatibilityLevel=1.3"]),
        ("gs compat 1.4", ["-dCompatibilityLevel=1.4"]) if allow_fallback_14 else None,
        ("gs compat 1.3 + override ICC", ["-dCompatibilityLevel=1.3", "-dOverrideICC", "-dUseFastColor=true"]),
        ("gs compat 1.4 + override ICC", ["-dCompatibilityLevel=1.4", "-dOverrideICC", "-dUseFastColor=true"])
        if allow_fallback_14
        else None,
    ]
    attempts_standard = [a for a in attempts_standard if a is not None]  # type: ignore

    for label, extra in attempts_standard:  # type: ignore
        cmd = [*base_cmd_standard, *extra, f"-sOutputFile={output_pdf}", str(input_pdf)]
        code, msg = _run(cmd)
        if code == 0:
            print(f"[FLAT] written {output_pdf} ({label})")
            return label
        errors.append(f"{label}: {msg or f'code {code}'}")
    
    # Strategy 2: Minimal parameters (to fix rangecheck errors)
    base_cmd_minimal = [
        str(gs_bin),
        "-dBATCH",
        "-dNOPAUSE",
        "-dSAFER",
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.3",
        "-dCompressFonts=true",
        "-dAutoRotatePages=/None",
    ]
    
    cmd_minimal = [*base_cmd_minimal, f"-sOutputFile={output_pdf}", str(input_pdf)]
    code, msg = _run(cmd_minimal)
    if code == 0:
        print(f"[FLAT] written {output_pdf} (minimal parameters)")
        return "minimal parameters"
    errors.append(f"minimal parameters: {msg or f'code {code}'}")
    
    # Strategy 3: qpdf rewrite then minimal gs
    qpdf_bin = find_qpdf()
    if qpdf_bin:
        with tempfile.TemporaryDirectory() as tmpdir:
            qpdf_out = Path(tmpdir) / "qpdf_clean.pdf"
            conv = subprocess.run(
                [
                    str(qpdf_bin),
                    "--object-streams=disable",
                    "--stream-data=uncompress",
                    str(input_pdf),
                    str(qpdf_out),
                ],
                capture_output=True,
                text=True,
            )
            if conv.returncode == 0 and qpdf_out.exists():
                cmd_qpdf_gs = [*base_cmd_minimal, f"-sOutputFile={output_pdf}", str(qpdf_out)]
                code4, msg4 = _run(cmd_qpdf_gs)
                if code4 == 0:
                    print(f"[FLAT] written {output_pdf} (qpdf+gs minimal)")
                    return "qpdf+gs minimal"
                errors.append(f"qpdf+gs minimal: {msg4 or f'code {code4}'}")
            else:
                errors.append(f"qpdf: {conv.stderr.strip() or conv.stdout.strip() or f'code {conv.returncode}'}")

    # Strategy 4: pdftops -> PS -> gs with minimal parameters
    pdftops = find_pdftops()
    if pdftops:
        with tempfile.TemporaryDirectory() as tmpdir:
            ps_path = Path(tmpdir) / "flatten.ps"
            conv = subprocess.run([str(pdftops), str(input_pdf), str(ps_path)], capture_output=True, text=True)
            if conv.returncode == 0 and ps_path.exists():
                cmd_ps = [*base_cmd_minimal, f"-sOutputFile={output_pdf}", str(ps_path)]
                code3, msg3 = _run(cmd_ps)
                if code3 == 0:
                    print(f"[FLAT] written {output_pdf} (pdftops+gs minimal)")
                    return "pdftops+gs minimal"
                errors.append(f"pdftops+gs minimal: {msg3 or f'code {code3}'}")
            else:
                errors.append(f"pdftops: {conv.stderr.strip() or conv.stdout.strip() or f'code {conv.returncode}'}")

    raise RuntimeError("Ghostscript a échoué. " + " | ".join(errors))



def vector_compress_pdf(input_pdf: Path, output_pdf: Path, profile: CompressionProfile) -> None:
    """
    Compress PDF keeping text and vector elements intact using Ghostscript.
    This prevents pixellation unlike rasterization.
    Optimized to preserve shadows and soft effects properly.
    
    Parameters:
    - input_pdf: source PDF file
    - output_pdf: destination PDF file
    - profile: compression settings (quality 1-95, where 95 = highest quality, 10 = maximum compression)
               dpi: image resolution for downsampling (e.g., 150-300 DPI)
    """
    gs_bin = find_ghostscript()
    if not gs_bin:
        raise RuntimeError("Ghostscript est requis pour la compression vectorielle.")
    
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    
    # Use profile DPI and quality directly
    # DPI: controls image resolution (higher = larger file, crisper)
    # quality: controls JPEG compression of images (higher = larger file, better quality)
    img_res = profile.dpi  # Use DPI from profile directly
    jpeg_quality = min(95, max(10, profile.quality))  # Clamp quality to 10-95 range
    
    # Downsampling: DISABLED for Vector profile (to preserve vectors), ENABLED for others
    # DPI tells Ghostscript the target resolution, downsample flag enables downsampling
    downsample_color = "false" if profile.name == "Vector-HQ" else ("true" if profile.name != "HQ" else "false")
    downsample_gray = "false" if profile.name == "Vector-HQ" else ("true" if profile.name != "HQ" else "false")
    
    # sRGB conversion for file size reduction (except for HQ profile)
    use_srgb = profile.name != "HQ"
    
    # Strategy 1: Full-featured compression with all optimizations
    def _build_full_cmd() -> list[str]:
        return [
            str(gs_bin),
            "-dBATCH",
            "-dNOPAUSE",
            "-dSAFER",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.3",  # Force PDF 1.3 for better compression and compatibility
            # Core settings - preserve vector and soft effects
            "-dDetectDuplicateImages=true",
            "-dCompressFonts=true",
            "-dSubsetFonts=true",
            "-dEmbedAllFonts=true",
            
            # Image quality settings - CRITICAL for compression
            f"-dColorImageDownsampleType=/Bicubic",
            f"-dGrayImageDownsampleType=/Bicubic",
            f"-dMonoImageDownsampleType=/Bicubic",
            f"-dColorImageResolution={img_res}",
            f"-dGrayImageResolution={img_res}",
            f"-dMonoImageResolution={img_res}",
            f"-dDownsampleColorImages={downsample_color}",
            f"-dDownsampleGrayImages={downsample_gray}",
            f"-dDownsampleMonoImages=false",
            
            # JPEG compression for images (critical for file size)
            # Lower quality = more compression, smaller file
            f"-dJPEGQ={jpeg_quality}",
            
            # Stream compression
            "-dCompressStreams=true",
            
            # Preserve anti-aliasing and soft edges
            "-dAntiAliasGrayImages=true",
            "-dAntiAliasColorImages=true",
            "-dAntiAliasMonoImages=true",
            
            # Transparency and rendering
            "-dAutoRotatePages=/None",
            "-dPreserveHalftoneInfo=false",
            "-dPreserveOverprintSettings=true",
            "-dTransferFunctionInfo=/Preserve",
            "-dUseFastColor=false",  # Use slower but more accurate color
            
            # Color conversion
            ("-dProcessColorModel=/DeviceRGB" if use_srgb else "-dColorConversionStrategy=/LeaveColorUnchanged"),
            ("-dColorConversionStrategy=/RGB" if use_srgb else "-dProcessColorModel=/DeviceCMYK"),
            
            # Gradient and blend settings
            "-dBlendColorSpace=/DeviceRGB",
            "-dAlignToPixels=0",  # Keep vectors sharp, not pixel-aligned (was causing pixelization)
            
            f"-sOutputFile={output_pdf}",
            str(input_pdf),
        ]
    
    # Strategy 2: Reduced parameters without problematic device properties
    def _build_minimal_cmd() -> list[str]:
        return [
            str(gs_bin),
            "-dBATCH",
            "-dNOPAUSE",
            "-dSAFER",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.3",
            "-dDetectDuplicateImages=true",
            "-dCompressFonts=true",
            "-dSubsetFonts=true",
            "-dEmbedAllFonts=true",
            f"-dColorImageResolution={img_res}",
            f"-dGrayImageResolution={img_res}",
            f"-dJPEGQ={jpeg_quality}",
            "-dCompressStreams=true",
            "-dAutoRotatePages=/None",
            f"-sOutputFile={output_pdf}",
            str(input_pdf),
        ]
    
    # Strategy 3: Ultra-safe with no color model parameters
    def _build_safe_cmd() -> list[str]:
        return [
            str(gs_bin),
            "-dBATCH",
            "-dNOPAUSE",
            "-dSAFER",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.3",
            "-dCompressFonts=true",
            "-dSubsetFonts=true",
            f"-dJPEGQ={jpeg_quality}",
            "-dCompressStreams=true",
            f"-sOutputFile={output_pdf}",
            str(input_pdf),
        ]
    
    # Try strategies in order
    strategies = [
        ("full-featured", _build_full_cmd),
        ("minimal", _build_minimal_cmd),
        ("ultra-safe", _build_safe_cmd),
    ]
    
    errors: list[str] = []
    for strategy_name, builder in strategies:
        cmd = builder()
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"[{profile.name}] {input_pdf.name} compressed with GS using '{strategy_name}' strategy")
            print(f"[{profile.name}] DPI={profile.dpi}, quality={profile.quality}")
            if use_srgb:
                print(f"[{profile.name}] sRGB conversion applied")
            print(f"[{profile.name}] written {output_pdf}")
            
            # Post-processing for sRGB: Use qpdf to force color space conversion if needed
            if use_srgb:
                try:
                    # qpdf can rewrite color spaces - convert any remaining CMYK to RGB
                    temp_output = Path(str(output_pdf) + ".tmp.pdf")
                    qpdf_cmd = [
                        "qpdf",
                        "--stream-data=uncompress",  # Uncompress streams to see color directives
                        "--",
                        str(output_pdf),
                        str(temp_output)
                    ]
                    result = subprocess.run(qpdf_cmd, capture_output=True, text=True, timeout=60)
                    if result.returncode == 0:
                        temp_output.replace(output_pdf)
                        print(f"[{profile.name}] Color space post-processing applied with qpdf")
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    # qpdf not available, continue with GS output only
                    pass
            return
        
        error_msg = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        errors.append(f"{strategy_name}: {error_msg}")
    
    # All strategies failed - report error
    raise RuntimeError(
        f"Ghostscript compression échouée (tous les stratégies):\n" + "\n".join(errors)
    )



def raster_compress_pdf(input_pdf: Path, output_pdf: Path, profile: CompressionProfile) -> None:
    """
    Rasterize each page then rebuild a PDF with JPEG-compressed pages.
    Keeps page sizes intact so any format is supported.
    ⚠️ This causes pixellation - use vector_compress_pdf for text/vectors.
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
    
    # sRGB conversion for file size reduction (except for HQ profile)
    use_srgb = profile.name != "HQ"
    
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
        img.save(buff, format="JPEG", quality=profile.quality, optimize=True)
        buff.seek(0)
        can.drawImage(ImageReader(buff), 0, 0, width=width_pt, height=height_pt)
        can.showPage()
        print(f"[{profile.name}] {input_pdf.name} page {idx + 1}/{page_count} at {profile.dpi} dpi, q={profile.quality}")
    
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
        if profile.use_vector_compression:
            vector_compress_pdf(clean_path, output_pdf, profile)
        else:
            raster_compress_pdf(clean_path, output_pdf, profile)
