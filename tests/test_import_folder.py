"""Adopting a folder of episodes somebody already has.

The folder in these cases is flat and messy on purpose: a real hard disk of One
Pace is as likely to be one heap of files as it is neat seasons, so nothing
here may depend on the shape of the folders.
"""
import json
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
_real_catalogues = imp._catalogues
imp._catalogues = lambda: (
    {"RO_1": ("One Pace", META["videos"][0]),
     "RO_3": ("One Pace", META["videos"][1]),
     "pp_fools17": ("One Pace", META["videos"][2]),
     "fools17": ("One Pace", META["videos"][2])},
    {"One Pace": META})

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
print("all assertions passed")
