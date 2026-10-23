"""Identifying a file by what the repo knows, not only by what it is called.

Every release the streams repo publishes is run through the resolver here. The
point is not that most of them work — the filename alone already manages that —
but that the handful a filename genuinely cannot settle come out right, and
that nothing comes out wrong.
"""
import json
import pathlib

import harness

harness.setup()

from lib import import_index as ix
from lib import import_match as m

FIXTURES = pathlib.Path(harness.FIXTURES)
CONFIG = json.loads((FIXTURES / "streams_config.json").read_text(encoding="utf-8"))
ARC_MAP, ALIASES = CONFIG["ARC_MAP"], CONFIG["ALIASES"]
RELEASES = json.loads((FIXTURES / "stream_releases.json").read_text(encoding="utf-8"))

ENTRIES = [(eid, {"filename": name, "videoSize": size})
           for eid, streams in RELEASES.items() for name, size in streams]
INDEX = ix.build(ENTRIES)

print("=== the three keys the repo gives us never collide ===")
print(f"  {len(ENTRIES)} releases across {len(RELEASES)} episodes")
for key in ("size", "crc", "name"):
    print(f"  {key:5} keys: {len(INDEX[key])}")
assert len(INDEX["size"]) == len(ENTRIES), "a size is shared by two releases"
assert len(INDEX["name"]) == len(ENTRIES), "a filename is shared by two releases"

print()
print("=== every release in the repo resolves to itself ===")
how = {}
for episode_id, streams in RELEASES.items():
    for name, size in streams:
        got = ix.resolve(name, size, INDEX, ARC_MAP, ALIASES)
        assert got["episode_id"] == episode_id, (name, got["episode_id"], episode_id)
        want = "extended" if "extended" in name.lower() else ""
        assert got["variant"] == want, (name, got["variant"])
        how[got["how"]] = how.get(got["how"], 0) + 1
print(f"  settled by: {how}")
assert how.get("guess", 0) == 0, "a guess was taken where the repo could confirm"
assert set(how) == {"size"}, how

print()
print("=== the names a filename cannot settle ===")
# These five are why the index exists. Two One Pace episodes share the name
# "Post-Enies Lobby 03", and the April Fools cuts are named exactly like the
# episodes they parody, so reading the name files them under the wrong one.
UNSETTLED = [
    ("[One Pace][436-437] Post-Enies Lobby 03 [720p][2C843A0F].mkv", "PEN_4", "PEN_3"),
    ("[One Pace][438-439] Post-Enies Lobby 04 [720p][659EF70C].mkv", "PEN_5", "PEN_4"),
    ("[One Pace][440-441] Post-Enies Lobby 05 [720p][CEF78887].mkv", "PEN_6", "PEN_5"),
    ("[One Pace][823-824] Whole Cake Island 01 [720p][EDB72EE5].mp4", "fools17", "WC_1"),
    ("[One Pace][199-201] Arabasta 16 - April Fools [480p][2F71D53E].mkv", "fools24", "AL_16"),
]
for name, right, name_says in UNSETTLED:
    byname = m.episode_id(m.parse(name), ARC_MAP, ALIASES)
    # No size: the file was renamed or re-encoded, so only the CRC is left.
    got = ix.resolve(name, 0, INDEX, ARC_MAP, ALIASES)
    print(f"  {name[:48]:<48} name says {byname:<7} CRC says {got['episode_id']}")
    assert byname == name_says, (name, byname)
    assert got["episode_id"] == right and got["how"] == "crc", got

print()
print("=== a renamed file is still found, by its size alone ===")
renamed = ix.resolve("my holiday video.mkv", 397698454, INDEX, ARC_MAP, ALIASES)
print(f"  'my holiday video.mkv' at 397,698,454 bytes -> "
      f"{renamed['episode_id']} ({renamed['how']})")
assert renamed["episode_id"] == "RO_1" and renamed["how"] == "size"

print()
print("=== a re-encode keeps its name and loses its size ===")
# The 720p cut in a real folder of downloads: a different size and a different
# CRC from anything the repo lists, so only the shape of the name is left.
got = ix.resolve("[One Pace][3-5] Romance Dawn 03 [720p][En CC][727376CF].mp4",
                 216984913, INDEX, ARC_MAP, ALIASES)
print(f"  -> {got['episode_id']} ({got['how']}), cut {got['variant']!r}")
assert got["episode_id"] == "RO_3" and got["how"] == "guess"

print()
print("=== nothing at all is nothing, not a guess ===")
for name, size in [("Holiday 2019.mp4", 123), ("", 0), ("random (1).mkv", 0)]:
    got = ix.resolve(name, size, INDEX, ARC_MAP, ALIASES)
    print(f"  {name!r:22} -> {got['episode_id']!r} ({got['how']!r})")
    assert got["episode_id"] == "" and got["how"] == ""

print()
print("=== a chapter bracket is never read as a CRC ===")
assert ix._crc_of("[Onigashima Paced][998] Onigashima 18 [1080p].mp4") == ""
assert ix._crc_of("[One Pace][3-5] Romance Dawn 03 [720p][727376CF].mp4") == "727376CF"
print("  [998] is a chapter, [727376CF] is a CRC  OK")

print()
print("=== the archive is one request, not eight hundred ===")
assert "tar.gz" in ix.ARCHIVE_URL and "codeload" in ix.ARCHIVE_URL
print(f"  {ix.ARCHIVE_URL}")

print()
print("all assertions passed")
