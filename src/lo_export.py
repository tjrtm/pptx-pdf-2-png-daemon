#!/usr/bin/env python3
"""
lo_export.py - LibreOffice export utilities for PPTX/PDF to PNG conversion.

Provides direct image export (bypassing PDF intermediate) and
configures LibreOffice for optimal font rendering.
"""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("lo_export")

# Default DPI for output images
DEFAULT_DPI = 200


def _configure_libreoffice_fonts(home_dir: str) -> None:
    """Configure LibreOffice user profile for better font rendering.

    Creates a registrymodifications.xcu that enables:
    - Anti-aliased font rendering
    - Font embedding in exports
    - Better font substitution behavior
    """
    lo_profile = Path(home_dir) / ".config" / "libreoffice" / "4" / "user"
    lo_profile.mkdir(parents=True, exist_ok=True)

    xcu_path = lo_profile / "registrymodifications.xcu"

    # Only write if it doesn't exist or is our managed version
    xcu_content = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry"
           xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <item oor:path="/org.openoffice.Office.Common/Font/Substitution">
    <prop oor:name="FontHeight">
      <value>true</value>
    </prop>
  </item>
  <item oor:path="/org.openoffice.Office.Common/View/FontAntiAliasing">
    <prop oor:name="Enabled">
      <value>true</value>
    </prop>
    <prop oor:name="MinPixelHeight">
      <value>1</value>
    </prop>
  </item>
  <item oor:path="/org.openoffice.Office.Common/Filter/PDF/Export">
    <prop oor:name="EmbedStandardFonts">
      <value>true</value>
    </prop>
  </item>
</oor:items>
"""
    xcu_path.write_text(xcu_content)
    logger.debug("Wrote LibreOffice config to %s", xcu_path)


def _get_libreoffice_env() -> tuple[dict[str, str], list[str]]:
    """Get environment and extra CLI args for LibreOffice subprocess.

    Uses LibreOffice's -env:UserInstallation flag to isolate the profile
    WITHOUT overriding HOME. This keeps fontconfig working with the real
    user font directories (~/.local/share/fonts/).

    Returns:
        Tuple of (environment dict, extra CLI arguments for soffice)
    """
    env = {**os.environ}

    # Create isolated LO user profile via -env:UserInstallation
    lo_profile = tempfile.mkdtemp(prefix="docs2image_lo_")
    _configure_libreoffice_fonts(lo_profile)

    # Use file:// URL for UserInstallation
    profile_url = "file://" + lo_profile

    extra_args = [f"-env:UserInstallation={profile_url}"]

    return env, extra_args


def convert_pptx_to_images(
    pptx_path: Path,
    output_dir: Path,
    dpi: int = DEFAULT_DPI,
) -> list[Path]:
    """Convert PPTX directly to PNG images using LibreOffice.

    Strategy: Export to PDF with maximal font embedding, then render
    each page to PNG at the requested DPI using pdftoppm (poppler).

    This is more reliable than UNO-based image export and gives
    consistent high-quality results.

    Args:
        pptx_path: Path to the PPTX file
        output_dir: Directory to save PNG images
        dpi: Output resolution

    Returns:
        List of paths to generated PNG images
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Convert PPTX to PDF via LibreOffice with embedded fonts
    pdf_path = _pptx_to_pdf(pptx_path)

    try:
        # Step 2: Render PDF pages to PNG via pdftoppm
        return _pdf_to_images(pdf_path, output_dir, dpi)
    finally:
        # Clean up temp PDF
        if pdf_path.exists():
            pdf_path.unlink()
            try:
                pdf_path.parent.rmdir()
            except OSError:
                pass


