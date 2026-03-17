#!/usr/bin/env python3
"""
pptx_preprocess.py - Pre-process PPTX files to prevent text overflow in LibreOffice.

LibreOffice renders text ~13% wider than PowerPoint for the same font and size.
This module detects text boxes where overflow would occur and reduces font size
just enough to fit, preserving the original layout as closely as possible.

Uses fontTools for actual glyph width measurement from installed font files.
"""

import logging
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

logger = logging.getLogger("pptx_preprocess")

# LibreOffice renders text wider than raw glyph metrics.
# Measured empirically: ~13% wider, with 5% safety margin.
LO_WIDTH_FACTOR = 1.18

# Don't shrink fonts smaller than this factor of original (max 18% reduction)
MIN_SCALE = 0.82

# Only process fonts this size or larger (points) — small text rarely overflows visibly
MIN_FONT_SIZE_PT = 14

# Default font size (hundredths of a point) when no explicit size is set.
# OOXML spec says 18pt, but slide masters often override to 44pt for titles.
# We use the slide master's txStyles title default if available.
DEFAULT_FONT_SIZE_HUNDREDTHS = 1800  # 18pt fallback

DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PRESENTML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _resolve_font_file(font_name: str, bold: bool = False, italic: bool = False) -> str | None:
    """Resolve a font family name to a file path using fontconfig."""
    style_parts = []
    if bold:
        style_parts.append("Bold")
    if italic:
        style_parts.append("Italic")
    style_str = ":style=" + " ".join(style_parts) if style_parts else ""

    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", f"{font_name}{style_str}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
        )
        path = result.stdout.strip()
        if path and Path(path).exists():
            return path
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _measure_text_width(text: str, font_path: str, font_size_pt: float) -> float | None:
    """Measure text width in points using actual glyph metrics from the font file.

    Applies the LO_WIDTH_FACTOR to estimate how LibreOffice will actually render it.

    Returns width in points, or None if measurement fails.
    """
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(font_path)
        cmap = font.getBestCmap()
        if not cmap:
            return None

        hmtx = font["hmtx"]
        units_per_em = font["head"].unitsPerEm

        total_units = 0
        for char in text:
            glyph_name = cmap.get(ord(char))
            if glyph_name and glyph_name in hmtx.metrics:
                total_units += hmtx[glyph_name][0]
            else:
                # Unknown glyph — estimate as average width
                total_units += units_per_em * 0.6

        raw_width = (total_units / units_per_em) * font_size_pt
        return raw_width * LO_WIDTH_FACTOR

    except Exception as e:
        logger.debug("Font measurement failed for %s: %s", font_path, e)
        return None


def _get_master_default_font_sizes(zf: zipfile.ZipFile) -> dict[str, int]:
    """Get default font sizes from the slide master's txStyles.

    Returns a dict with keys like 'title', 'body', 'other' mapped to
    font size in hundredths of a point.
    """
    defaults = {
        "title": 4400,  # 44pt — common PowerPoint default
        "body": 1800,   # 18pt
        "other": 1800,
    }

    master_files = [n for n in zf.namelist() if "slideMaster" in n and n.endswith(".xml")]
    for mf in master_files:
        try:
            tree = ET.fromstring(zf.read(mf))
        except ET.ParseError:
            continue

        # Look for <p:txStyles> which has titleStyle, bodyStyle, otherStyle
        for ts in tree.iter(f"{{{PRESENTML_NS}}}txStyles"):
            for style_elem in ts:
                tag = style_elem.tag.split("}")[-1] if "}" in style_elem.tag else style_elem.tag
                # Find the level 1 default
                for lvl in style_elem:
                    lvl_tag = lvl.tag.split("}")[-1] if "}" in lvl.tag else lvl.tag
                    if "lvl1" in lvl_tag or lvl_tag == "defPPr":
                        defRPr = lvl.find(f"{{{DRAWINGML_NS}}}defRPr")
                        if defRPr is not None and defRPr.get("sz"):
                            sz = int(defRPr.get("sz"))
                            if "title" in tag.lower():
                                defaults["title"] = sz
                            elif "body" in tag.lower():
                                defaults["body"] = sz
                            elif "other" in tag.lower():
                                defaults["other"] = sz
        break  # Use first master

    return defaults


