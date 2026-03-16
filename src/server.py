#!/usr/bin/env python3
"""
docs2image HTTP service - Convert PDF or PPTX files to PNG images.

Accepts multipart form-data file uploads.
"""

import logging
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

from font_utils import extract_pptx_fonts, check_missing_fonts
from lo_export import convert_pptx_to_images, convert_pdf_to_images

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("server")

app = FastAPI(title="docs2image", description="PDF/PPTX to PNG converter")

BASE_DIR = Path(__file__).parent.parent.resolve()
OUTPUT_DIR = BASE_DIR / "output"

# Configuration via environment variables
HOST = os.environ.get("DOCS2IMAGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("DOCS2IMAGE_PORT", "8085"))
DPI = int(os.environ.get("DOCS2IMAGE_DPI", "200"))


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
                    "error": f"Unsupported file type: {ext}. Supported: .pdf, .pptx",
                },
            )

        # Read file content
        file_data = await file.read()

        if len(file_data) < 100:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": f"File too small ({len(file_data)} bytes)",
                },
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

        try:
            if ext == ".pptx":
                # Extract and install all embedded fonts
                new_fonts = extract_pptx_fonts(input_path)
                if new_fonts:
                    logger.info(
                        "Installed %d embedded fonts: %s",
                        len(new_fonts),
                        ", ".join(new_fonts),
                    )

                # Check for missing fonts (informational)
                missing = check_missing_fonts(input_path)
                if missing:
                    logger.warning(
                        "Missing fonts (will be substituted): %s",
                        ", ".join(missing),
                    )

                # Convert PPTX directly to images
                image_paths = convert_pptx_to_images(
                    input_path, session_dir, dpi=DPI
                )
            else:
                # PDF: render directly to images
                image_paths = convert_pdf_to_images(
                    input_path, session_dir, dpi=DPI
                )

            return {
                "success": True,
                "session_id": session_id,
                "images": [str(p) for p in image_paths],
                "count": len(image_paths),
            }

        finally:
            # Cleanup temp files
            if input_path.exists():
                input_path.unlink()
            try:
                Path(temp_dir).rmdir()
            except OSError:
                pass

    except Exception as e:
        logger.exception("Conversion failed")
        return JSONResponse(
            status_code=500, content={"success": False, "error": str(e)}
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
