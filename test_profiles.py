#!/usr/bin/env python3
"""Quick test of the 3 profiles"""

import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).parent))

from app import CompressionProfile, vector_compress_pdf, clean_pdf

def main() -> int:
    # Historical manual fixture. This script is intentionally inert when a
    # test runner imports the module.
    test_pdf = Path("/Users/yvesnowak/Documents/TEST APP/OP03-G20-AFF-480x680.pdf")
    if not test_pdf.exists():
        print(f"❌ PDF not found: {test_pdf}")
        return 1

    print(f"✅ PDF found: {test_pdf}")
    print(f"   Size: {test_pdf.stat().st_size:,} bytes\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = Path(tmpdir)
        clean_path = work_dir / "test-clean.pdf"
        clean_pdf(test_pdf, clean_path, bleed_mm=5.0)
        clean_size = clean_path.stat().st_size
        print(f"✅ clean_pdf OK -> {clean_size:,} bytes\n")

        profiles = [
            CompressionProfile("Nettoyer", dpi=0, quality=0),
            CompressionProfile("Moyen", dpi=0, quality=0),
            CompressionProfile("Très légers", dpi=96, quality=60),
        ]
        for profile in profiles:
            out_path = work_dir / f"test-{profile.name}.pdf"
            vector_compress_pdf(clean_path, out_path, profile)
            size = out_path.stat().st_size
            ratio = (clean_size - size) / clean_size * 100
            print(f"✅ {profile.name:20} -> {size:,} bytes ({ratio:+.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
