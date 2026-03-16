# docs2image

HTTP service and CLI tool that converts **PDF** and **PPTX** files to high-fidelity **PNG** images. Designed for headless server deployment with systemd, Apache/Nginx, and integration with automation tools like n8n.

## Key Features

- Converts PDF and PPTX files to per-page PNG images
- **Comprehensive font extraction** from PPTX — scans all relationship files, handles obfuscated `.odttf` fonts
- **High-fidelity rendering** via LibreOffice + pdftoppm with font anti-aliasing
- REST API with multipart form-data uploads
- CLI tool for local/batch use
- Automatic Docker bridge detection for container access
- systemd service + web server for production

## Quick Start

```bash
# 1. Clone
git clone https://github.com/tjrtm/pptx-pdf-2-png-daemon.git
cd pptx-pdf-2-png-daemon

# 2. Install system dependencies
sudo apt install python3 python3-venv poppler-utils libreoffice-impress
sudo apt install ttf-mscorefonts-installer fonts-liberation  # recommended

# 3. Run the installer
./install.sh

# 4. Start the service
sudo cp docs2image.service.generated /etc/systemd/system/docs2image.service
sudo systemctl daemon-reload
sudo systemctl enable --now docs2image

# 5. Test
curl http://localhost:8085/health
curl -X POST http://localhost:8085/convert -F "file=@presentation.pptx"
```

## System Requirements

- Python 3.10+
- poppler-utils (provides `pdftoppm`)
- libreoffice-impress (PPTX → PDF conversion)
- Apache2 or Nginx (optional, for serving output images)

### Recommended Fonts

For accurate PPTX rendering, install Microsoft core fonts:

```bash
sudo apt install ttf-mscorefonts-installer fonts-liberation fonts-noto
```

See [docs/fonts.md](docs/fonts.md) for details on font handling.

## Project Structure

```
docs2image/
├── run.py                  # HTTP service entry point
├── install.sh              # Automated installer
├── requirements.txt        # Python dependencies (FastAPI, uvicorn)
├── docs2image.service      # systemd unit template
├── src/
│   ├── server.py           # FastAPI HTTP service
│   ├── docs2image.py       # CLI tool
│   ├── font_utils.py       # Font extraction & management
│   └── lo_export.py        # LibreOffice export pipeline
├── docs/                   # Documentation
│   ├── architecture.md     # System design & data flow diagrams
│   ├── fonts.md            # Font handling & troubleshooting
│   └── upgrading.md        # Migration guide from v1.x
├── examples/               # Integration examples
│   ├── n8n-integration.md  # n8n workflow setup
│   └── docker-compose.yml  # Traefik/Nginx serving config
└── output/                 # Generated images (per session UUID)
```

## API Reference

### POST /convert

Convert a PDF or PPTX file to PNG images.

```bash
curl -X POST http://localhost:8085/convert -F "file=@document.pptx"
```

**Response:**

```json
{
  "success": true,
  "session_id": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
  "images": [
    "/path/to/output/a1b2c3d4-.../page_001.png",
    "/path/to/output/a1b2c3d4-.../page_002.png"
  ],
  "count": 2
}
```

### GET /health

```bash
curl http://localhost:8085/health
# {"status": "ok"}
```

## CLI Usage

```bash
# Activate the virtual environment
source venv/bin/activate

# Convert PPTX
python src/docs2image.py presentation.pptx

# Convert PDF at 300 DPI
python src/docs2image.py document.pdf --dpi 300

# Verbose output (shows font extraction details)
python src/docs2image.py presentation.pptx --verbose
```

## Configuration

| Environment Variable   | Default     | Description                                        |
| ---------------------- | ----------- | -------------------------------------------------- |
| `DOCS2IMAGE_HOST`      | `127.0.0.1` | API bind address (auto-detected for Docker)       |
| `DOCS2IMAGE_PORT`      | `8085`      | API port                                           |
| `DOCS2IMAGE_WEB_PORT`  | `8080`      | Apache/Nginx web server port (installer only)      |
| `DOCS2IMAGE_DPI`       | `200`       | Output image resolution                            |

## How It Works

```mermaid
flowchart LR
    A[Upload PPTX] --> B[Extract embedded fonts]
    B --> C[Install fonts + fc-cache]
    C --> D[LibreOffice → PDF<br/>with font embedding]
    D --> E[pdftoppm → PNG<br/>with anti-aliasing]
    E --> F[Per-page PNGs]
```

For the full architecture with sequence diagrams, see [docs/architecture.md](docs/architecture.md).

## Serving Output Images

### Apache

```bash
echo 'Listen 8080' | sudo tee -a /etc/apache2/ports.conf
sudo cp apache-docs2image.conf /etc/apache2/sites-available/
sudo a2ensite apache-docs2image.conf
sudo systemctl reload apache2
```

### Docker + Traefik

See [examples/docker-compose.yml](examples/docker-compose.yml).

### URL Pattern

```
http://<server>:8080/{session_id}/page_001.png
```

## Service Management

```bash
sudo systemctl start docs2image
sudo systemctl stop docs2image
sudo systemctl restart docs2image
sudo systemctl status docs2image
journalctl -u docs2image -f
```

## Upgrading

See [docs/upgrading.md](docs/upgrading.md) for migration instructions from previous versions.

## Troubleshooting

### Fonts look wrong in PPTX output

Install Microsoft core fonts and check for missing font warnings:

```bash
sudo apt install ttf-mscorefonts-installer fonts-liberation
python src/docs2image.py presentation.pptx --verbose
```

### LibreOffice conversion fails

```bash
# Check LibreOffice is installed
soffice --version

# Check for lock files
rm -f /tmp/.~lock.*
```

### Service won't start

```bash
journalctl -u docs2image -n 50
```

### Cannot access from Docker

Verify the service binds to the Docker bridge IP:

```bash
ss -tlnp | grep 8085
# Should show 172.17.0.1:8085
```

## License

MIT
