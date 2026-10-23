"""Adopting a folder of episodes somebody already has.

The folder in these cases is flat and messy on purpose: a real hard disk of One
Pace is as likely to be one heap of files as it is neat seasons, so nothing
here may depend on the shape of the folders.
"""
import json
import os
import pathlib

import harness

store = harness.setup({"downloads_enabled": "true"})

import kodistub
from lib import import_folder as imp
from lib import import_index as ix

FIXTURES = pathlib.Path(harness.FIXTURES)
CONFIG = json.loads((FIXTURES / "streams_config.json").read_text(encoding="utf-8"))
RELEASES = json.loads((FIXTURES / "stream_releases.json").read_text(encoding="utf-8"))
ENTRIES = [(eid, {"filename": name, "videoSize": size})
           for eid, streams in RELEASES.items() for name, size in streams]
INDEX = ix.build(ENTRIES)

ROOT = "E:/Anime/One Pace/"
# One heap, a nested folder, and something that is not an episode at all.
TREE = {
    ROOT: (["Extras"], ["[One Pace][1] Romance Dawn 01 [1080p][E5F09F49].mkv",
                        "[One Pace][3-5] Romance Dawn 03 [720p][En CC][727376CF].mp4",
                        "cover.jpg", "notes.txt"]),
    ROOT + "Extras/": ([".onepace-subs"], ["holiday clip.mkv"]),
    ROOT + "Extras/.onepace-subs/": ([], ["should-never-be-walked.mkv"]),
}
SIZES = {
    ROOT + "[One Pace][1] Romance Dawn 01 [1080p][E5F09F49].mkv": 397698454,
    ROOT + "[One Pace][3-5] Romance Dawn 03 [720p][En CC][727376CF].mp4": 216984913,
    ROOT + "Extras/holiday clip.mkv": 12345,
}

TWICE = "G:/Both/"
# The same episode at two qualities: the 1080p matches on size, the 720p only
# by name. Both are RO_3 standard, so only one of them can take the seat.
TREE[TWICE] = ([], ["[One Pace][3-5] Romance Dawn 03 [1080p][2DEB2701].mkv",
                    "[One Pace][3-5] Romance Dawn 03 [720p][En CC][727376CF].mp4"])
SIZES[TWICE + "[One Pace][3-5] Romance Dawn 03 [1080p][2DEB2701].mkv"] = 705395954
SIZES[TWICE + "[One Pace][3-5] Romance Dawn 03 [720p][En CC][727376CF].mp4"] = 216984913

CUTS = "F:/Kuma/"
TREE[CUTS] = ([], ["One Piece - 1131 [Kuma Cut].mp4",
                   "[DUB] One Piece - 1127+8 [Shaved Egghead].mp4"])
SIZES[CUTS + "One Piece - 1131 [Kuma Cut].mp4"] = 518581763
SIZES[CUTS + "[DUB] One Piece - 1127+8 [Shaved Egghead].mp4"] = 810092048

imp.xbmcvfs.listdir = lambda path: TREE.get(path, ([], []))
imp._size_on_disk = lambda path: SIZES.get(path, 0)
imp._sidecars = lambda path: ()
imp.download_queue.busy = lambda: False
imp.folder = lambda: "C:/kodi/downloads/"
imp._releases = lambda: (INDEX, CONFIG)
imp.refresh_container = lambda: None

# Two series are enough to prove the lookup indexes both id spellings.
META = {"id": "pp_onepacee", "name": "One Pace", "logo": "logo.png",
        "seasons": [{"season": 1, "poster": "s1.jpg"}],
        "videos": [{"id": "RO_1", "season": 1, "episode": 1, "name": "Romance Dawn",
                    "thumbnail": "t1.jpg"},
                   {"id": "RO_3", "season": 1, "episode": 3, "name": "The Pirate Hunter",
                    "thumbnail": "t3.jpg"},
                   {"id": "pp_fools17", "season": 0, "episode": 7, "name": "A World Abuzz"}]}
KUMA = {"id": "pp_KUMA_SHAVED", "name": "Shaved Egghead & Kuma Cut",
        "logo": "k.png", "seasons": [{"season": 1, "poster": "k1.jpg"}],
        "videos": [{"id": "pp_KUMA_SHAVED_1", "season": 1, "episode": 1,
                    "name": "Shaved Egghead 1127+8"},
                   {"id": "pp_KUMA_SHAVED_4", "season": 1, "episode": 4,
                    "name": "Kuma Cut 1131"}]}
