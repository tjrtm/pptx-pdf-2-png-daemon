# docs2image

HTTP service that converts PDF and PPTX files to PNG images. Designed to run as a systemd service with Apache serving the output images. Ideal for integration with n8n or other automation tools.

## Features

- Convert PDF files to PNG images
- Convert PPTX (PowerPoint) files to PNG images
- REST API with multipart form-data file uploads
- Apache web server for serving generated images
- Automatic Docker bridge detection for container access
- Systemd service for production deployment

## Quick Start

```bash
# 1. Clone or copy files to your server
cd /path/to/docs2image

# 2. Run the installer (handles dependencies, venv, configs)
./install.sh

# 3. Install and start the systemd service
sudo cp docs2image.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable docs2image
sudo systemctl start docs2image

# 4. Configure Apache for serving images
echo 'Listen 8080' | sudo tee -a /etc/apache2/ports.conf
sudo cp apache-docs2image.conf /etc/apache2/sites-available/
sudo a2ensite apache-docs2image.conf
sudo systemctl reload apache2

# 5. Verify
curl http://localhost:8085/health
curl http://localhost:8080/
```

## System Requirements

- Python 3.10+
- Apache2 (for serving images)
- poppler-utils (for PDF conversion)
- libreoffice-impress (for PPTX conversion)

### Install system dependencies

```bash
sudo apt update
sudo apt install python3 python3-venv apache2 poppler-utils libreoffice-impress
```

## Installation

### Automated (recommended)

```bash
./install.sh
```

The installer will:
- Check and optionally install missing system dependencies
- Detect Docker and configure appropriate API host binding
- Create Python virtual environment and install dependencies
- Generate systemd service file with correct paths
- Generate Apache configuration file
- Set up proper file permissions

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCS2IMAGE_HOST` | `127.0.0.1` | API bind address (auto-detected for Docker) |
| `DOCS2IMAGE_PORT` | `8085` | API port |
| `DOCS2IMAGE_WEB_PORT` | `8080` | Apache web server port |
| `DOCS2IMAGE_DPI` | `200` | Output image resolution |

## Service Management

```bash
# Start/stop/restart
sudo systemctl start docs2image
sudo systemctl stop docs2image
sudo systemctl restart docs2image

# Check status
sudo systemctl status docs2image

# View logs
journalctl -u docs2image -f
```

## API Reference

### POST /convert

Convert a PDF or PPTX file to PNG images.

**Request:** Multipart form-data with `file` field

```bash
curl -X POST http://localhost:8085/convert -F "file=@document.pdf"
```

**Response:**

```json
{
  "success": true,
  "session_id": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
  "images": [
    "/path/to/docs2image/output/a1b2c3d4-.../page_001.png",
    "/path/to/docs2image/output/a1b2c3d4-.../page_002.png"
  ],
  "count": 2
}
```

**Error Response:**

```json
{
  "success": false,
  "error": "Error message"
}
```

### GET /health

Health check endpoint.

```bash
curl http://localhost:8085/health
```

**Response:**

```json
{"status": "ok"}
```

## Accessing Generated Images

Images are served via Apache on port 8080 (configurable).

**URL Pattern:**
```
http://<server-ip>:8080/{session_id}/page_001.png
```

**Convert file path to URL:**
```
File:  /path/to/docs2image/output/abc123/page_001.png
URL:   http://<server-ip>:8080/abc123/page_001.png
```

## n8n Integration

### HTTP Request Node Configuration

| Setting | Value |
|---------|-------|
| Method | POST |
| URL | `http://172.17.0.1:8085/convert` (Docker) or `http://localhost:8085/convert` |
| Body Content Type | Form-Data |
| Body Parameters | Name: `file`, Type: n-8 Binary |

### Converting File Paths to URLs

Use a Code node after the HTTP Request to convert paths to URLs:

```javascript
const serverUrl = "http://your-server-ip:8080";
const items = $input.all();

for (const item of items) {
  if (item.json.images) {
    item.json.image_urls = item.json.images.map(path => {
      const parts = path.split('/output/');
      return serverUrl + '/' + parts[1];
    });
  }
}

return items;
```

## Docker Integration

If Docker is detected during installation, the service automatically binds to the Docker bridge IP (typically `172.17.0.1`) so containers can access it.

**From Docker containers, use:**
```
http://172.17.0.1:8085/convert
```

**From host machine, use:**
```
http://localhost:8085/convert
```

### Serving Output via Docker Nginx (with Traefik)

If you have an existing docker-compose setup with Traefik as reverse proxy, you can expose the output directory by adding this service to your `docker-compose.yml`:

```yaml
services:
  # ... your existing services ...

  docs2image-output:
    image: nginx:alpine
    restart: always
    volumes:
      - /path/to/docs2image/output:/usr/share/nginx/html:ro
    command: >
      sh -c "echo 'server { listen 80; location / { root /usr/share/nginx/html; autoindex on; autoindex_exact_size off; autoindex_localtime on; } }' > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"
    labels:
      - traefik.enable=true
      - traefik.http.routers.docs2image.rule=Host(`${YOUR_DOMAIN}`) && PathPrefix(`/output`)
      - traefik.http.routers.docs2image.entrypoints=websecure
      - traefik.http.routers.docs2image.tls=true
      - traefik.http.routers.docs2image.tls.certresolver=mytlschallenge
      - traefik.http.middlewares.docs2image-strip.stripprefix.prefixes=/output
      - traefik.http.routers.docs2image.middlewares=docs2image-strip
      - traefik.http.services.docs2image.loadbalancer.server.port=80
```

**Configuration notes:**
- Replace `/path/to/docs2image/output` with the actual path to your docs2image output directory
- Replace `${YOUR_DOMAIN}` with your domain or use an environment variable
- The `autoindex on` directive enables directory listing
- The `stripprefix` middleware removes `/output` from the URL path before passing to nginx

**Deploy:**
```bash
docker compose up -d docs2image-output
```

**Access:**
```
https://your-domain.com/output/
https://your-domain.com/output/{session_id}/page_001.png
```

## Output Structure

```
output/
  {session_id}/
    page_001.png
    page_002.png
    page_003.png
    ...
```

Each conversion creates a new session with a unique UUID.

## Files

| File | Description |
|------|-------------|
| `server.py` | FastAPI HTTP service |
| `docs2image.py` | CLI tool (standalone) |
| `install.sh` | Installation script |
| `docs2image.service` | Systemd unit file (generated) |
| `apache-docs2image.conf` | Apache config (generated) |
| `requirements.txt` | Python dependencies |

## Troubleshooting

### Service fails to start

```bash
journalctl -u docs2image -n 50
```

### LibreOffice PPTX conversion fails

Ensure libreoffice-impress is installed:
```bash
sudo apt install libreoffice-impress
```

### Permission denied on output directory

```bash
chmod o+x /path/to/docs2image
chmod -R o+rX /path/to/docs2image/output
```

### Cannot access from Docker container

Verify the service is bound to the Docker bridge IP:
```bash
ss -tlnp | grep 8085
```

Should show `172.17.0.1:8085` not `127.0.0.1:8085`.

### Port already in use

```bash
sudo lsof -i :8085
sudo lsof -i :8080
```

## CLI Tool

A standalone CLI tool is also included:

```bash
source venv/bin/activate

# Convert PDF
python docs2image.py document.pdf

# Convert PPTX
python docs2image.py presentation.pptx

# Custom DPI
python docs2image.py document.pdf --dpi 300
```

## License

MIT
