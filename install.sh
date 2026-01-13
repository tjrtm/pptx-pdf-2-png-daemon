#!/bin/bash
#
# docs2image installation script
# Installs the PDF/PPTX to PNG converter service
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${SUDO_USER:-$USER}"
SERVICE_NAME="docs2image"
API_PORT="${DOCS2IMAGE_PORT:-8085}"
WEB_PORT="${DOCS2IMAGE_WEB_PORT:-8080}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================"
echo "  docs2image installer"
echo "========================================"
echo ""
echo "Install directory: $SCRIPT_DIR"
echo "Service user: $SERVICE_USER"
echo "API port: $API_PORT"
echo "Web port: $WEB_PORT"
echo ""

# Detect Docker and get bridge IP
DOCKER_BRIDGE_IP=""
if command -v docker &> /dev/null && docker ps &> /dev/null 2>&1; then
    DOCKER_BRIDGE_IP=$(ip addr show docker0 2>/dev/null | grep -oP 'inet \K[\d.]+' || echo "")
    if [ -n "$DOCKER_BRIDGE_IP" ]; then
        echo -e "${GREEN}Docker detected${NC} - bridge IP: $DOCKER_BRIDGE_IP"
        echo "Service will bind to $DOCKER_BRIDGE_IP for Docker container access"
        API_HOST="$DOCKER_BRIDGE_IP"
    fi
else
    echo "Docker not detected - service will bind to 127.0.0.1"
    API_HOST="127.0.0.1"
fi
echo ""

# Check for required system dependencies
echo "Checking system dependencies..."

MISSING_DEPS=()

if ! command -v python3 &> /dev/null; then
    MISSING_DEPS+=("python3 python3-venv")
fi

if ! dpkg -s poppler-utils &> /dev/null 2>&1; then
    MISSING_DEPS+=("poppler-utils")
fi

if ! dpkg -s libreoffice-impress &> /dev/null 2>&1; then
    echo -e "${YELLOW}WARNING:${NC} libreoffice-impress not installed (required for PPTX support)"
    MISSING_DEPS+=("libreoffice-impress")
fi

if ! command -v apache2 &> /dev/null; then
    MISSING_DEPS+=("apache2")
fi

if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
    echo ""
    echo -e "${YELLOW}Missing dependencies:${NC}"
    echo "  sudo apt install ${MISSING_DEPS[*]}"
    echo ""
    read -p "Install missing dependencies now? [Y/n] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
        sudo apt update
        sudo apt install -y ${MISSING_DEPS[*]}
    else
        echo -e "${RED}Please install dependencies manually and re-run this script${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}All dependencies installed${NC}"
echo ""

# Create virtual environment
echo "Creating Python virtual environment..."
python3 -m venv "$SCRIPT_DIR/venv"

# Install Python dependencies
echo "Installing Python dependencies..."
"$SCRIPT_DIR/venv/bin/pip" install --upgrade pip -q
"$SCRIPT_DIR/venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" -q

echo -e "${GREEN}Python dependencies installed${NC}"
echo ""

# Create output directory
echo "Creating output directory..."
mkdir -p "$SCRIPT_DIR/output"

# Set permissions for web server access
echo "Setting permissions..."
chmod o+x "$(dirname "$SCRIPT_DIR")" 2>/dev/null || true
chmod o+x "$SCRIPT_DIR"
chmod -R o+rX "$SCRIPT_DIR/output"

# Generate systemd service file
echo "Generating systemd service file..."
cat > "$SCRIPT_DIR/docs2image.service" << EOF
[Unit]
Description=docs2image - PDF/PPTX to PNG converter service
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$SCRIPT_DIR
Environment="DOCS2IMAGE_HOST=$API_HOST"
Environment="DOCS2IMAGE_PORT=$API_PORT"
ExecStart=$SCRIPT_DIR/venv/bin/uvicorn server:app --host $API_HOST --port $API_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Generate Apache config
echo "Generating Apache configuration..."
cat > "$SCRIPT_DIR/apache-docs2image.conf" << EOF
<VirtualHost *:$WEB_PORT>
    ServerName localhost
    DocumentRoot $SCRIPT_DIR/output

    <Directory $SCRIPT_DIR/output>
        Options Indexes FollowSymLinks
        AllowOverride None
        Require all granted
    </Directory>

    <IfModule mod_autoindex.c>
        IndexOptions FancyIndexing HTMLTable VersionSort
    </IfModule>

    ErrorLog \${APACHE_LOG_DIR}/docs2image_error.log
    CustomLog \${APACHE_LOG_DIR}/docs2image_access.log combined
</VirtualHost>
EOF

echo ""
echo -e "${GREEN}========================================"
echo "  Installation complete!"
echo "========================================${NC}"
echo ""
echo "Next steps:"
echo ""
echo "1. Install the systemd service:"
echo "   sudo cp $SCRIPT_DIR/docs2image.service /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable docs2image"
echo "   sudo systemctl start docs2image"
echo ""
echo "2. Configure Apache to serve output images:"
echo "   # Set Apache to listen on port $WEB_PORT (edit /etc/apache2/ports.conf):"
echo "   echo 'Listen $WEB_PORT' | sudo tee -a /etc/apache2/ports.conf"
echo ""
echo "   # Install the site:"
echo "   sudo cp $SCRIPT_DIR/apache-docs2image.conf /etc/apache2/sites-available/"
echo "   sudo a2ensite apache-docs2image.conf"
echo "   sudo systemctl reload apache2"
echo ""
echo "3. Verify installation:"
echo "   curl http://$API_HOST:$API_PORT/health"
echo "   curl http://localhost:$WEB_PORT/"
echo ""
echo "API endpoint: http://$API_HOST:$API_PORT/convert"
echo "Web files:    http://localhost:$WEB_PORT/{session_id}/page_001.png"
echo ""
