#!/usr/bin/env python3

from pathlib import Path
from xml.etree import ElementTree as ET
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
LOGOS = ROOT / "logos"
DIST = ROOT / "dist"
OUTPUT = DIST / "icons.svg"

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)

URL_REF_RE = re.compile(r"url\(#([^)]+)\)")
HASH_REF_RE = re.compile(r"^#(.+)$")
PAINT_TAGS = {
    "linearGradient",
    "radialGradient",
    "pattern",
    "clipPath",
    "mask",
    "filter",
    "marker",
}


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def make_id(filename):
    name = Path(filename).stem.lower()
    name = re.sub(r"[^a-z0-9_-]+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")


def load_svg(path):
    try:
        tree = ET.parse(path)
    except ET.ParseError as error:
        raise ValueError(f"{path.name}: ungültiges SVG/XML: {error}")

    root = tree.getroot()

    if local_name(root.tag) != "svg":
        raise ValueError(f"{path.name}: Root-Element ist kein <svg>")

    return root


def modernize_xlink(root):
    xlink_href = f"{{{XLINK_NS}}}href"

    for element in root.iter():
        if xlink_href in element.attrib:
            value = element.attrib.pop(xlink_href)
            element.set("href", value)


def collect_ids(root):
    ids = {}

    for element in root.iter():
        element_id = element.get("id")
        if element_id:
            ids[element_id] = element

    return ids


def referenced_ids(root):
    found = set()

    for element in root.iter():
        for value in element.attrib.values():
            found.update(URL_REF_RE.findall(value))

            match = HASH_REF_RE.match(value)
            if match:
                found.add(match.group(1))

        if element.text:
            found.update(URL_REF_RE.findall(element.text))

    return found


def rewrite_value(value, mapping):
    def replace_url(match):
        old_id = match.group(1)
        new_id = mapping.get(old_id, old_id)
        return f"url(#{new_id})"

    value = URL_REF_RE.sub(replace_url, value)

    match = HASH_REF_RE.match(value)
    if match:
        old_id = match.group(1)
        if old_id in mapping:
            value = f"#{mapping[old_id]}"

    return value


def clone_element(element, drop_id=True):
    attrib = dict(element.attrib)
    if drop_id:
        attrib.pop("id", None)

    clone = ET.Element(element.tag, attrib)
    clone.text = element.text
    clone.tail = element.tail

    for child in element:
        clone.append(clone_element(child, drop_id=drop_id))

    return clone


def local_href(element):
    value = element.get("href")
    if not value:
        return None

    match = HASH_REF_RE.match(value)
    if not match:
        return None

    return match.group(1)


def combine_transform(base, extra):
    parts = [part for part in (base, extra) if part]
    return " ".join(parts) if parts else None


def expand_use_elements(root):
    """Replace local <use href=\"#id\"> with copies of the referenced nodes.

    That removes geometry IDs that would otherwise leak into the sprite
    and make the public logo IDs ambiguous.
    """
    for _ in range(32):
        uses = [
            element
            for element in root.iter()
            if local_name(element.tag) == "use" and local_href(element)
        ]

        if not uses:
            return

        ids = collect_ids(root)
        expanded = 0

        for use in uses:
            parent = None
            index = None

            for candidate in root.iter():
                children = list(candidate)
                if use in children:
                    parent = candidate
                    index = children.index(use)
                    break

            if parent is None:
                continue

            target_id = local_href(use)
            target = ids.get(target_id)

            if target is None or target is use:
                continue

            replacement = clone_element(target, drop_id=True)

            for name, value in use.attrib.items():
                if name in {"id", "href"}:
                    continue

                if name == "transform":
                    replacement.set(
                        "transform",
                        combine_transform(replacement.get("transform"), value),
                    )
                    continue

                if name not in replacement.attrib:
                    replacement.set(name, value)

            replacement.tail = use.tail
            parent.remove(use)
            parent.insert(index, replacement)
            expanded += 1

        if expanded == 0:
            return

    raise ValueError("konnte interne <use>-Referenzen nicht vollständig auflösen")


