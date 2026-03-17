#!/usr/bin/env python3
"""
docs2image HTTP service - Convert PDF or PPTX files to PNG images.

Accepts multipart form-data file uploads.
Serves a web UI for testing and browsing converted images.
"""

import logging
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from font_utils import extract_pptx_fonts, check_missing_fonts
from lo_export import convert_pptx_to_images, convert_pdf_to_images
from pptx_preprocess import preprocess_pptx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("server")

app = FastAPI(title="docs2image", description="PDF/PPTX to PNG converter")

BASE_DIR = Path(__file__).parent.parent.resolve()
OUTPUT_DIR = BASE_DIR / "output"
STATIC_DIR = BASE_DIR / "static"

# Configuration via environment variables
HOST = os.environ.get("DOCS2IMAGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("DOCS2IMAGE_PORT", "8085"))
DPI = int(os.environ.get("DOCS2IMAGE_DPI", "200"))

# Mount output directory for serving images
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")

# Mount static assets
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Serve the main dashboard page."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text())
    return HTMLResponse(content="<h1>docs2image</h1><p>Static files not found.</p>")


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/sessions")
def list_sessions():
    """List all conversion sessions with their images."""
    sessions = []
    if OUTPUT_DIR.exists():
        for session_dir in sorted(OUTPUT_DIR.iterdir(), reverse=True):
            if session_dir.is_dir():
                images = sorted(session_dir.glob("page_*.png"))
                if images:
                    sessions.append({
                        "session_id": session_dir.name,
                        "images": [
                            f"/output/{session_dir.name}/{img.name}"
                            for img in images
                        ],
                        "count": len(images),
                    })
    return {"sessions": sessions}


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

                # Pre-process PPTX to fix text overflow
                processed_path, mods = preprocess_pptx(input_path)
                if mods > 0:
                    logger.info("Pre-processed PPTX: %d text boxes adjusted", mods)
                    convert_source = processed_path
                else:
                    convert_source = input_path

                # Convert PPTX directly to images
                image_paths = convert_pptx_to_images(
                    convert_source, session_dir, dpi=DPI
                )
            else:
                # PDF: render directly to images
                image_paths = convert_pdf_to_images(
                    input_path, session_dir, dpi=DPI
                )

            return {
                "success": True,
                "session_id": session_id,
                "filename": filename,
                "images": [
                    f"/output/{session_id}/{p.name}" for p in image_paths
                ],
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