def _get_theme_fonts(zf: zipfile.ZipFile) -> tuple[str, str]:
    """Extract major (heading) and minor (body) font names from the theme."""
    heading_font = "Aptos Display"
    body_font = "Aptos"

    theme_files = [n for n in zf.namelist() if "theme" in n and n.endswith(".xml")]
    for tf in theme_files:
        try:
            tree = ET.fromstring(zf.read(tf))
        except ET.ParseError:
            continue

        for mf in tree.iter(f"{{{DRAWINGML_NS}}}majorFont"):
            latin = mf.find(f"{{{DRAWINGML_NS}}}latin")
            if latin is not None and latin.get("typeface"):
                heading_font = latin.get("typeface")

        for mf in tree.iter(f"{{{DRAWINGML_NS}}}minorFont"):
            latin = mf.find(f"{{{DRAWINGML_NS}}}latin")
            if latin is not None and latin.get("typeface"):
                body_font = latin.get("typeface")

    return heading_font, body_font


def _resolve_font_name(typeface: str | None, heading_font: str, body_font: str) -> str:
    """Resolve a typeface reference (which may be a theme reference like +mj-lt)."""
    if not typeface:
        return heading_font
    if typeface.startswith("+mj"):
        return heading_font
    if typeface.startswith("+mn"):
        return body_font
    return typeface


