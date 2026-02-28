#!/usr/bin/env python3
"""
docs2image.py - Convert PDF or PPTX files to PNG images.

Each page is saved as a separate PNG image in a UUID-named output directory.

Usage:
    python docs2image.py input.pdf
    python docs2image.py presentation.pptx

Dependencies:
    - pdf2image (pip install pdf2image)
    - Pillow (pip install Pillow)
    - poppler-utils (apt install poppler-utils)
    - libreoffice (apt install libreoffice) - for PPTX support
"""

import argparse
import os
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

from pdf2image import convert_from_path

SCRIPT_DIR = Path(__file__).parent.resolve()
FONTS_DIR = SCRIPT_DIR / "fonts"


def _deobfuscate_odttf(data: bytes, rel_id: str) -> bytes:
    """Deobfuscate an .odttf font per OOXML spec (ECMA-376 Part 1 §14.2.7.2).

    The first 32 bytes are XOR'd with the font relationship ID GUID (reversed).
    """
    guid_hex = rel_id.strip("{}").replace("-", "")
    key = bytes(int(guid_hex[i:i+2], 16) for i in range(0, 32, 2))
    key = bytes(reversed(key))
    head = bytes(b ^ key[i % 16] for i, b in enumerate(data[:32]))
    return head + data[32:]


def extract_pptx_fonts(pptx_path: Path) -> list[str]:
    """Extract fonts embedded in a PPTX file and install them for LibreOffice.

    Handles both plain (.ttf/.otf) and obfuscated (.odttf) embedded fonts.
    Fonts are saved to BASE_DIR/fonts/ and the font cache is rebuilt only
    when new fonts are found.

    Returns list of newly installed font filenames.
    """
    FONTS_DIR.mkdir(parents=True, exist_ok=True)

    rel_id_map: dict[str, str] = {}
    new_fonts = []

    with zipfile.ZipFile(pptx_path, 'r') as zf:
        rels_path = "ppt/_rels/presentation.xml.rels"
        if rels_path in zf.namelist():
            import xml.etree.ElementTree as ET
            tree = ET.fromstring(zf.read(rels_path))
            ns = "http://schemas.openxmlformats.org/package/2006/relationships"
            for rel in tree.findall(f"{{{ns}}}Relationship"):
                target = rel.get("Target", "")
                rel_id = rel.get("Id", "")
                if "/fonts/" in target and rel_id:
                    rel_id_map[target.lstrip("/")] = rel_id

        font_entries = [
            f for f in zf.namelist()
            if f.startswith("ppt/fonts/") and Path(f).suffix.lower() in (".ttf", ".otf", ".odttf", ".fntdata")
        ]

        for entry in font_entries:
            suffix = Path(entry).suffix.lower()
            stem = Path(entry).stem
            out_name = stem + (".ttf" if suffix == ".odttf" else suffix)
            dest = FONTS_DIR / out_name

            if not dest.exists():
                data = zf.read(entry)
                if suffix == ".odttf":
                    rel_id = rel_id_map.get(entry, "")
                    if rel_id:
                        data = _deobfuscate_odttf(data, rel_id)
                    else:
                        continue
                dest.write_bytes(data)
                new_fonts.append(out_name)

    if new_fonts:
        subprocess.run(["fc-cache", "-f", str(FONTS_DIR)], capture_output=True)

    return new_fonts


def convert_pptx_to_pdf(pptx_path: Path) -> Path:
    """Convert PPTX to PDF using LibreOffice headless mode.

    Args:
        pptx_path: Path to the PPTX file

    Returns:
        Path to the generated PDF file in a temp directory
    """
    temp_dir = tempfile.mkdtemp()

    cmd = [
        "libreoffice",
        "--headless",
        "--convert-to", "pdf",
        "--outdir", temp_dir,
        str(pptx_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"LibreOffice conversion failed: {result.stderr}")

    pdf_name = pptx_path.stem + ".pdf"
    pdf_path = Path(temp_dir) / pdf_name

    if not pdf_path.exists():
        raise RuntimeError(f"PDF was not created: {pdf_path}")

    return pdf_path


def convert_pdf_to_images(pdf_path: Path, output_dir: Path, dpi: int = 200) -> list[Path]:
    """Convert PDF pages to PNG images.

    Args:
        pdf_path: Path to the PDF file
        output_dir: Directory to save images
        dpi: Resolution for output images

    Returns:
        List of paths to generated images
    """
    images = convert_from_path(pdf_path, dpi=dpi)

    output_paths = []
    for i, image in enumerate(images, start=1):
        output_path = output_dir / f"page_{i:03d}.png"
        image.save(output_path, "PNG")
        output_paths.append(output_path)

    return output_paths


def main():
    parser = argparse.ArgumentParser(
        description="Convert PDF or PPTX files to PNG images"
    )
    parser.add_argument(
        "input_file",
        type=str,
        help="Path to PDF or PPTX file"
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Output image resolution (default: 200)"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Base output directory (default: output)"
    )

    args = parser.parse_args()

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

    temp_pdf_path = None

    try:
        if extension == ".pptx":
            new_fonts = extract_pptx_fonts(input_path)
            if new_fonts:
                print(f"Installed embedded fonts: {', '.join(new_fonts)}")
            print(f"Converting PPTX to PDF...")
            temp_pdf_path = convert_pptx_to_pdf(input_path)
            pdf_path = temp_pdf_path
        else:
            pdf_path = input_path

        print(f"Converting pages to images at {args.dpi} DPI...")
        image_paths = convert_pdf_to_images(pdf_path, output_dir, dpi=args.dpi)

        print(f"\nConversion complete!")
        print(f"Input: {input_path.name}")
        print(f"Output directory: {output_dir}")
        print(f"Pages converted: {len(image_paths)}")

    finally:
        if temp_pdf_path and temp_pdf_path.exists():
            temp_pdf_path.unlink()
            temp_pdf_path.parent.rmdir()


if __name__ == "__main__":
    main()
