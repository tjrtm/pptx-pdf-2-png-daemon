#!/usr/bin/env python3
"""
font_utils.py - Comprehensive font extraction and installation for PPTX files.

Scans ALL relationship files in a PPTX to find embedded fonts,
handles .odttf deobfuscation, installs to user font directory,
and detects missing fonts that LibreOffice will need to substitute.
"""

import logging
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

logger = logging.getLogger("font_utils")

FONT_EXTENSIONS = {".ttf", ".otf", ".odttf", ".fntdata"}
DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

# User font directory — picked up by fontconfig and LibreOffice
USER_FONTS_DIR = Path.home() / ".local" / "share" / "fonts" / "pptx-extracted"


def _deobfuscate_odttf(data: bytes, guid: str) -> bytes:
    """Deobfuscate an .odttf font per OOXML spec (ECMA-376 Part 1 §14.2.7.2).

    The first 32 bytes are XOR'd with the GUID from the relationship ID.
    The GUID bytes are reversed per the spec.
    """
    guid_hex = guid.strip("{}").replace("-", "")
    if len(guid_hex) != 32:
        logger.warning("Invalid GUID for deobfuscation: %s", guid)
        return data

    key = bytes(int(guid_hex[i : i + 2], 16) for i in range(0, 32, 2))
    key = bytes(reversed(key))
    head = bytes(b ^ key[i % 16] for i, b in enumerate(data[:32]))
    return head + data[32:]


def _build_rel_id_map(zf: zipfile.ZipFile) -> dict[str, str]:
    """Build a map of font file paths → relationship GUIDs from ALL .rels files.

    Scans every .rels file in the archive, not just presentation.xml.rels.
    This catches fonts referenced from slides, slide masters, layouts, and themes.
    """
    rel_id_map: dict[str, str] = {}

    for name in zf.namelist():
        if not name.endswith(".rels"):
            continue
        try:
            tree = ET.fromstring(zf.read(name))
        except ET.ParseError:
            continue

        # Determine the directory context for resolving relative targets
        # e.g. ppt/slides/_rels/slide1.xml.rels → context is ppt/slides/
        rels_dir = str(Path(name).parent)  # e.g. ppt/_rels
        # The parent of _rels is the context dir
        if "/_rels" in rels_dir:
            context_dir = rels_dir.rsplit("/_rels", 1)[0]
        elif rels_dir == "_rels":
            context_dir = ""
        else:
            context_dir = rels_dir

        for rel in tree.findall(f"{{{RELS_NS}}}Relationship"):
            target = rel.get("Target", "")
            rel_id = rel.get("Id", "")
            rel_type = rel.get("Type", "")

            # Match font relationships by type or by file extension
            is_font_rel = (
                "font" in rel_type.lower()
                or any(target.lower().endswith(ext) for ext in FONT_EXTENSIONS)
            )

            if is_font_rel and rel_id:
                # Resolve relative path against context directory
                if target.startswith("/"):
                    resolved = target.lstrip("/")
                elif context_dir:
                    resolved = f"{context_dir}/{target}"
                else:
                    resolved = target

                # Normalize path (handle ../ etc.)
                resolved = str(Path(resolved))
                rel_id_map[resolved] = rel_id

    return rel_id_map


def _find_font_entries(zf: zipfile.ZipFile) -> list[str]:
    """Find ALL font file entries in the PPTX archive.

    Checks both ppt/fonts/ directory and any path with a font extension.
    Also inspects [Content_Types].xml for font content type declarations.
    """
    font_entries = set()

    # Method 1: Direct file extension matching anywhere in the archive
    for name in zf.namelist():
        suffix = Path(name).suffix.lower()
        if suffix in FONT_EXTENSIONS:
            font_entries.add(name)

    # Method 2: Check [Content_Types].xml for font MIME types
    content_types_path = "[Content_Types].xml"
    if content_types_path in zf.namelist():
        try:
            tree = ET.fromstring(zf.read(content_types_path))
            ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
            for override in tree.findall(f"{{{ct_ns}}}Override"):
                content_type = override.get("ContentType", "").lower()
                part_name = override.get("PartName", "")
                if "font" in content_type and part_name:
                    font_entries.add(part_name.lstrip("/"))
        except ET.ParseError:
            pass

    return sorted(font_entries)