_real_catalogues = imp._catalogues
imp._catalogues = lambda: (
    {"RO_1": ("One Pace", META["videos"][0]),
     "RO_3": ("One Pace", META["videos"][1]),
     "pp_fools17": ("One Pace", META["videos"][2]),
     "fools17": ("One Pace", META["videos"][2])},
    {"One Pace": META, KUMA["name"]: KUMA})

written = []
imp.read_index = lambda: {"files": dict(written[-1]["files"]) if written else {},
                          "series": {}}
imp._write_index = lambda data: written.append(data)
reports = []
imp.write_report = lambda heading, lines: reports.append((heading, lines))

D = harness.dialog()


def attempt(answer=True, browse=ROOT, cancel=None):
    rec = harness.recorder()
    written.clear()
    reports.clear()
    imp.xbmcgui.DialogProgress.cancel_after = cancel
    D.browse_answers = [browse]
    D.yesno_answers = [answer]
    imp.run()
    return rec


print("=== a flat folder of mixed files is read without complaint ===")
rec = attempt()
rows = written[-1]["files"] if written else {}
for path in sorted(rows):
    row = rows[path]
    print(f"  {path.split('/')[-1][:46]:<46} -> {row['episode_id']} "
          f"s{row['season']}e{row['episode']}")
assert len(rows) == 2, sorted(rows)
assert all(r["imported"] is True for r in rows.values()), "all of these are theirs"
assert len(written) == 1, "the index should be written once, not once per file"

print()
print("=== it never walks into our own subtitle folders ===")
walked = [p for p in rows if "should-never-be-walked" in p]
assert not walked, walked
print("  .onepace-subs was skipped  OK")

print()
print("=== the provider decides the titles, not the filenames ===")
titles = sorted(r["episode_title"] for r in rows.values())
print(f"  {titles}")
assert titles == ["Romance Dawn", "The Pirate Hunter"]
one = [r for r in rows.values() if r["episode_id"] == "RO_1"][0]
assert one["season_poster"] == "s1.jpg" and one["logo"] == "logo.png"
assert one["series_id"] == "pp_onepacee"

print()
print("=== nothing is written until it is asked for ===")
attempt(answer=False)
print(f"  answered Cancel -> index writes: {len(written)}")
assert not written, "a cancelled import still wrote rows"
assert reports, "the report is still worth keeping"

print()
print("=== an episode already in the list is left alone ===")
rec = attempt()
first = dict(written[-1]["files"])
imp.read_index = lambda: {"files": dict(first), "series": {}}
rec = attempt()
print(f"  second run -> {len(written)} write(s)")
assert not written, "everything was already there, so there was nothing to add"
imp.read_index = lambda: {"files": dict(written[-1]["files"]) if written else {},
                          "series": {}}

print()
print("=== what it could not read is what the report is for ===")
attempt()
heading, lines = reports[-1]
body = chr(10).join(lines)
print(f"  {heading}: {[l for l in lines if 'not recognised' in l]}")
assert "holiday clip.mkv" in body, "the unreadable names are the useful part"
assert "1 not recognised" in body

print()
print("=== a guess is counted apart from what the repo confirmed ===")
# Romance Dawn 01 matches on size; the 720p re-encode only matches by name.
assert "1 identified from the release list" in body, body
assert "1 matched by name only" in body, body

print()
print("=== it refuses the jobs it should refuse ===")
rec = attempt(browse="")
print(f"  no folder chosen     -> writes {len(written)}")
assert not written

imp.download_queue.busy = lambda: True
rec = attempt()
print(f"  a download running   -> {rec.notifications}")
assert not written
imp.download_queue.busy = lambda: False

rec = attempt(browse="C:/kodi/downloads/One Pace/")
print(f"  our own folder       -> writes {len(written)}")
assert not written, "Rescan is for our own folder, not Import"

store["downloads_enabled"] = "false"
rec = attempt()
print(f"  downloads switched off -> {[m for m, _ in rec.notifications]}")
assert not written and rec.notifications
store["downloads_enabled"] = "true"

print()
print("=== cancelling the walk stops there ===")
rec = attempt(cancel=0)
print(f"  cancelled -> writes {len(written)}")
assert not written