def parent_of(root, target):
    for element in root.iter():
        if target in list(element):
            return element
    return None


def drop_unused_defs(root):
    used = referenced_ids(root)

    for defs in list(root.iter()):
        if local_name(defs.tag) != "defs":
            continue

        for child in list(defs):
            child_id = child.get("id")
            if child_id and child_id in used:
                continue
            defs.remove(child)

        if len(defs) == 0:
            parent = parent_of(root, defs)
            if parent is not None:
                parent.remove(defs)


def namespace_internal_ids(root, symbol_id):
    """Keep only referenced paint-server IDs. Prefix them so they cannot be
    mistaken for public logo IDs when someone scans every id attribute.
    """
    existing = collect_ids(root)
    used = referenced_ids(root)
    mapping = {}

    for old_id, element in existing.items():
        if old_id not in used:
            del element.attrib["id"]
            continue

        if local_name(element.tag) not in PAINT_TAGS:
            del element.attrib["id"]
            continue

        new_id = f"_{symbol_id}-{old_id}"
        mapping[old_id] = new_id
        element.set("id", new_id)

    if mapping:
        for element in root.iter():
            for name, value in list(element.attrib.items()):
                rewritten = rewrite_value(value, mapping)
                if rewritten != value:
                    element.set(name, rewritten)

            if element.text:
                rewritten = rewrite_value(element.text, mapping)
                if rewritten != element.text:
                    element.text = rewritten

    drop_unused_defs(root)


def build_symbol(path):
    symbol_id = make_id(path.name)

    if not symbol_id:
        raise ValueError(
            f"{path.name}: konnte keine gültige Sprite-ID erzeugen"
        )

    root = load_svg(path)

    viewbox = root.get("viewBox")

    if not viewbox:
        width = root.get("width")
        height = root.get("height")

        if width and height:
            viewbox = f"0 0 {width} {height}"
        else:
            raise ValueError(
                f"{path.name}: kein viewBox vorhanden"
            )

    modernize_xlink(root)
    expand_use_elements(root)
    drop_unused_defs(root)
    namespace_internal_ids(root, symbol_id)

    leftover_uses = [
        element
        for element in root.iter()
        if local_name(element.tag) == "use" and local_href(element)
    ]
    if leftover_uses:
        raise ValueError(
            f"{path.name}: unaufgelöste interne Referenz {[local_href(u) for u in leftover_uses]}"
        )

    symbol = ET.Element(
        f"{{{SVG_NS}}}symbol",
        {
            "id": symbol_id,
            "viewBox": viewbox,
        },
    )

    for attribute in (
        "preserveAspectRatio",
        "fill",
        "stroke",
        "stroke-width",
    ):
        value = root.get(attribute)

        if value is not None:
            symbol.set(attribute, value)

    for child in list(root):
        root.remove(child)
        symbol.append(child)

    return symbol


def indent(element, level=0):
    indentation = "\n" + "  " * level

    if len(element):
        if not element.text or not element.text.strip():
            element.text = indentation + "  "

        for child in element:
            indent(child, level + 1)

            if not child.tail or not child.tail.strip():
                child.tail = indentation + "  "

        element[-1].tail = indentation


PREVIEW_COLS = 6
PREVIEW_CELL = 96
PREVIEW_LABEL = 22
PREVIEW_GAP = 28
PREVIEW_PAD = 28


