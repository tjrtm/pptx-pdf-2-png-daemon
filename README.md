# docs2image

HTTP service and CLI tool that converts **PDF** and **PPTX** files to high-fidelity **PNG** images. Features smart text overflow correction and comprehensive font handling for accurate rendering of PowerPoint presentations on Linux.

## Key Features

- Converts PDF and PPTX files to per-page PNG images
- **Smart text overflow preprocessor** — detects and corrects text that would overflow in LibreOffice using actual font metrics
- **Comprehensive font extraction** from PPTX — scans all relationship files, handles obfuscated `.odttf` fonts
- **High-fidelity rendering** via LibreOffice + pdftoppm with font anti-aliasing
- **Web UI** with drag-drop upload, image gallery, lightbox viewer, and live conversion log
- REST API with multipart form-data uploads
- CLI tool for local/batch use
- Missing font detection and warnings
- systemd service for production deployment

## Quick Start

```bash
# 1. Clone
git clone https://github.com/tjrtm/pptx-pdf-2-png-daemon.git
cd pptx-pdf-2-png-daemon

# 2. Install system dependencies
sudo apt install python3 python3-venv poppler-utils libreoffice-impress

# 3. Install Microsoft 365 fonts (REQUIRED for accurate PPTX rendering)
git clone --depth 1 https://github.com/pjobson/Microsoft-365-Fonts.git /tmp/ms-fonts
mkdir -p ~/.local/share/fonts/ms365
find /tmp/ms-fonts -name "*.ttf" -exec cp {} ~/.local/share/fonts/ms365/ \;
fc-cache -f
rm -rf /tmp/ms-fonts

# 4. Run the installer
./install.sh

# 5. Start the service
sudo cp docs2image.service.generated /etc/systemd/system/docs2image.service
sudo systemctl daemon-reload
sudo systemctl enable --now docs2image

# 6. Open the web UI
# http://localhost:8085
```

## System Requirements

- Python 3.10+
- poppler-utils (provides `pdftoppm`)
- libreoffice-impress (PPTX → PDF conversion)
- **Microsoft 365 fonts** — see [docs/fonts.md](docs/fonts.md)
- Apache2 or Nginx (optional, for serving output images on a separate port)

## How It Works

```mermaid
flowchart LR
    A[Upload PPTX] --> B[Extract embedded fonts]
    B --> C[Check text overflow<br/>using font metrics]
    C --> D[Fix overflowing text boxes]
    D --> E[LibreOffice → PDF]
    E --> F[pdftoppm → PNG]
    F --> G[Per-page PNGs]
```

For the full architecture, see [docs/architecture.md](docs/architecture.md).

## Project Structure

```
docs2image/
├── run.py                  # HTTP service entry point
├── install.sh              # Automated installer
├── requirements.txt        # Python dependencies
├── docs2image.service      # systemd unit template
├── src/
│   ├── server.py           # FastAPI HTTP service + web UI
│   ├── docs2image.py       # CLI tool
│   ├── font_utils.py       # Font extraction & management
│   ├── lo_export.py        # LibreOffice export pipeline
│   └── pptx_preprocess.py  # Text overflow preprocessor
├── static/
│   └── index.html          # Web UI dashboard
├── docs/                   # Documentation
│   ├── architecture.md     # System design & data flow diagrams
│   ├── fonts.md            # Font installation & troubleshooting
│   └── upgrading.md        # Migration guide
├── examples/               # Integration examples
│   ├── n8n-integration.md
│   └── docker-compose.yml
└── output/                 # Generated images (per session UUID)
```

## API Reference

### POST /convert

Convert a PDF or PPTX file to PNG images.

```bash
curl -X POST http://localhost:8085/convert -F "file=@presentation.pptx"
```

**Response:**

```json
{
  "success": true,
  "session_id": "a1b2c3d4-5678-...",
  "filename": "presentation.pptx",
  "images": ["/output/a1b2c3d4-.../page_001.png", "..."],
  "count": 17,
  "missing_fonts": [],
  "new_fonts": []
}
```

### GET /health

```bash
curl http://localhost:8085/health
```

### GET /sessions

List all conversion sessions.

### GET /

Web UI dashboard with upload, gallery, and conversion log.

## CLI Usage

```bash
source venv/bin/activate

# Convert PPTX
PYTHONPATH=src python src/docs2image.py presentation.pptx

# Convert PDF at 300 DPI
PYTHONPATH=src python src/docs2image.py document.pdf --dpi 300

# Verbose output (shows font details)
PYTHONPATH=src python src/docs2image.py presentation.pptx --verbose
```

## Configuration

| Environment Variable   | Default     | Description                                   |
| ---------------------- | ----------- | --------------------------------------------- |
| `DOCS2IMAGE_HOST`      | `127.0.0.1` | API bind address (auto-detected for Docker)  |
| `DOCS2IMAGE_PORT`      | `8085`      | API port                                      |
| `DOCS2IMAGE_DPI`       | `200`       | Output image resolution                       |

## Updating an Existing Installation

See [docs/upgrading.md](docs/upgrading.md) for step-by-step instructions.

**Quick update:**

```bash
sudo systemctl stop docs2image
cd /path/to/docs2image
git pull origin main
rm -rf venv && python3 -m venv venv && venv/bin/pip install -r requirements.txt
sudo cp docs2image.service.generated /etc/systemd/system/docs2image.service
sudo systemctl daemon-reload && sudo systemctl start docs2image
```

**⚠️ Don't forget fonts!** See [docs/fonts.md](docs/fonts.md). Without Microsoft 365 fonts, text will render incorrectly.

## Troubleshooting

### Text overflows or looks wrong

Install Microsoft 365 fonts — this fixes 95% of rendering issues:

```bash
git clone --depth 1 https://github.com/pjobson/Microsoft-365-Fonts.git /tmp/ms-fonts
mkdir -p ~/.local/share/fonts/ms365
find /tmp/ms-fonts -name "*.ttf" -exec cp {} ~/.local/share/fonts/ms365/ \;
fc-cache -f
```

Check for missing fonts:

```bash
PYTHONPATH=src python src/docs2image.py presentation.pptx --verbose
# Look for "Missing fonts:" in output
```

### Service won't start

```bash
journalctl -u docs2image -n 50
```

### Cannot access from Docker

```bash
ss -tlnp | grep 8085
# Should show 0.0.0.0:8085 or 172.17.0.1:8085
```

## License

MIT
