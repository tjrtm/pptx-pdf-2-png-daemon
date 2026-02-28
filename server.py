#!/usr/bin/env python3
"""
docs2image HTTP service - Convert PDF or PPTX files to PNG images.

Accepts multipart form-data file uploads.
"""

import os
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from pdf2image import convert_from_path

app = FastAPI(title="docs2image", description="PDF/PPTX to PNG converter")

BASE_DIR = Path(__file__).parent.resolve()
OUTPUT_DIR = BASE_DIR / "output"
FONTS_DIR = BASE_DIR / "fonts"

# Configuration via environment variables
HOST = os.environ.get("DOCS2IMAGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("DOCS2IMAGE_PORT", "8085"))
DPI = int(os.environ.get("DOCS2IMAGE_DPI", "200"))


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

    # Build a map of relationship IDs to font files from the PPTX rels
    rel_id_map: dict[str, str] = {}
    new_fonts = []

    with zipfile.ZipFile(pptx_path, 'r') as zf:
        # Parse font relationship IDs from ppt/_rels/presentation.xml.rels
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
            # Determine output filename (deobfuscated odttf saves as .ttf)
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
                        # No rel ID found — skip, can't deobfuscate reliably
                        continue
                dest.write_bytes(data)
                new_fonts.append(out_name)

    if new_fonts:
        subprocess.run(["fc-cache", "-f", str(FONTS_DIR)], capture_output=True)

    return new_fonts


def convert_pptx_to_pdf(pptx_path: Path) -> Path:
    """Convert PPTX to PDF using LibreOffice headless mode."""
    if not pptx_path.exists():
        raise RuntimeError(f"Input file does not exist: {pptx_path}")

    file_size = pptx_path.stat().st_size
    if file_size == 0:
        raise RuntimeError(f"Input file is empty: {pptx_path}")

    temp_dir = tempfile.mkdtemp()

    cmd = [
        "soffice",
        "--headless",
        "--nofirststartwizard",
        "--convert-to", "pdf",
        "--outdir", temp_dir,
        str(pptx_path)
    ]

    env = {**os.environ, "HOME": "/tmp", "XDG_DATA_HOME": str(BASE_DIR)}
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)

    if result.returncode != 0:
        raise RuntimeError(f"LibreOffice failed: {result.stderr}")

    pdf_name = pptx_path.stem + ".pdf"
    pdf_path = Path(temp_dir) / pdf_name

    if not pdf_path.exists():
        raise RuntimeError(
            f"PDF not created. Input: {pptx_path} ({file_size} bytes). "
            f"stderr: {result.stderr}"
        )

    return pdf_path


def convert_pdf_to_images(pdf_path: Path, output_dir: Path, dpi: int = DPI) -> list[Path]:
    """Convert PDF pages to PNG images."""
    images = convert_from_path(pdf_path, dpi=dpi)

    output_paths = []
    for i, image in enumerate(images, start=1):
        output_path = output_dir / f"page_{i:03d}.png"
        image.save(output_path, "PNG")
        output_paths.append(output_path)

    return output_paths


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/convert")
async def convert_document(file: UploadFile = File(...)):
    """
    Convert PDF or PPTX file to PNG images.

    Accepts multipart form-data with a 'file' field.
    Returns JSON with list of image paths.
    """
    try:
        # Validate file extension
        filename = file.filename or "upload"
        ext = Path(filename).suffix.lower()

        if ext not in (".pdf", ".pptx"):
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": f"Unsupported file type: {ext}. Supported: .pdf, .pptx"
                }
            )

        # Read file content
        file_data = await file.read()

        if len(file_data) < 100:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": f"File too small ({len(file_data)} bytes)"
                }
            )

        # Create session directory
        session_id = str(uuid.uuid4())
        session_dir = OUTPUT_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # Save uploaded file to temp location
        temp_dir = tempfile.mkdtemp()
        safe_filename = "input" + ext
        input_path = Path(temp_dir) / safe_filename
        input_path.write_bytes(file_data)

        temp_pdf_path = None

        try:
            # Convert PPTX to PDF if needed
            if ext == ".pptx":
                extract_pptx_fonts(input_path)
                temp_pdf_path = convert_pptx_to_pdf(input_path)
                pdf_path = temp_pdf_path
            else:
                pdf_path = input_path

            # Convert PDF to images
            image_paths = convert_pdf_to_images(pdf_path, session_dir)

            return {
                "success": True,
                "session_id": session_id,
                "images": [str(p) for p in image_paths],
                "count": len(image_paths)
            }

        finally:
            # Cleanup temp files
            if input_path.exists():
                input_path.unlink()
            if temp_pdf_path and temp_pdf_path.exists():
                temp_pdf_path.unlink()
                temp_pdf_path.parent.rmdir()
            if Path(temp_dir).exists():
                Path(temp_dir).rmdir()

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