print()
print("=== a folder nobody named in English works the same ===")
# A real download folder: D:/تحميلات/Video/Test_ONEPACE. Nothing here may
# depend on the path being ASCII, and the index escapes what it writes.
ARABIC = "D:/\u062a\u062d\u0645\u064a\u0644\u0627\u062a/Video/Test_ONEPACE/"
ARABIC_FILE = ARABIC + "[One Pace][1] Romance Dawn 01 [1080p][E5F09F49].mkv"
TREE[ARABIC] = ([], [os.path.basename(ARABIC_FILE)])
SIZES[ARABIC_FILE] = 397698454
rec = attempt(browse=ARABIC)
rows = written[-1]["files"] if written else {}
print(f"  {len(rows)} row(s) from {ARABIC}")
assert len(rows) == 1 and list(rows)[0] == ARABIC_FILE, sorted(rows)
# json.dumps escapes it, so the index is plain ASCII on disk either way.
assert json.loads(json.dumps(written[-1])), "the index has to round-trip"

print()
print("=== an episode is found whether or not its id carries the pp_ ===")
# The streams repo calls it fools17; the provider calls it pp_fools17. Both
# spellings have to find the same episode, or every special fails to import.
from lib import downloads as _dl
from lib import provider_api as _api
_dl._series_ids = lambda: {"One Pace": "pp_onepacee"}
_api._fetch_provider_meta = lambda catalog_type, series_id: META
found, metas = _real_catalogues()
for probe in ("RO_1", "pp_fools17", "fools17"):
    series, video = found.get(probe, ("", {}))
    print(f"  {probe!r:12} -> {video.get('id')!r} in {series!r}")
    assert video, probe
assert found["fools17"][1]["id"] == "pp_fools17", "the bare spelling must resolve"
assert metas["One Pace"] is META

print()
print("=== the cuts with no arc are found by the title the catalogue uses ===")
# Neither of these is the official release — both are re-encodes, so the size,
# the CRC and the full name all miss. What is left is "Kuma Cut 1131".
rec = attempt(browse=CUTS)
rows = written[-1]["files"] if written else {}
for path in sorted(rows):
    print(f"  {path.split('/')[-1][:44]:<44} -> {rows[path]['episode_id']}")
assert sorted(r["episode_id"] for r in rows.values()) == [
    "pp_KUMA_SHAVED_1", "pp_KUMA_SHAVED_4"], sorted(rows.values(), key=str)
assert all(r["imported"] is True for r in rows.values())

print()
print("=== what it cannot read, somebody can say by hand ===")
# Review, then series -> season -> episode for the one unreadable file.
D.select_answers = [0, 0, 0, 0]
rec = attempt(answer=2)
rows = written[-1]["files"] if written else {}
chosen = [p for p in rows if "holiday" in p]
print(f"  assigned by hand: {[p.split('/')[-1] for p in chosen]}")
print(f"  as: {rows[chosen[0]]['episode_id']} "
      f"s{rows[chosen[0]]['season']}e{rows[chosen[0]]['episode']}")
assert chosen, "the file they assigned should have been added"
assert rows[chosen[0]]["imported"] is True
# The report is written last, so it counts what actually happened.
body = chr(10).join(reports[-1][1])
print(f"  report says: {[l for l in reports[-1][1] if 'hand' in l]}")
assert "1 settled by hand" in body and "not recognised" not in body, body

print()
print("=== backing out of the picker leaves the file alone ===")
D.select_answers = [0, -1]
rec = attempt(answer=2)
rows = written[-1]["files"] if written else {}
print(f"  cancelled at the season -> holiday clip in index: "
      f"{any('holiday' in p for p in rows)}")
assert not any("holiday" in p for p in rows)
assert len(rows) == 2, "the ones it worked out itself still go in"

print()
print("=== assigning an episode that is already listed asks first ===")
first = dict(written[-1]["files"])
imp.read_index = lambda: {"files": dict(first), "series": {}}
# Review, the file, One Pace, Season 1 (index 1, after Specials), its first
# episode RO_1 — which the run before already listed — then "Leave this out".
D.select_answers = [0, 0, 1, 0, 2]
rec = attempt(answer=2)
print(f"  chose 'leave it out' -> writes {len(written)}")
assert not written, "nothing new to write, so nothing should be written"

D.select_answers = [0, 0, 1, 0, 1]
rec = attempt(answer=2)
rows = written[-1]["files"]
kept = [p for p in rows if "Romance Dawn 01" in p]
print(f"  chose 'use this one instead' -> the listed one went: {not kept}")
assert any("holiday" in p for p in rows), "their choice should be in"
assert not kept, "the row it replaces should have gone"
imp.read_index = lambda: {"files": dict(written[-1]["files"]) if written else {},
                          "series": {}}

