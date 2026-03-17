#!/usr/bin/env python3
"""
docs2image - Convert PDF or PPTX files to PNG images.

Each page is saved as a separate PNG image in a UUID-named output directory.

Usage:
    python -m src.docs2image input.pdf
    python -m src.docs2image presentation.pptx

Dependencies:
    - poppler-utils (apt install poppler-utils) - for PDF/image rendering
    - libreoffice (apt install libreoffice-impress) - for PPTX support
"""

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

# Allow running directly: python src/docs2image.py ...
sys.path.insert(0, str(Path(__file__).parent))

from font_utils import extract_pptx_fonts, check_missing_fonts  # noqa: E402
from lo_export import convert_pptx_to_images, convert_pdf_to_images  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("docs2image")


def main():
    parser = argparse.ArgumentParser(
        description="Convert PDF or PPTX files to PNG images"
    )
    parser.add_argument("input_file", type=str, help="Path to PDF or PPTX file")
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Output image resolution (default: 200)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Base output directory (default: output)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    input_path = Path(args.input_file).resolve()

    if not input_path.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    extension = input_path.suffix.lower()
    if extension not in (".pdf", ".pptx"):
        print(f"Error: Unsupported file type: {extension}", file=sys.stderr)
        print("Supported formats: .pdf, .pptx", file=sys.stderr)
        sys.exit(1)

    session_id = str(uuid.uuid4())
    output_dir = Path(args.output_dir) / session_id
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if extension == ".pptx":
            # Extract and install all embedded fonts
            new_fonts = extract_pptx_fonts(input_path)
            if new_fonts:
                print(f"Installed embedded fonts: {', '.join(new_fonts)}")

            # Check for missing fonts
            missing = check_missing_fonts(input_path)
            if missing:
                print(f"Warning: Missing fonts (will be substituted): {', '.join(missing)}")

            print("Converting PPTX to images...")
            image_paths = convert_pptx_to_images(
                input_path, output_dir, dpi=args.dpi
            )
        else:
            print(f"Converting PDF to images at {args.dpi} DPI...")
            image_paths = convert_pdf_to_images(
                input_path, output_dir, dpi=args.dpi
            )

        print(f"\nConversion complete!")
        print(f"Input: {input_path.name}")
        print(f"Output directory: {output_dir}")
        print(f"Pages converted: {len(image_paths)}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
