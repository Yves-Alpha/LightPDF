#!/usr/bin/env python3
"""Quick test of the 3 profiles"""

import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).parent))

from app import CompressionProfile, vector_compress_pdf, clean_pdf

# Test with the problematic PDF
test_pdf = Path("/Users/yvesnowak/Documents/TEST APP/OP03-G20-AFF-480x680.pdf")

if not test_pdf.exists():
    print(f"❌ PDF not found: {test_pdf}")
    sys.exit(1)

print(f"✅ PDF found: {test_pdf}")
print(f"   Size: {test_pdf.stat().st_size:,} bytes\n")

with tempfile.TemporaryDirectory() as tmpdir:
    tmpdir = Path(tmpdir)
    
    # Step 1: Clean PDF
    clean_path = tmpdir / "test-clean.pdf"
    clean_pdf(test_pdf, clean_path, bleed_mm=5.0)
    clean_size = clean_path.stat().st_size
    print(f"✅ clean_pdf OK -> {clean_size:,} bytes\n")
    
    # Step 2: Test all 3 profiles
    profiles = [
        CompressionProfile("Nettoyer", dpi=0, quality=0),
        CompressionProfile("Moyen", dpi=0, quality=0),
        CompressionProfile("Très légers", dpi=96, quality=60),
    ]
    
    print("Testing compression profiles:")
    print("-" * 60)
    
    for profile in profiles:
        out_path = tmpdir / f"test-{profile.name}.pdf"
        try:
            vector_compress_pdf(clean_path, out_path, profile)
            size = out_path.stat().st_size
            ratio = (clean_size - size) / clean_size * 100
            print(f"✅ {profile.name:20} -> {size:,} bytes ({ratio:+.1f}%)")
        except Exception as e:
            print(f"❌ {profile.name:20} FAILED: {e}")
    
    print("-" * 60)