def _process_slide_xml(
    xml_bytes: bytes,
    heading_font: str,
    body_font: str,
    default_sizes: dict[str, int] | None = None,
) -> tuple[bytes, int]:
    """Process a single slide XML, detecting and fixing text overflow.

    Returns (modified_xml_bytes, modification_count).
    """
    tree = ET.fromstring(xml_bytes)
    modifications = 0
    if default_sizes is None:
        default_sizes = {"title": 4400, "body": 1800, "other": 1800}

    # Get slide dimensions for fallback (standard 16:9 = 12192000 x 6858000 EMU)
    slide_width_emu = 12192000
    slide_height_emu = 6858000

    # Build a list of filled rectangle shapes (potential background containers)
    # so we can match zero-width text shapes to their visual containers
    bg_rects: list[tuple[int, int, int, int]] = []  # (x, y, width, height)
    for bg_sp in tree.iter(f"{{{PRESENTML_NS}}}sp"):
        bg_spPr = bg_sp.find(f"{{{PRESENTML_NS}}}spPr")
        if bg_spPr is None:
            continue
        # Must have a fill (solid or gradient)
        has_fill = (
            bg_spPr.find(f"{{{DRAWINGML_NS}}}solidFill") is not None
            or bg_spPr.find(f"{{{DRAWINGML_NS}}}gradFill") is not None
        )
        if not has_fill:
            continue
        bg_xfrm = bg_spPr.find(f"{{{DRAWINGML_NS}}}xfrm")
        if bg_xfrm is None:
            continue
        bg_ext = bg_xfrm.find(f"{{{DRAWINGML_NS}}}ext")
        bg_off = bg_xfrm.find(f"{{{DRAWINGML_NS}}}off")
        if bg_ext is None:
            continue
        bg_cx = int(bg_ext.get("cx", "0"))
        bg_cy = int(bg_ext.get("cy", "0"))
        bg_x = int(bg_off.get("x", "0")) if bg_off is not None else 0
        bg_y = int(bg_off.get("y", "0")) if bg_off is not None else 0
        if bg_cx > 0:
            bg_rects.append((bg_x, bg_y, bg_cx, bg_cy))

    for sp in tree.iter(f"{{{PRESENTML_NS}}}sp"):
        # Get shape dimensions - xfrm is in drawingml ns, may be under p:spPr or a:spPr
        xfrm = sp.find(f".//{{{DRAWINGML_NS}}}xfrm")
        ext = xfrm.find(f"{{{DRAWINGML_NS}}}ext") if xfrm is not None else None

        shape_width_emu = int(ext.get("cx", "0")) if ext is not None else 0
        shape_height_emu = int(ext.get("cy", "0")) if ext is not None else 0

        # Fall back for shapes with no explicit dimensions
        if shape_width_emu == 0:
            # Try to find a filled background rectangle to use as container
            if bg_rects:
                best_bg = max(bg_rects, key=lambda r: r[2])
                shape_width_emu = best_bg[2]
                if shape_height_emu == 0:
                    shape_height_emu = best_bg[3]
            else:
                shape_width_emu = slide_width_emu
        elif bg_rects:
            # Even if the shape has dimensions, constrain to the background
            # rectangle if it's smaller (text visually overflows the banner)
            best_bg = max(bg_rects, key=lambda r: r[2])
            if best_bg[2] < shape_width_emu:
                shape_width_emu = best_bg[2]
        if shape_height_emu == 0:
            shape_height_emu = slide_height_emu

        # txBody can be in either namespace (p:txBody or a:txBody)
        txBody = sp.find(f"{{{PRESENTML_NS}}}txBody")
        if txBody is None:
            txBody = sp.find(f"{{{DRAWINGML_NS}}}txBody")
        if txBody is None:
            txBody = sp.find(f".//{{{DRAWINGML_NS}}}txBody")
        if txBody is None:
            continue

        # Get text body margins (bodyPr is always in drawingml namespace)
        bodyPr = txBody.find(f"{{{DRAWINGML_NS}}}bodyPr")
        if bodyPr is None:
            bodyPr = txBody.find(f".//{{{DRAWINGML_NS}}}bodyPr")
        l_ins = int(bodyPr.get("lIns", "91440")) if bodyPr is not None else 91440
        r_ins = int(bodyPr.get("rIns", "91440")) if bodyPr is not None else 91440
        t_ins = int(bodyPr.get("tIns", "45720")) if bodyPr is not None else 45720
        b_ins = int(bodyPr.get("bIns", "45720")) if bodyPr is not None else 45720

        usable_width_pt = (shape_width_emu - l_ins - r_ins) / 12700
        usable_height_pt = (shape_height_emu - t_ins - b_ins) / 12700

        if usable_width_pt <= 0:
            continue

        # Process each paragraph
        for p in txBody.findall(f"{{{DRAWINGML_NS}}}p"):
            runs = p.findall(f"{{{DRAWINGML_NS}}}r")
            if not runs:
                continue

            # Collect text and font info
            full_text = ""
            font_size_pt = None
            font_name = None
            is_bold = False
            is_italic = False

            for r in runs:
                t = r.find(f"{{{DRAWINGML_NS}}}t")
                if t is not None and t.text:
                    full_text += t.text

                rPr = r.find(f"{{{DRAWINGML_NS}}}rPr")
                if rPr is not None:
                    sz = rPr.get("sz")
                    if sz and font_size_pt is None:
                        font_size_pt = int(sz) / 100

                    if rPr.get("b") == "1":
                        is_bold = True
                    if rPr.get("i") == "1":
                        is_italic = True

                    latin = rPr.find(f"{{{DRAWINGML_NS}}}latin")
                    if latin is not None and font_name is None:
                        font_name = _resolve_font_name(
                            latin.get("typeface"), heading_font, body_font
                        )

            # Also check paragraph-level default run properties
            pPr = p.find(f"{{{DRAWINGML_NS}}}pPr")
            if pPr is not None:
                defRPr = pPr.find(f"{{{DRAWINGML_NS}}}defRPr")
                if defRPr is not None:
                    if font_size_pt is None:
                        sz = defRPr.get("sz")
                        if sz:
                            font_size_pt = int(sz) / 100
                    if font_name is None:
                        latin = defRPr.find(f"{{{DRAWINGML_NS}}}latin")
                        if latin is not None:
                            font_name = _resolve_font_name(
                                latin.get("typeface"), heading_font, body_font
                            )

            full_text = full_text.strip()
            if not full_text:
                continue

            # If no explicit font size found, use master defaults
            if font_size_pt is None:
                # Guess whether this is title-like text based on characteristics:
                # - All uppercase
                # - Short text (< 80 chars)
                # - Large shape
                is_title_like = (
                    full_text.isupper()
                    or len(full_text) < 60
                    or usable_width_pt > 500
                )
                default_key = "title" if is_title_like else "body"
                font_size_pt = default_sizes.get(default_key, 1800) / 100
                logger.debug(
                    "Using default font size %.0fpt for '%s' (key=%s)",
                    font_size_pt,
                    full_text[:40],
                    default_key,
                )

            if font_size_pt < MIN_FONT_SIZE_PT:
                continue

            if font_name is None:
                font_name = heading_font

            # Resolve font file
            font_file = _resolve_font_file(font_name, bold=is_bold, italic=is_italic)
            if not font_file:
                continue

            # Simulate word wrapping to find line breaks
            words = full_text.split()
            if not words:
                continue

            lines = []
            current_line = ""
            for word in words:
                test_line = f"{current_line} {word}".strip() if current_line else word
                w = _measure_text_width(test_line, font_file, font_size_pt)
                if w is None:
                    continue
                if w > usable_width_pt and current_line:
                    lines.append(current_line)
                    current_line = word
                else:
                    current_line = test_line
            if current_line:
                lines.append(current_line)

            # Check for overflow: any single word wider than the box
            needs_shrink = False
            max_overflow_ratio = 0

            for word in words:
                w = _measure_text_width(word, font_file, font_size_pt)
                if w is not None and w > usable_width_pt:
                    ratio = w / usable_width_pt
                    max_overflow_ratio = max(max_overflow_ratio, ratio)
                    needs_shrink = True

            # Check vertical overflow
            if usable_height_pt > 0 and font_size_pt >= 20:
                line_height = font_size_pt * 1.2
                max_lines = max(1, int(usable_height_pt / line_height))
                if len(lines) > max_lines:
                    # How much to shrink to fit vertically
                    v_ratio = len(lines) / max_lines
                    if v_ratio > max_overflow_ratio:
                        max_overflow_ratio = v_ratio
                        needs_shrink = True

            # Check if wrapped line count exceeds what the box can hold
            # (even without single-word overflow)
            if not needs_shrink and usable_height_pt > 0 and font_size_pt >= 20:
                line_height = font_size_pt * 1.25
                max_lines = max(1, int(usable_height_pt / line_height))
                if len(lines) > max_lines:
                    ratio = len(lines) / max_lines
                    max_overflow_ratio = ratio
                    needs_shrink = True

            if needs_shrink:
                # Calculate scale to fit
                scale = 1.0 / max_overflow_ratio
                scale = max(scale, MIN_SCALE)  # don't shrink too much
                scale *= 0.97  # extra 3% safety margin

                new_size_hundredths = int(font_size_pt * scale * 100)

                logger.info(
                    "Shrinking text: '%s' from %.0fpt to %.0fpt (scale %.2f) in %.0fpt-wide box",
                    full_text[:50],
                    font_size_pt,
                    new_size_hundredths / 100,
                    scale,
                    usable_width_pt,
                )

                # Apply size reduction to all runs in this paragraph
                for r in runs:
                    rPr = r.find(f"{{{DRAWINGML_NS}}}rPr")
                    if rPr is not None:
                        rPr.set("sz", str(new_size_hundredths))
                    else:
                        # Create rPr if it doesn't exist
                        rPr = ET.SubElement(r, f"{{{DRAWINGML_NS}}}rPr")
                        rPr.set("sz", str(new_size_hundredths))
                        # Insert before the text element
                        t_elem = r.find(f"{{{DRAWINGML_NS}}}t")
                        if t_elem is not None:
                            r.remove(rPr)
                            r.insert(list(r).index(t_elem), rPr)

                # Also set defRPr if present
                if pPr is not None:
                    defRPr = pPr.find(f"{{{DRAWINGML_NS}}}defRPr")
                    if defRPr is not None and defRPr.get("sz"):
                        defRPr.set("sz", str(new_size_hundredths))

                modifications += 1

    # Re-serialize
    # We need to preserve the original namespace declarations
    modified_xml = ET.tostring(tree, encoding="unicode", xml_declaration=False)
    return modified_xml.encode("utf-8"), modifications


