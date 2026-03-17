# Changelog

## v2.1.0 — 2026-03-17

### Text Overflow Fix & Web UI

Critical fix for PPTX text rendering where LibreOffice renders text ~13% wider than PowerPoint, causing titles to overflow their visual containers.

#### Added
- `pptx_preprocess.py` — Smart text overflow preprocessor using fontTools glyph metrics
  - Measures actual text width with installed fonts
  - Detects background rectangles to determine visual container width
  - Shrinks only overflowing text and disables `normAutofit` to prevent LO override
  - Estimates font size for shapes with inherited (no explicit) sizes
- Web UI dashboard (`static/index.html`) with:
  - Drag-drop file upload
  - Thumbnail gallery grid per conversion session
  - Full-screen lightbox viewer with keyboard navigation
  - Live conversion log with elapsed timer
  - Missing font warnings
  - Service health indicator
- `/sessions` API endpoint listing all conversion sessions
- `/progress/{session_id}` SSE endpoint for real-time conversion progress
- `missing_fonts` and `new_fonts` fields in `/convert` API response

#### Changed
- LibreOffice isolation switched from `HOME` override to `-env:UserInstallation` flag
  - **Root cause fix**: `HOME=/tmp` broke fontconfig, making all user-installed fonts invisible to LibreOffice
- LO_WIDTH_FACTOR calibrated to 1.15 (empirically measured)
- `fonttools` added to requirements.txt
- Font size estimation for shapes without explicit `sz`: uses 44pt for title-like text (uppercase, wide shapes)
- `fc-list` output parsed with `errors="replace"` to handle non-UTF-8 font names

#### Fixed
- Titles overflowing dark banner backgrounds (e.g., "AMBASADORIŲ PROGRAMA - LT")
- Text boxes with `normAutofit` ignoring preprocessor font size changes
- Shapes with zero width (inherited from group/slide) now resolved via background rectangle detection

---

## v2.0.0 — 2026-03-16

### Font Rendering Overhaul

Major rewrite of the PPTX conversion pipeline to fix font rendering issues where text would overflow boxes, not fit, or use wrong fonts.

#### Added
- `src/font_utils.py` — Comprehensive font extraction that scans ALL `.rels` files in the PPTX archive, not just `presentation.xml.rels`
- `src/lo_export.py` — LibreOffice export with font anti-aliasing, embedding config, and direct `pdftoppm` rendering
- ODTTF deobfuscation with multi-strategy GUID matching (path, normalized path, filename fallback)
- `[Content_Types].xml` parsing to discover fonts declared by MIME type
- Font signature validation after deobfuscation
- Missing font detection and warnings (`--verbose`)
- `docs/architecture.md` with Mermaid diagrams
- `docs/fonts.md` — font handling guide
- `docs/upgrading.md` — migration instructions
- `examples/n8n-integration.md`
- `examples/docker-compose.yml`
- `CHANGELOG.md`

#### Changed
- Project restructured: source code moved to `src/`
- Fonts now install to `~/.local/share/fonts/pptx-extracted/` (user XDG dir) instead of project-local `./fonts/`
- PDF rendering switched from `pdf2image` (Pillow) to `pdftoppm` (Poppler) for higher fidelity
- LibreOffice runs with isolated profile and font rendering configuration
- Install script now checks for Microsoft core fonts and recommends installation

#### Removed
- `pdf2image` dependency
- `Pillow` dependency
- Project-local `fonts/` directory usage

#### Fixed
- Fonts referenced from slide-level, master, layout, and theme `.rels` files are now extracted (previously only presentation-level was checked)
- ODTTF fonts with non-matching paths are now matched by filename fallback
- LibreOffice font substitution reduced by proper font installation path

---

## v1.0.0 — Initial Release

- PDF to PNG conversion via pdf2image
- PPTX to PNG via LibreOffice → PDF → pdf2image
- Basic embedded font extraction from `presentation.xml.rels`
- FastAPI HTTP service with `/convert` endpoint
- CLI tool
- systemd service + Apache config generation
- Docker bridge auto-detection
