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

ET.register_namespace("", SVG_NS)


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def make_id(filename):
    name = Path(filename).stem.lower()
    name = re.sub(r"[^a-z0-9_-]+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")


def namespace_ids(root, prefix):
    mapping = {}

    for element in root.iter():
        old_id = element.get("id")

        if old_id:
            new_id = f"{prefix}-{old_id}"
            mapping[old_id] = new_id
            element.set("id", new_id)

    return mapping


def replace_references(value, mapping):
    for old, new in mapping.items():
        value = value.replace(f"url(#{old})", f"url(#{new})")
        value = value.replace(f'url("#{old}")', f'url("#{new}")')
        value = value.replace(f"url('#{old}')", f"url('#{new}')")
        value = value.replace(f"#{old}", f"#{new}")

    return value


def rewrite_references(root, mapping):
    for element in root.iter():
        for key, value in list(element.attrib.items()):
            element.set(key, replace_references(value, mapping))

        if element.text:
            element.text = replace_references(element.text, mapping)


def load_svg(path):
    try:
        tree = ET.parse(path)
    except ET.ParseError as error:
        raise ValueError(
            f"{path.name}: ungültiges SVG/XML: {error}"
        )

    root = tree.getroot()

    if local_name(root.tag) != "svg":
        raise ValueError(
            f"{path.name}: Root-Element ist kein <svg>"
        )

    return root


def build_symbol(path):
    symbol_id = make_id(path.name)

    if not symbol_id:
        raise ValueError(
            f"{path.name}: konnte keine gültige Sprite-ID erzeugen"
        )

    root = load_svg(path)

    mapping = namespace_ids(root, symbol_id)
    rewrite_references(root, mapping)

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
            symbol.set(
                attribute,
                replace_references(value, mapping),
            )

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