def _pptx_to_pdf(pptx_path: Path) -> Path:
    """Convert PPTX to PDF using LibreOffice with optimal settings."""
    if not pptx_path.exists():
        raise RuntimeError(f"Input file does not exist: {pptx_path}")

    file_size = pptx_path.stat().st_size
    if file_size == 0:
        raise RuntimeError(f"Input file is empty: {pptx_path}")

    temp_dir = tempfile.mkdtemp(prefix="docs2image_pdf_")
    env, extra_args = _get_libreoffice_env()

    # Use Impress-specific PDF export with font embedding
    filter_options = (
        "pdf:impress_pdf_Export:"
        '{"EmbedStandardFonts":{"type":"boolean","value":"true"},'
        '"UseTaggedPDF":{"type":"boolean","value":"true"},'
        '"EmbedCompleteFont":{"type":"boolean","value":"true"}}'
    )

    cmd = [
        "soffice",
        "--headless",
        "--nofirststartwizard",
        "--norestore",
        *extra_args,
        "--convert-to", filter_options,
        "--outdir", temp_dir,
        str(pptx_path),
    ]

    logger.info("Converting PPTX to PDF: %s", pptx_path.name)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )

    if result.returncode != 0:
        # Fall back to simple conversion if filter string isn't supported
        logger.warning(
            "LibreOffice filter export failed, falling back to simple PDF: %s",
            result.stderr,
        )
        cmd_simple = [
            "soffice",
            "--headless",
            "--nofirststartwizard",
            "--norestore",
            *extra_args,
            "--convert-to", "pdf",
            "--outdir", temp_dir,
            str(pptx_path),
        ]
        result = subprocess.run(
            cmd_simple,
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice conversion failed: {result.stderr}")

    pdf_name = pptx_path.stem + ".pdf"
    pdf_path = Path(temp_dir) / pdf_name

    if not pdf_path.exists():
        raise RuntimeError(
            f"PDF not created. Input: {pptx_path} ({file_size} bytes). "
            f"stdout: {result.stdout}  stderr: {result.stderr}"
        )

    logger.info("PDF created: %s (%d bytes)", pdf_path, pdf_path.stat().st_size)
    return pdf_path


def _pdf_to_images(
    pdf_path: Path,
    output_dir: Path,
    dpi: int = DEFAULT_DPI,
) -> list[Path]:
    """Render PDF pages to PNG images using pdftoppm (poppler).

    pdftoppm produces higher quality output than pdf2image's PIL pipeline
    and handles embedded fonts more faithfully.

    Args:
        pdf_path: Path to the PDF file
        output_dir: Directory to save PNG images
        dpi: Output resolution

    Returns:
        List of paths to generated PNG images, sorted by page number
    """
    prefix = str(output_dir / "page")

    cmd = [
        "pdftoppm",
        "-png",
        "-r", str(dpi),
        "-aa", "yes",          # Anti-alias fonts
        "-aaVector", "yes",    # Anti-alias vector graphics
        str(pdf_path),
        prefix,
    ]

    logger.info("Rendering PDF to PNG at %d DPI", dpi)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        raise RuntimeError(f"pdftoppm failed: {result.stderr}")

    # pdftoppm creates files like page-01.png, page-02.png etc.
    # We need to rename them to page_001.png format for API compatibility
    output_paths: list[Path] = []
    raw_files = sorted(output_dir.glob("page-*.png"))

    if not raw_files:
        # Some versions use different naming: page-001.png
        raw_files = sorted(output_dir.glob("page*.png"))

    for raw_file in raw_files:
        # Extract page number from filename
        match = re.search(r"(\d+)", raw_file.stem.replace("page", ""))
        if match:
            page_num = int(match.group(1))
            new_name = output_dir / f"page_{page_num:03d}.png"
            if raw_file != new_name:
                raw_file.rename(new_name)
            output_paths.append(new_name)

    if not output_paths:
        raise RuntimeError(
            f"No PNG files generated from {pdf_path}. "
            f"Files in output: {list(output_dir.iterdir())}"
        )

    logger.info("Generated %d page images", len(output_paths))
    return sorted(output_paths)


def convert_pdf_to_images(
    pdf_path: Path,
    output_dir: Path,
    dpi: int = DEFAULT_DPI,
) -> list[Path]:
    """Convert PDF pages to PNG images using pdftoppm.

    Direct replacement for the old pdf2image-based conversion.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    return _pdf_to_images(pdf_path, output_dir, dpi)
