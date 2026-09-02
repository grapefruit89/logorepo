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


def namespace_internal_ids(root, symbol_id):
    """Keep referenced ids, prefix them with the symbol id, drop unused ids.

    Gradients/masks/clips must keep working inside the combined sprite,
    but raw ids like `a` must not collide across logos.
    """
    existing = collect_ids(root)
    used = referenced_ids(root)
    mapping = {}

    for old_id, element in existing.items():
        if old_id in used:
            new_id = f"{symbol_id}-{old_id}"
            mapping[old_id] = new_id
            element.set("id", new_id)
        else:
            del element.attrib["id"]

    if not mapping:
        return

    for element in root.iter():
        for name, value in list(element.attrib.items()):
            rewritten = rewrite_value(value, mapping)
            if rewritten != value:
                element.set(name, rewritten)

        if element.text:
            rewritten = rewrite_value(element.text, mapping)
            if rewritten != element.text:
                element.text = rewritten


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
    namespace_internal_ids(root, symbol_id)

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

    sprite = ET.Element(
        f"{{{SVG_NS}}}svg",
        {
            "aria-hidden": "true",
        },
    )

    used_ids = set()

    for path in files:
        symbol = build_symbol(path)
        symbol_id = symbol.get("id")

        if symbol_id in used_ids:
            raise ValueError(
                f"Doppelte Sprite-ID: #{symbol_id}"
            )

        used_ids.add(symbol_id)
        sprite.append(symbol)

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
    print(f"  {len(files)} Logos:")

    for symbol_id in sorted(used_ids):
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
