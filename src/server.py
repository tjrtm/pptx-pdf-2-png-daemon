#!/usr/bin/env python3
"""
docs2image HTTP service - Convert PDF or PPTX files to PNG images.

Accepts multipart form-data file uploads.
Serves a web UI for testing and browsing converted images.
Provides real-time conversion progress via SSE.
"""

import asyncio
import logging
import os
import queue
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

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
STATIC_DIR = BASE_DIR / "static"

# Configuration via environment variables
HOST = os.environ.get("DOCS2IMAGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("DOCS2IMAGE_PORT", "8085"))
DPI = int(os.environ.get("DOCS2IMAGE_DPI", "200"))

# Progress tracking for active conversions
# session_id -> queue of log messages
_progress: dict[str, queue.Queue] = {}

# Mount output directory for serving images
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")

# Mount static assets
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _emit(session_id: str, step: str, detail: str = ""):
    """Send a progress event to the UI."""
    q = _progress.get(session_id)
    if q:
        q.put({"step": step, "detail": detail, "ts": time.time()})


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


@app.get("/progress/{session_id}")
async def progress_stream(session_id: str):
    """SSE endpoint for real-time conversion progress."""
    q = _progress.get(session_id)
    if not q:
        return JSONResponse(status_code=404, content={"error": "Session not found"})

    async def event_generator():
        import json
        while True:
            try:
                msg = q.get_nowait()
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("step") in ("done", "error"):
                    break
            except queue.Empty:
                # Send keepalive
                yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/convert")
async def convert_document(file: UploadFile = File(...)):
    """Convert PDF or PPTX file to PNG images."""
    try:
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

        file_data = await file.read()
        file_size_mb = len(file_data) / (1024 * 1024)

        if len(file_data) < 100:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": f"File too small ({len(file_data)} bytes)",
                },
            )

        session_id = str(uuid.uuid4())
        session_dir = OUTPUT_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # Set up progress tracking
        _progress[session_id] = queue.Queue()

        temp_dir = tempfile.mkdtemp()
        safe_filename = "input" + ext
        input_path = Path(temp_dir) / safe_filename
        input_path.write_bytes(file_data)

        _emit(session_id, "upload", f"Received {filename} ({file_size_mb:.1f} MB)")

        try:
            missing = []
            new_fonts_list = []

            if ext == ".pptx":
                _emit(session_id, "fonts", "Extracting embedded fonts…")
                new_fonts = extract_pptx_fonts(input_path)
                if new_fonts:
                    new_fonts_list = new_fonts
                    _emit(session_id, "fonts", f"Installed {len(new_fonts)} embedded fonts")
                    logger.info("Installed %d embedded fonts: %s", len(new_fonts), ", ".join(new_fonts))

                _emit(session_id, "fonts", "Checking font availability…")
                missing = check_missing_fonts(input_path)
                if missing:
                    _emit(session_id, "fonts_warning", f"{len(missing)} fonts not found: {', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}")
                    logger.warning("Missing fonts: %s", ", ".join(missing))
                else:
                    _emit(session_id, "fonts", "All fonts available ✓")

                _emit(session_id, "converting", "Converting PPTX → PDF via LibreOffice…")
                image_paths = convert_pptx_to_images(input_path, session_dir, dpi=DPI)
                _emit(session_id, "rendering", f"Rendered {len(image_paths)} slides to PNG")

            else:
                _emit(session_id, "converting", "Rendering PDF pages to PNG…")
                image_paths = convert_pdf_to_images(input_path, session_dir, dpi=DPI)
                _emit(session_id, "rendering", f"Rendered {len(image_paths)} pages to PNG")

            result = {
                "success": True,
                "session_id": session_id,
                "filename": filename,
                "images": [f"/output/{session_id}/{p.name}" for p in image_paths],
                "count": len(image_paths),
                "missing_fonts": missing,
                "new_fonts": new_fonts_list,
            }

            _emit(session_id, "done", f"Complete: {len(image_paths)} images")
            return result

        finally:
            if input_path.exists():
                input_path.unlink()
            try:
                Path(temp_dir).rmdir()
            except OSError:
                pass
            # Clean up progress queue after a delay
            def _cleanup():
                import time
                time.sleep(30)
                _progress.pop(session_id, None)
            threading.Thread(target=_cleanup, daemon=True).start()

    except Exception as e:
        logger.exception("Conversion failed")
        if session_id in _progress:
            _emit(session_id, "error", str(e))
        return JSONResponse(
            status_code=500, content={"success": False, "error": str(e)}
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