def preprocess_pptx(input_path: Path, output_path: Path | None = None) -> tuple[Path, int]:
    """Pre-process a PPTX file to prevent text overflow in LibreOffice.

    Scans all slides for text boxes where text would overflow when rendered
    by LibreOffice, and reduces font size just enough to fit.

    Args:
        input_path: Path to the original PPTX file
        output_path: Where to save the modified PPTX (default: temp file)

    Returns:
        Tuple of (path_to_modified_pptx, number_of_modifications)
    """
    if output_path is None:
        temp_dir = tempfile.mkdtemp(prefix="docs2image_preprocess_")
        output_path = Path(temp_dir) / input_path.name

    with zipfile.ZipFile(input_path, "r") as zin:
        # Get theme fonts and master default sizes
        heading_font, body_font = _get_theme_fonts(zin)
        default_sizes = _get_master_default_font_sizes(zin)
        logger.info(
            "Theme fonts: heading=%s, body=%s; defaults: title=%.0fpt, body=%.0fpt",
            heading_font,
            body_font,
            default_sizes.get("title", 4400) / 100,
            default_sizes.get("body", 1800) / 100,
        )

        total_modifications = 0

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)

                # Process only actual slide XML files (not layouts, masters, or rels)
                if (
                    item.filename.endswith(".xml")
                    and item.filename.startswith("ppt/slides/slide")
                    and "/_rels/" not in item.filename
                ):
                    try:
                        modified_data, mods = _process_slide_xml(
                            data, heading_font, body_font, default_sizes
                        )
                        if mods > 0:
                            data = modified_data
                            total_modifications += mods
                            logger.info(
                                "Modified %s: %d text boxes adjusted",
                                item.filename,
                                mods,
                            )
                    except Exception as e:
                        logger.warning(
                            "Failed to process %s: %s", item.filename, e
                        )

                zout.writestr(item, data)

    logger.info("Pre-processing complete: %d modifications", total_modifications)
    return output_path, total_modifications
