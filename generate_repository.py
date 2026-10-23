import hashlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DIST_DIR = Path("dist")
# Out of the datadir: Kodi can cache a listing of that folder and then
# answer "no such file" for the index inside it without asking the server.
INDEX_DIR = DIST_DIR / "index"


def collect_addons(dist: Path):
    addons = []
    for xml_path in sorted(dist.glob("*/addon.xml")):
        element = ET.parse(xml_path).getroot()
        if element.tag != "addon":
            print(
                f"Skipping {xml_path}: invalid root tag '{element.tag}'",
                file=sys.stderr,
            )
            continue
        addons.append(element)
    return addons


def main():
    addons = collect_addons(DIST_DIR)
    if not addons:
        print("No addons found in dist/", file=sys.stderr)
        sys.exit(1)

    root = ET.Element("addons")
    root.extend(addons)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    xml_path = INDEX_DIR / "addons.xml"
    ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)
    body = xml_path.read_bytes()
    md5 = hashlib.md5(body).hexdigest()
    (INDEX_DIR / "addons.xml.md5").write_text(md5)

    # Repositories already installed still read the old place, and dropping it
    # would leave them unable to fetch even the update that moves them.
    (DIST_DIR / "addons.xml").write_bytes(body)
    (DIST_DIR / "addons.xml.md5").write_text(md5)

    print(f"Generated {xml_path} ({len(addons)} addons) + addons.xml.md5, "
          f"and a copy at the old path")


if __name__ == "__main__":
    main()
