#!/usr/bin/env python3
"""Entry point for the docs2image HTTP service."""

import sys
from pathlib import Path

# Ensure src/ is on the import path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from server import app, HOST, PORT  # noqa: E402

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