def add_preview_sheet(sprite, symbol_ids):
    """Make the sprite visible when opened as a file.

    <symbol> is only a definition. Without <use>, a browser/editor shows
    an empty white canvas even though all logo data is present.
    """
    count = len(symbol_ids)
    cols = min(PREVIEW_COLS, count) or 1
    rows = (count + cols - 1) // cols

    width = PREVIEW_PAD * 2 + cols * PREVIEW_CELL + (cols - 1) * PREVIEW_GAP
    height = (
        PREVIEW_PAD * 2
        + rows * (PREVIEW_CELL + PREVIEW_LABEL)
        + (rows - 1) * PREVIEW_GAP
    )

    sprite.set("viewBox", f"0 0 {width} {height}")
    sprite.set("width", str(width))
    sprite.set("height", str(height))
    sprite.set("role", "img")

    background = ET.Element(
        f"{{{SVG_NS}}}rect",
        {
            "width": "100%",
            "height": "100%",
            "fill": "#f3f4f6",
        },
    )
    sprite.append(background)

    preview = ET.Element(
        f"{{{SVG_NS}}}g",
        {
            "data-preview": "true",
        },
    )

    for index, symbol_id in enumerate(symbol_ids):
        col = index % cols
        row = index // cols
        x = PREVIEW_PAD + col * (PREVIEW_CELL + PREVIEW_GAP)
        y = PREVIEW_PAD + row * (PREVIEW_CELL + PREVIEW_LABEL + PREVIEW_GAP)

        tile = ET.Element(
            f"{{{SVG_NS}}}rect",
            {
                "x": str(x),
                "y": str(y),
                "width": str(PREVIEW_CELL),
                "height": str(PREVIEW_CELL),
                "rx": "16",
                "fill": "#fff",
                "stroke": "#e5e7eb",
            },
        )
        icon = ET.Element(
            f"{{{SVG_NS}}}use",
            {
                "href": f"#{symbol_id}",
                "x": str(x + 12),
                "y": str(y + 12),
                "width": str(PREVIEW_CELL - 24),
                "height": str(PREVIEW_CELL - 24),
            },
        )
        label = ET.Element(
            f"{{{SVG_NS}}}text",
            {
                "x": str(x + PREVIEW_CELL / 2),
                "y": str(y + PREVIEW_CELL + 16),
                "text-anchor": "middle",
                "font-family": "ui-sans-serif, system-ui, sans-serif",
                "font-size": "12",
                "fill": "#374151",
            },
        )
        label.text = symbol_id

        preview.append(tile)
        preview.append(icon)
        preview.append(label)

    sprite.append(preview)


def build():
    if not LOGOS.exists():
        raise SystemExit(
            f"Fehler: {LOGOS} existiert nicht."
        )

    files = sorted(
        LOGOS.glob("*.svg"),
        key=lambda path: path.name.lower(),
    )

    if not files:
        raise SystemExit(
            f"Fehler: Keine SVG-Dateien in {LOGOS}"
        )

    used_ids = []
    symbols = []

    for path in files:
        symbol = build_symbol(path)
        symbol_id = symbol.get("id")

        if symbol_id in used_ids:
            raise ValueError(
                f"Doppelte Sprite-ID: #{symbol_id}"
            )

        used_ids.append(symbol_id)
        symbols.append(symbol)

    sprite = ET.Element(
        f"{{{SVG_NS}}}svg",
        {
            "data-icons": " ".join(used_ids),
        },
    )

    for symbol in symbols:
        sprite.append(symbol)

    add_preview_sheet(sprite, used_ids)

    indent(sprite)

    DIST.mkdir(parents=True, exist_ok=True)

    xml = ET.tostring(
        sprite,
        encoding="unicode",
    )

    OUTPUT.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + xml
        + "\n",
        encoding="utf-8",
    )

    print()
    print("✓ SVG-Sprite gebaut")
    print()
    print(f"  Quelle : {LOGOS}")
    print(f"  Ausgabe: {OUTPUT}")
    print()
    print(f"  {len(files)} öffentliche IDs:")

    for symbol_id in used_ids:
        print(f"    #{symbol_id}")

    print()


if __name__ == "__main__":
    try:
        build()
    except ValueError as error:
        print()
        print(f"✗ Fehler: {error}")
        print()
        sys.exit(1)
