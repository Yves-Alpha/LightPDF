#!/usr/bin/env python3
"""
Test script for the Ghostscript rangecheck fix.
Demonstrates the fallback strategies when processing problematic PDFs.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app import CompressionProfile, vector_compress_pdf, flatten_transparency_pdf

def test_vector_compression(pdf_path: str, output_dir: str = "./output") -> None:
    """Test vector compression with fallback strategies."""
    input_pdf = Path(pdf_path)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    if not input_pdf.exists():
        print(f"❌ PDF not found: {input_pdf}")
        return
    
    print(f"Testing vector compression on: {input_pdf.name}")
    print("=" * 60)
    
    # Test with HQ profile (full featured - most demanding)
    hq_profile = CompressionProfile(name="HQ", dpi=300, quality=80, use_vector_compression=True)
    try:
        output_hq = output_path / f"{input_pdf.stem}-HQ.pdf"
        print(f"\n1️⃣  Testing HQ profile (300 DPI, quality 80)...")
        vector_compress_pdf(input_pdf, output_hq, hq_profile)
        print(f"✅ HQ compression succeeded: {output_hq}")
    except RuntimeError as e:
        print(f"❌ HQ compression failed: {e}")
    
    # Test with Light profile (minimal parameters)
    light_profile = CompressionProfile(name="Light", dpi=150, quality=50, use_vector_compression=True)
    try:
        output_light = output_path / f"{input_pdf.stem}-Light.pdf"
        print(f"\n2️⃣  Testing Light profile (150 DPI, quality 50)...")
        vector_compress_pdf(input_pdf, output_light, light_profile)
        print(f"✅ Light compression succeeded: {output_light}")
    except RuntimeError as e:
        print(f"❌ Light compression failed: {e}")


def test_flatten_transparency(pdf_path: str, output_dir: str = "./output") -> None:
    """Test transparency flattening with fallback strategies."""
    input_pdf = Path(pdf_path)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    if not input_pdf.exists():
        print(f"❌ PDF not found: {input_pdf}")
        return
    
    print(f"\nTesting transparency flattening on: {input_pdf.name}")
    print("=" * 60)
    
    try:
        output_flat = output_path / f"{input_pdf.stem}-flattened.pdf"
        print(f"Attempting to flatten transparencies...")
        method = flatten_transparency_pdf(input_pdf, output_flat)
        print(f"✅ Flattening succeeded using method: {method}")
        print(f"   Output: {output_flat}")
    except RuntimeError as e:
        print(f"❌ Flattening failed: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_ghostscript_fix.py <pdf_file> [output_dir]")
        print("\nExample:")
        print("  python test_ghostscript_fix.py 'OP03 G20 AFF 480x680.pdf' ./results")
        print("\nThis script will:")
        print("  1. Test vector compression with fallback strategies")
        print("  2. Test transparency flattening with fallback strategies")
        print("  3. Show which strategy succeeded for each operation")
        sys.exit(1)
    
    pdf_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "./output"
    
    print("🧪 Ghostscript Fallback Strategy Test")
    print("=" * 60)
    
    test_vector_compression(pdf_file, output_dir)
    test_flatten_transparency(pdf_file, output_dir)
    
    print("\n" + "=" * 60)
    print("Test completed!")