def extract_pptx_fonts(pptx_path: Path, fonts_dir: Path | None = None) -> list[str]:
    """Extract ALL embedded fonts from a PPTX file and install them.

    Comprehensively scans all .rels files for font references,
    handles .odttf deobfuscation, and installs to the user font directory.

    Args:
        pptx_path: Path to the PPTX file
        fonts_dir: Override font installation directory (default: ~/.local/share/fonts/pptx-extracted/)

    Returns:
        List of newly installed font filenames
    """
    dest_dir = fonts_dir or USER_FONTS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    new_fonts: list[str] = []

    with zipfile.ZipFile(pptx_path, "r") as zf:
        # Build comprehensive relationship ID map from ALL .rels files
        rel_id_map = _build_rel_id_map(zf)
        logger.debug("Font rel_id_map: %s", rel_id_map)

        # Find all font entries in the archive
        font_entries = _find_font_entries(zf)
        logger.info("Found %d font entries in PPTX", len(font_entries))

        for entry in font_entries:
            suffix = Path(entry).suffix.lower()
            stem = Path(entry).stem

            # Determine output filename
            if suffix in (".odttf", ".fntdata"):
                out_name = stem + ".ttf"
            else:
                out_name = stem + suffix

            dest = dest_dir / out_name

            if dest.exists():
                logger.debug("Font already installed: %s", out_name)
                continue

            try:
                data = zf.read(entry)
            except KeyError:
                logger.warning("Font entry not found in archive: %s", entry)
                continue

            # Deobfuscate if needed
            if suffix in (".odttf", ".fntdata"):
                guid = rel_id_map.get(entry, "")
                if not guid:
                    # Try normalized path variations
                    normalized = str(Path(entry))
                    guid = rel_id_map.get(normalized, "")
                if not guid:
                    # Try just the filename against all values
                    entry_name = Path(entry).name
                    for key, val in rel_id_map.items():
                        if Path(key).name == entry_name:
                            guid = val
                            break
                if guid:
                    data = _deobfuscate_odttf(data, guid)
                    logger.info("Deobfuscated font: %s (GUID: %s)", entry, guid)
                else:
                    logger.warning(
                        "No GUID found for obfuscated font %s — "
                        "extracting raw (may not work)",
                        entry,
                    )

            # Validate: a TrueType/OpenType font should start with known signatures
            if suffix in (".odttf", ".fntdata", ".ttf"):
                # TrueType starts with 0x00010000 or 'true' or 'OTTO'
                sig = data[:4]
                valid_sigs = [
                    b"\x00\x01\x00\x00",
                    b"true",
                    b"OTTO",
                    b"typ1",
                ]
                if sig not in valid_sigs and len(data) > 4:
                    logger.warning(
                        "Font %s has unexpected signature %s — may be corrupt",
                        out_name,
                        sig.hex(),
                    )

            dest.write_bytes(data)
            new_fonts.append(out_name)
            logger.info("Installed font: %s → %s", entry, dest)

    # Rebuild font cache if we installed new fonts
    if new_fonts:
        logger.info("Rebuilding font cache for %s", dest_dir)
        result = subprocess.run(
            ["fc-cache", "-f", str(dest_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning("fc-cache failed: %s", result.stderr)

    return new_fonts


def get_used_font_names(pptx_path: Path) -> set[str]:
    """Parse PPTX XML to find all font family names referenced in the presentation.

    Scans slides, slide masters, layouts, and themes for DrawingML font references.

    Returns:
        Set of font family names (e.g. {"Calibri", "Arial", "Impact"})
    """
    font_names: set[str] = set()

    # Tags that carry font references in DrawingML
    font_tags = {"latin", "ea", "cs", "font", "sym"}

    with zipfile.ZipFile(pptx_path, "r") as zf:
        for name in zf.namelist():
            if not name.endswith(".xml"):
                continue
            # Only scan relevant XML parts
            if not any(
                part in name
                for part in (
                    "slide",
                    "theme",
                    "master",
                    "layout",
                    "presentation.xml",
                )
            ):
                continue

            try:
                tree = ET.fromstring(zf.read(name))
            except ET.ParseError:
                continue

            for elem in tree.iter():
                # Get local name from full qualified tag
                local = elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag
                if local in font_tags:
                    typeface = elem.get("typeface")
                    if typeface and not typeface.startswith("+"):
                        font_names.add(typeface)

    return font_names


def get_system_font_families() -> set[str]:
    """Get the set of font family names available on the system via fontconfig."""
    result = subprocess.run(
        ["fc-list", "--format", "%{family}\n"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    families: set[str] = set()
    for line in result.stdout.splitlines():
        # fc-list may return "Family1,Family2" for fonts with multiple names
        for name in line.split(","):
            name = name.strip()
            if name:
                families.add(name)
    return families


def check_missing_fonts(pptx_path: Path) -> list[str]:
    """Check which fonts used in a PPTX are not available on the system.

    Returns list of missing font family names.
    """
    used = get_used_font_names(pptx_path)
    available = get_system_font_families()

    # Case-insensitive comparison
    available_lower = {f.lower() for f in available}
    missing = [f for f in sorted(used) if f.lower() not in available_lower]

    if missing:
        logger.warning("Missing fonts: %s", ", ".join(missing))
    else:
        logger.info("All %d referenced fonts are available", len(used))

    return missing
