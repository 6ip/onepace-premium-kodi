"""Pull third-party add-ons straight from their upstream releases into dist/.

Nothing is committed to this repo — the released zip is fetched at build time
and served as-is, so what users install is byte-identical to upstream.
"""
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

DIST = Path("dist")

# id -> (version, release zip url)
VENDORED = {
    "repository.elementumorg": (
        "0.0.7",
        "https://github.com/ElementumOrg/repository.elementumorg"
        "/releases/download/v{version}/{id}-{version}.zip",
    ),
}


def fetch(addon_id, version, url_template):
    url = url_template.format(id=addon_id, version=version)
    print(f"  fetching {addon_id} v{version}")

    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read()

    archive = zipfile.ZipFile(io.BytesIO(payload))

    # Kodi requires a single top-level folder named exactly the add-on id.
    tops = {name.split("/")[0] for name in archive.namelist()}
    if tops != {addon_id}:
        raise SystemExit(f"    FAILED: unexpected zip layout {sorted(tops)}, expected ['{addon_id}']")

    # Version in the zip must match what we advertise, or Kodi offers a
    # download it can't find.
    with archive.open(f"{addon_id}/addon.xml") as f:
        addon_xml = f.read()
    import xml.etree.ElementTree as ET
    actual = ET.fromstring(addon_xml).get("version")
    if actual != version:
        raise SystemExit(f"    FAILED: addon.xml says v{actual}, expected v{version}")

    out = DIST / addon_id
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{addon_id}-{version}.zip").write_bytes(payload)
    (out / "addon.xml").write_bytes(addon_xml)
    with archive.open(f"{addon_id}/icon.png") as f:
        (out / "icon.png").write_bytes(f.read())

    print(f"    ok - {len(payload) // 1024} KB, {len(archive.namelist())} files")


def main():
    if not VENDORED:
        return
    for addon_id, (version, url) in VENDORED.items():
        try:
            fetch(addon_id, version, url)
        except SystemExit:
            raise
        except (urllib.error.URLError, OSError, zipfile.BadZipFile, KeyError) as exc:
            raise SystemExit(f"    FAILED: could not fetch {addon_id}: {exc}")


if __name__ == "__main__":
    main()
