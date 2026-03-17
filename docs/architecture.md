# Architecture

## Conversion Pipeline

```mermaid
flowchart LR
    subgraph Input
        A[PPTX file] --> B[Font Extraction]
        C[PDF file]
    end

    subgraph "Font Processing"
        B --> D[Scan all .rels files]
        D --> E[Extract & deobfuscate fonts]
        E --> F[Install to ~/.local/share/fonts]
        F --> G[fc-cache rebuild]
    end

    subgraph "Text Preprocessing"
        G --> H[Measure text widths<br/>using fontTools]
        H --> I{Overflow<br/>detected?}
        I -->|Yes| J[Shrink font size<br/>Remove autofit]
        I -->|No| K[Keep original]
    end

    subgraph "PPTX → PNG"
        J --> L[LibreOffice headless<br/>-env:UserInstallation]
        K --> L
        L -->|"PDF with embedded fonts"| M[pdftoppm]
    end

    subgraph "PDF → PNG"
        C --> M
    end

    M -->|"per-page PNG"| N[Output directory]
```

## Component Responsibilities

| Module | Purpose |
|--------|---------|
| `server.py` | FastAPI HTTP service with upload, progress tracking, SSE |
| `docs2image.py` | CLI tool with same conversion logic |
| `font_utils.py` | Font extraction from PPTX, installation, missing font detection |
| `lo_export.py` | LibreOffice PDF export with isolated profile, pdftoppm rendering |
| `pptx_preprocess.py` | Text overflow detection and correction using fontTools |
| `run.py` | Service entry point |
| `static/index.html` | Web UI dashboard |

## Key Design Decisions

### LibreOffice Profile Isolation

LibreOffice uses `-env:UserInstallation=file:///tmp/...` to avoid lock conflicts between concurrent conversions. **Critical**: we do NOT override `HOME`, because that breaks fontconfig's access to `~/.local/share/fonts/`.

### Text Overflow Preprocessing

LibreOffice renders text ~13% wider than PowerPoint. Rather than trying to modify LibreOffice's behavior, we pre-process the PPTX:

1. Parse each slide's XML for text shapes
2. Measure text width using actual glyph metrics from installed font files
3. Compare against the visual container width (background rectangles)
4. Shrink only the overflowing text boxes and disable `normAutofit`

### Background Rectangle Detection

PPTX title text often sits in a shape that spans the full slide, but visually appears inside a narrower colored banner. The preprocessor finds filled rectangles on each slide and constrains text width to the banner width, not the text shape width.

## Data Flow (HTTP Request)

```mermaid
sequenceDiagram
    participant Client
    participant Server as FastAPI
    participant Fonts as font_utils
    participant Pre as pptx_preprocess
    participant LO as lo_export
    participant LibreOffice as soffice
    participant Poppler as pdftoppm

    Client->>Server: POST /convert (multipart file)
    Server->>Server: Validate file type & size

    alt PPTX file
        Server->>Fonts: extract_pptx_fonts()
        Fonts-->>Server: new font list

        Server->>Fonts: check_missing_fonts()
        Fonts-->>Server: missing font warnings

        Server->>Pre: preprocess_pptx()
        Pre->>Pre: Measure text, detect overflow
        Pre-->>Server: modified PPTX + modification count

        Server->>LO: convert_pptx_to_images()
        LO->>LibreOffice: soffice -env:UserInstallation=... --convert-to pdf
        LibreOffice-->>LO: PDF file
        LO->>Poppler: pdftoppm -png -r DPI
        Poppler-->>LO: PNG files
    else PDF file
        Server->>LO: convert_pdf_to_images()
        LO->>Poppler: pdftoppm -png -r DPI
        Poppler-->>LO: PNG files
    end

    LO-->>Server: list of image paths
    Server-->>Client: JSON response
```

## File Layout

```
docs2image/
├── run.py                  # HTTP service entry point
├── install.sh              # Automated installer
├── requirements.txt        # Python dependencies
├── docs2image.service      # systemd unit template
├── src/
│   ├── __init__.py
│   ├── server.py           # FastAPI HTTP service
│   ├── docs2image.py       # CLI tool
│   ├── font_utils.py       # Font extraction & management
│   ├── lo_export.py        # LibreOffice export pipeline
│   └── pptx_preprocess.py  # Text overflow preprocessor
├── static/
│   └── index.html          # Web UI dashboard
├── docs/
│   ├── architecture.md     # This file
│   ├── fonts.md            # Font handling guide
│   └── upgrading.md        # Migration guide
├── examples/
│   ├── n8n-integration.md  # n8n workflow setup
│   └── docker-compose.yml  # Traefik/Nginx serving
└── output/                 # Generated images (per session UUID)
```