print()
print("=== one episode cannot take two seats without being asked ===")
# This is what makes a list show the same episode twice: a 720p and a 1080p of
# one episode, both correctly identified, both added without a word.
rec = attempt(answer=1, browse=TWICE)
rows = written[-1]["files"] if written else {}
print(f"  {len(rows)} row(s) written from two files of the same episode")
for path in rows:
    print(f"    {path.split('/')[-1][:50]}")
assert len(rows) == 1, "the second one should have waited to be asked about"
body = chr(10).join(reports[-1][1])
print(f"  report: {[l for l in reports[-1][1] if 'already held' in l]}")
assert "1 left out, already held as another file" in body, body

print()
print("=== and Review is where the second one gets its yes ===")
# Pick the clash, then "Add this one as well".
D.select_answers = [0, 0]
rec = attempt(answer=2, browse=TWICE)
rows = written[-1]["files"] if written else {}
print(f"  after Review -> {len(rows)} row(s)")
assert len(rows) == 2, "they said to add it, so it goes in"
assert all(r["episode_id"] == "RO_3" for r in rows.values()), rows

print()
print("=== a clash is never asked which episode it is ===")
# It already knows, so Review skips straight past the series, season and
# episode questions — one answer, the file itself. Where the episode it
# clashes with is already in the index, the three-way question follows; here
# both files arrived in the same run, so there is nothing stored yet to keep
# or replace, and picking it means adding it.
assert len(D.select_answers) == 1, f"too many questions asked: {D.select_answers}"

print()
print("=== our own downloads are never adopted as somebody else's ===")
# Choosing a parent of our download folder is a fair thing to want. Walking
# into it would mark our own files imported, and Delete would then stop
# deleting them while Check for Updates skipped them for good.
PARENT = "D:/Media/"
OURS_DIR = "D:/Media/One Pace Premium/"
TREE[PARENT] = (["One Pace Premium", "Mine"], [])
TREE[PARENT + "Mine/"] = ([], ["[One Pace][1] Romance Dawn 01 [1080p][E5F09F49].mkv"])
TREE[OURS_DIR] = ([], ["1x01 - Romance Dawn.mkv"])
SIZES[PARENT + "Mine/[One Pace][1] Romance Dawn 01 [1080p][E5F09F49].mkv"] = 397698454
SIZES[OURS_DIR + "1x01 - Romance Dawn.mkv"] = 397698454
imp.folder = lambda: OURS_DIR
imp._ours = lambda: (OURS_DIR,)

rec = attempt(answer=1, browse=PARENT)
rows = written[-1]["files"] if written else {}
print(f"  rows written: {[p.split('/')[-1] for p in rows]}")
assert len(rows) == 1, sorted(rows)
assert OURS_DIR not in list(rows)[0], "our own download was adopted as theirs"
assert list(rows)[0].startswith(PARENT + "Mine/"), list(rows)

# And choosing the folder itself is still refused outright, with a reason.
rec = attempt(answer=1, browse=OURS_DIR)
print(f"  choosing it directly -> writes {len(written)}, said {rec.notifications}")
assert not written
imp.folder = lambda: "C:/kodi/downloads/"
imp._ours = lambda: ()

print()
print("=== two files cannot quietly take the same episode in one pass ===")
# The stored index knows nothing of what was settled a moment ago, so without
# a check the second one is written beside the first with no word said.
TWO = "H:/Unknown/"
TREE[TWO] = ([], ["mystery one.mkv", "mystery two.mkv"])
SIZES[TWO + "mystery one.mkv"] = 111
SIZES[TWO + "mystery two.mkv"] = 222
imp.read_index = lambda: {"files": {}, "series": {}}

# Review; file 0 -> One Pace, Season 1, RO_1; then file 0 again -> the same,
# and answer "Leave this file out" when it asks.
D.select_answers = [0, 0, 1, 0, 0, 0, 1, 0, 2]
rec = attempt(answer=2, browse=TWO)
rows = written[-1]["files"] if written else {}
print(f"  asked, answered 'leave out' -> {len(rows)} row(s)")
assert len(rows) == 1, sorted(rows)

# And "Use this one instead" keeps the second, dropping the first.
D.select_answers = [0, 0, 1, 0, 0, 0, 1, 0, 1]
rec = attempt(answer=2, browse=TWO)
rows = written[-1]["files"] if written else {}
print(f"  asked, answered 'use this instead' -> {[p.split('/')[-1] for p in rows]}")
assert len(rows) == 1 and "mystery two" in list(rows)[0], sorted(rows)
imp.read_index = lambda: {"files": dict(written[-1]["files"]) if written else {},
                          "series": {}}

print()
print("all assertions passed")
