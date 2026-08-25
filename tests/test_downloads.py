"""Keeping episodes on disk: names, what is fetchable, and the index."""
import harness

store = harness.setup()
from lib import downloads

SRC = (harness.ADDON / "lib" / "downloads.py").read_text(encoding="utf-8")
STREAMS = (harness.ADDON / "lib" / "episode_routes.py").read_text(encoding="utf-8")

print("=== only a stream we can fetch ourselves offers Download ===")
for url, want in (
        ("https://dl.example.com/ep.mkv", True),
        ("http://dl.example.com/ep.mkv", True),
        ("plugin://plugin.video.elementum/play?uri=magnet%3A", False),
        ("magnet:?xt=urn:btih:abc", False),
        ("", False)):
    got = downloads.is_downloadable(url)
    print(f"  {(url or '(empty)')[:46]:<48} {'yes' if got else 'no'}")
    assert got is want, url

pick = STREAMS[STREAMS.index("    if downloadable_only:"):]
pick = pick[:pick.index("log(f\"[streams]")]
assert "is_downloadable" in pick, "a magnet could be picked for download"
assert "No stream here can be downloaded" in pick, "an empty picker would just appear"
print("  downloading filters the picker to streams we can fetch  OK")

row = STREAMS[STREAMS.index("        ctx_items.append(("):]
row = row[:row.index("addContextMenuItems")]
assert "download_episode" in row, "Download is not on the episode row"
print("  Download sits on the episode row, beside Mark Watched  OK")

print()
print("=== filenames survive every platform ===")
for raw, want in (
        ("Romance Dawn", "Romance Dawn"),
        ("Luffy: The Beginning", "Luffy The Beginning"),
        ("Who? What/Why", "Who WhatWhy"),
        ("  spaced  ", "spaced"),
        ("...", "Episode"),
        ("", "Episode")):
    got = downloads.safe_name(raw, "Episode")
    print(f"  {raw!r:<26} -> {got!r}")
    assert got == want, raw
assert len(downloads.safe_name("x" * 400)) <= 120, "a 400-char title would break most filesystems"
print("  long titles are trimmed  OK")

print()
print("=== the layout Kodi and a human both read ===")
for params, want_dir, want_name in (
        ({"series_name": "One Pace", "episode_title": "Romance Dawn",
          "season": "1", "episode": "3", "video_url": "https://x/y.mkv"},
         "One Pace/Season 01/", "1x03 - Romance Dawn.mkv"),
        ({"series_name": "One Pace", "episode_title": "A Special",
          "season": "0", "episode": "1", "video_url": "https://x/y.mp4"},
         "One Pace/Season 00/", "0x01 - A Special.mp4"),
        ({"video_url": "https://x/y"},
         "One Pace/Season 00/", "0x00 - Episode.mkv")):
    directory, name = downloads._target(params)
    print(f"  {directory[-22:]:<24} {name}")
    assert directory.endswith(want_dir), directory
    assert name == want_name, name
print("  specials land in Season 00 as 0x01  OK")

print()
print("=== the extension comes from what the provider named ===")
real = "One.Pace.S01E01.Romance.Dawn.1080p.FHD.H265.10bit.AAC.2.0.JPN.ENG.SPA-OnePace.mkv"
for params, want in (
        ({"filename": real, "video_url": "https://x/play/abc123"}, ".mkv"),
        ({"filename": "ep.MP4", "video_url": "https://x/play/abc123"}, ".mp4"),
        ({"video_url": "https://x/y.mkv?token=abc.def"}, ".mkv"),
        ({"video_url": "https://x/play/abc123"}, ".mkv")):
    params.update({"season": "1", "episode": "2", "episode_title": "T"})
    _, name = downloads._target(params)
    src = (params.get("filename") or params["video_url"])[-28:]
    print(f"  ...{src:<30} -> {name}")
    assert name.endswith(want), name
print("  an extensionless debrid link no longer guesses wrong  OK")

print()
print("=== the provider's own byte count is trusted over the header ===")
dl3 = SRC[SRC.index("def download("):SRC.index("def _remove(")]
assert 'params.get("video_size")' in dl3, "a lying Content-Length would truncate the check"
assert '"video_size": params.get("video_size")' in SRC, "nothing to compare on a re-release"
print("  videoSize drives the progress bar and the short-read check  OK")

print()
print("=== a half-written file is never kept ===")
dl = SRC[SRC.index("def download("):SRC.index("def _remove(")]
assert 'xbmcvfs.File(partial, "w")' in dl, "a failed replacement would truncate the good copy"
assert dl.count("xbmcvfs.delete(partial)") == 4, "errors, short reads, a blocked swap and a failed swap"
assert dl.index("xbmcvfs.rename(partial, destination)") < dl.index("_remember(destination"), "partial indexed"

# Deleting the file already there before the swap would mean a rename that
# fails leaves neither the old copy nor the new one.
assert dl.index("xbmcvfs.rename(destination, backup)") < dl.index("xbmcvfs.rename(partial, destination)"),     "the old copy is destroyed before the new one is safely in place"
assert "xbmcvfs.rename(backup, destination)" in dl, "a failed swap would not put the old copy back"
assert dl.index("xbmcvfs.rename(partial, destination)") < dl.index("xbmcvfs.delete(backup)"),     "the backup goes before the swap is known to have worked"
assert dl.index("xbmcvfs.delete(backup)") < dl.index("xbmcvfs.delete(already)"),     "a differently named old cut goes before the new file is in place"
assert "raise_for_status()" in dl, "a 404 body would be written to disk"
assert "abortRequested()" in dl, "closing Kodi would not stop the transfer"
print("  the real file only appears once the transfer finished  OK")

print()
print("=== the same cut never lands twice, but a different cut may ===")
sames = SRC[SRC.index("def _existing("):SRC.index("def _remove_rows(")]
assert 'meta.get("episode_id") == episode_id' in sames, "a different extension would slip past"
assert 'meta.get("variant") or ""' in sames, "Extended would overwrite Standard"
swap = SRC[SRC.index("if already and already != destination:"):]
assert "xbmcvfs.delete(already)" in swap[:200], "the old file would sit beside the new one"

base = {"series_name": "One Pace", "episode_title": "Arlong Park", "season": "6",
        "episode": "5", "filename": "x.mkv", "video_url": "https://x/play/AR_5"}
names = {}
for variant in ("standard", "extended", "", "director"):
    names[variant] = downloads._target(dict(base, variant=variant))[1]
    print(f"  {variant or '(none)':<10} -> {names[variant]}")
assert names["standard"] == names[""], "a missing bingeGroup must not rename old files"
assert names["standard"] != names["extended"], "both cuts would share one filename"
assert names["director"] == "6x05 - Arlong Park (Director).mkv", names["director"]
print("  the cut comes from bingeGroup, and standard keeps the plain name  OK")

print()
print("=== the display name is not what decides the cut ===")
from lib.episode_routes import _binge_part
for group, name_says, want in (
        ("onepace|rd|extended", "Extended", "extended"),
        ("muhnpace|rd|extended", "Fillerver", "extended"),
        ("muhnpace|rd|standard", "-", "standard")):
    got = _binge_part(group, 2)
    print(f"  {group:<22} name says {name_says:<10} -> {got}")
    assert got == want, group
assert 'playback_params["variant"] = _binge_part' in STREAMS, "the cut never reaches download"
print("  Fillerver is filed as Extended, like the provider says  OK")

print()
print("=== moving downloads follows the folder setting ===")
mv = SRC[SRC.index("def move_downloads("):SRC.index("def _sweep(")]
assert "_relocate(path, destination)" in mv, "nothing is actually moved"
assert "xbmcvfs.copy" in SRC[SRC.index("def _relocate("):SRC.index("def move_downloads(")],     "a rename across drives fails, so a copy has to back it up"
assert 'data["files"][destination] = data["files"].pop(path)' in mv, "the index would point at the old path"
assert mv.index("if not planned:") < mv.index("Dialog().yesno"), "it would ask with nothing to do"
assert mv.index("xbmcvfs.exists(destination)") < mv.index("_relocate"), "it would overwrite a file already there"
print("  renames, falls back to copy, and re-keys the index  OK")

import kodistub

store["download_folder"] = "E:/Media"
HOME = downloads.folder()
print(f"  a custom folder: {HOME}")
assert HOME == "E:/Media/One Pace Premium/", HOME

# The default is already a folder of ours, so it gets no folder inside it.
store["download_folder"] = ""
print(f"  the default   : ...{downloads.folder()[-30:]}")
assert not downloads.folder().endswith(downloads.CONTAINER + "/"),     "nesting our folder inside our own folder is just noise"
for setting in ("", "D:" + chr(92) + "Downloads", "E:/Media/", "E:/Media"):
    store["download_folder"] = setting
    got = downloads.folder()
    assert chr(92) not in got, (setting, got)
    assert "//" not in got.replace("://", ""), (setting, got)
    assert got.endswith("/"), (setting, got)
    if setting:
        assert got.endswith(downloads.CONTAINER + "/"), (setting, got)
store["download_folder"] = "E:/Media"

files = {
    "D:/old/One Pace/Season 01/1x01 - Romance Dawn.mkv":
        {"series_name": "One Pace", "season": "1", "episode": "1",
         "episode_title": "Romance Dawn", "episode_id": "RO_1", "variant": "standard"},
    HOME + "One Pace/Season 01/1x02 - Swordsman.mkv":
        {"series_name": "One Pace", "season": "1", "episode": "2",
         "episode_title": "Swordsman", "episode_id": "RO_2", "variant": "standard"},
}
downloads.read_index = lambda: {"files": dict(files), "series": {}}
saved = {}
downloads._write_index = lambda data: saved.update(data)
# Only the sources are on disk; nothing waits at the destinations.
downloads.xbmcvfs.exists = lambda p: p in files or p.endswith("/")
kodistub.Dialog.yesno = lambda self, *a, **k: True
kodistub.recorder.reset()
downloads.move_downloads()
for k in sorted(saved["files"]):
    print(f"  {k}")
assert all(k.startswith(HOME) for k in saved["files"]), saved["files"]
assert len(saved["files"]) == 2, "a row was lost in the move"
assert kodistub.recorder.notifications[-1] == ("Moved 1 file", "INFO"),     "the file already in place should not be moved again"
downloads.xbmcvfs.exists = lambda p: True
store["download_folder"] = ""
print("  only what was outside the folder moves, and the index follows  OK")

print()
print("=== the folders we made go when the last file leaves ===")
store["download_folder"] = "E:/Media"
HOME = downloads.folder()
kodistub.recorder.reset()
downloads._prune(HOME + "One Pace/Season 01/1x01 - A.mkv")
print(f"  removed: {kodistub.recorder.rmdirs}")
assert kodistub.recorder.rmdirs == [HOME + "One Pace/Season 01", HOME.rstrip("/") + "/One Pace"],     kodistub.recorder.rmdirs
kodistub.recorder.reset()
downloads._prune("D:/somewhere/else/file.mkv")
assert kodistub.recorder.rmdirs == [], "a path outside the root must be left alone"
kodistub.recorder.reset()
downloads._prune(HOME + "loose.mkv")
assert kodistub.recorder.rmdirs == [], "the download root itself must never go"

# translatePath hands back backslashes, the rest of the path is built with
# slashes; rmdir must be given one shape or Windows keeps the folder.
store["download_folder"] = "D:" + chr(92) + "Downloads"
built = "".join(downloads._target({"series_name": "One Pace", "episode_title": "A",
                                   "season": "0", "episode": "1", "filename": "x.mkv"}))
print(f"  built: {built}")
assert chr(92) not in built, "a mixed-separator path is not the one on disk"
kodistub.recorder.reset()
downloads._prune(built)
assert kodistub.recorder.rmdirs == ["D:/Downloads/One Pace Premium/One Pace/Season 00",
                                    "D:/Downloads/One Pace Premium/One Pace"],     kodistub.recorder.rmdirs
print(f"  and prunes: {kodistub.recorder.rmdirs[0]}")
pr = SRC[SRC.index("def _prune("):SRC.index("def _remove(")]
assert "for _ in range(2)" in pr, "an unbounded walk can spin on a drive root"
assert 'startswith(root + "/")' in pr, "it could climb above the download folder"
assert "_prune(path)" in SRC[SRC.index("def _remove("):SRC.index("def delete(")], "delete leaves shells"
assert "_prune(path)" in SRC[SRC.index("def move_downloads("):], "moving leaves the old shells"
store["download_folder"] = ""
print("  season then series, never the root, never outside it  OK")

print()
print("=== the default folder is visible before you ever change it ===")
import xml.etree.ElementTree as _ET
_root = _ET.parse(harness.ADDON / "resources" / "settings.xml").getroot()
_folder = next(x for x in _root.iter("setting") if x.get("id") == "download_folder")
_shown = next(x for x in _root.iter("setting") if x.get("id") == "download_folder_display")
print(f"  stored default : {_folder.get('default')}")
print(f"  showing default: {_shown.get('default')}")
assert _folder.get("default", "") == downloads.DEFAULT_FOLDER, "code and settings disagree"
assert _folder.get("visible") == "false", "the raw path should not be an editable row"
assert _shown.get("default") == "Default", "the row would start blank"
_chooser = next(x for x in _root.iter("setting") if x.get("id") == "choose_download_folder")
assert "choose_download_folder" in _chooser.get("action", ""), _chooser.get("action")

tools_src = (harness.ADDON / "lib" / "tools.py").read_text(encoding="utf-8")
chooser = tools_src[tools_src.index("def choose_download_folder("):
                    tools_src.index("def choose_sub_langs(")]
assert '"Custom..."' in chooser and "Default" in chooser, "there is no way back to default"
assert "from ." not in chooser, "RunScript makes this __main__, so relative imports blow up"

# Three copies of one path: the module, the standalone script, and settings.xml.
import re as _re
_tools_default = _re.search(r'DEFAULT_DOWNLOAD_FOLDER = f?"([^"]+)"', tools_src).group(1)
_tools_default = _tools_default.replace("{ADDON_ID}", "plugin.video.onepacepremium")
print(f"  tools.py      : {_tools_default}")
assert _tools_default == downloads.DEFAULT_FOLDER, "the script and the module disagree"
assert "browseSingle" in chooser, "Custom would have nothing to browse with"
assert '"download_folder_display"' in chooser, "the Showing row would go stale"
print("  Default or Custom, and the stored path is never typed by hand  OK")
_front = next(c for c in _root.iter("category") if c.get("label") == "One Pace Premium")
_groups = [x.get("label") for x in _front if x.get("type") == "lsep"]
print(f"  front page groups: {_groups}")
assert _groups == ["About", "Connection"], _groups

print()
print("=== the index never outlives the files ===")
lst = SRC[SRC.index("def _sweep("):SRC.index("def _empty(")]
assert "not xbmcvfs.exists(p)" in lst, "deleting a file by hand would leave a dead row"
assert "_write_index(data)" in lst, "the sweep would not be saved"
assert "_sweep(read_index())" in SRC, "the list never sweeps"
print("  files removed by hand are swept on the next visit  OK")

print()
print("=== an empty section is dressed like the root menu ===")
downloads.read_index = lambda: {"files": {}, "series": {}}
downloads._write_index = lambda data: None
kodistub.recorder.reset()
downloads.list_downloads(None)
_, blank, is_folder = kodistub.recorder.directories[0]
print(f"  {blank.label!r}, content={kodistub.recorder.content!r}")
print(f"  art: {sorted(v.rsplit('/', 1)[-1] for v in blank.art.values())}")
assert kodistub.recorder.content == "", "a content type draws an episode row with no episode"
assert not is_folder, "it must not be enterable"
for slot in ("icon", "thumb", "poster", "banner", "landscape"):
    assert blank.art.get(slot, "").endswith("/info.png"), (slot, blank.art)
assert blank.art.get("fanart", "").endswith("/fanart.png"), blank.art
icon_file = harness.ADDON / "resources" / "skins" / "Default" / "media" / "info.png"
assert icon_file.exists(), "the icon the empty state points at is missing"
print("  same background and icon slots the root menu uses  OK")

print()
print("=== the walk is series, then season, then episode ===")
import kodistub
FAKE = {
    "/d/One Pace/Season 01/1x01 - Romance Dawn.mkv":
        {"series_name": "One Pace", "season": "1", "episode": "1",
         "episode_title": "Romance Dawn", "season_poster": "s1.jpg", "thumb": "ep.jpg",
         "episode_id": "RO_1", "series_id": "pp_onepacee",
         "video": {"id": "RO_1", "overview": "Luffy sets out.", "runtime": "24",
                   "released": "2026-06-28T00:00:00.000Z", "imdbRating": "8.9",
                   "thumbnail": "ep.jpg", "season": 1, "episode": 1},
         "duration": 1077, "video_size": 397698454},
    "/d/One Pace/Season 01/1x02 - The Great Swordsman.mkv":
        {"series_name": "One Pace", "season": "1", "episode": "2",
         "episode_title": "The Great Swordsman", "season_poster": "s1.jpg",
         "episode_id": "RO_2", "series_id": "pp_onepacee"},
    "/d/One Pace/Season 00/0x01 - A Special.mkv":
        {"series_name": "One Pace", "season": "0", "episode": "1",
         "episode_title": "A Special", "season_poster": "s0.jpg",
         "episode_id": "SP_1", "series_id": "pp_onepacee"},
    "/d/Muhn Pace/Season 03/3x01 - Elsewhere.mkv":
        {"series_name": "Muhn Pace", "season": "3", "episode": "1",
         "episode_title": "Elsewhere", "season_poster": "m3.jpg"},
}
SHOW = {"One Pace": {"name": "One Pace", "poster": "op.jpg", "description": "A crew.",
                     "genres": ["Animation"], "status": "Continuing", "ageRating": "TV-14",
                     "cast": [{"name": "Luffy", "role": "Self"}],
                     "seasons": [{"season": 1, "poster": "op-s1.jpg"}]},
        "Muhn Pace": {"name": "Muhn Pace", "poster": "mp.jpg"}}
downloads.read_index = lambda: {"files": dict(FAKE), "series": dict(SHOW)}
downloads._write_index = lambda data: None
downloads.xbmcvfs.exists = lambda p: True


def walk(**params):
    kodistub.recorder.reset()
    downloads.list_downloads(params or None)
    return [(url, item.label, folder)
            for url, item, folder in kodistub.recorder.directories]


rows = walk()
print("  top level:")
for _, label, folder in rows:
    print(f"    {label:<24} {'folder' if folder else 'file'}")
assert [r[1] for r in rows] == ["Muhn Pace", "One Pace"], rows
assert all(r[2] for r in rows), "series rows must be folders"

rows = walk(series="One Pace")
print("  inside One Pace:")
for _, label, folder in rows:
    print(f"    {label:<24} {'folder' if folder else 'file'}")
assert [r[1] for r in rows] == ["Specials", "Season 1"], rows

rows = walk(series="One Pace", season="1")
print("  inside Season 1:")
for url, label, folder in rows:
    print(f"    {label:<24} {'folder' if folder else 'file'}")
    assert not folder, "an episode must not be a folder"
    assert "action=play_video" in url, "playing off disk skips our watched tracking"
    assert "sub_id" not in url, "fetching the subtitle list would stall offline"
assert [r[1] for r in rows] == ["1x01. Romance Dawn", "1x02. The Great Swordsman"], rows

specials = walk(series="One Pace", season="0")
print(f"  a special is labelled {specials[0][1]!r}, with no number")
assert specials[0][1] == "A Special", specials

rows = walk(series="Muhn Pace")
assert [r[1] for r in rows] == ["3x01. Elsewhere"], "one season should not add a level"
print("  a series with one season skips straight to its episodes  OK")

print()
print("=== the counts and artwork the season list already uses ===")
kodistub.recorder.reset()
downloads.list_downloads({"series": "One Pace"})
_, first, _ = kodistub.recorder.directories[0]
print(f"  Specials  ->  {first.properties.get('TotalEpisodes')} episode(s), "
      f"poster={first.art.get('poster')}")
assert first.properties.get("TotalEpisodes") == "1", first.properties
assert "(" not in first.label, "counts belong in properties, not the label"

kodistub.recorder.reset()
downloads.list_downloads({"series": "One Pace", "season": "1"})
_, ep, _ = kodistub.recorder.directories[0]
shown = ep._tag.calls
print(f"  episode tags set offline: {sorted(shown)[:6]}")
assert ep.art.get("poster") == "op-s1.jpg" or ep.art.get("poster"), ep.art
assert "_set_show_tags" in SRC and "_set_season_art" in SRC,     "the download lists should use the same helpers browsing does"
print("  same tag and art helpers as normal browsing  OK")

print()
print("=== a downloaded episode carries what the online one does ===")
kodistub.recorder.reset()
downloads.list_downloads({"series": "One Pace", "season": "1"})
_, first, _ = kodistub.recorder.directories[0]
have = set(first._tag.calls)
print(f"  {len(have)} tags set: {', '.join(sorted(have))[:88]}...")
for needed in ("setPlot", "setYear", "setPremiered", "setFirstAired", "setRating",
               "setCast", "setGenres", "setMpaa", "setTvShowStatus", "setDuration"):
    assert needed in have, f"{needed} never runs, so the skin shows a blank there"
assert first.art.get("thumb") == "ep.jpg", first.art
shown = first._tag.calls["setDuration"]
shown = shown[0] if isinstance(shown, (list, tuple)) else shown
print(f"  runtime: {shown}s from the stream, not 1440s from the series")
assert shown == 1077, "the round series runtime would be shown instead"
print("  plot, date, rating, cast, runtime and artwork all present  OK")

print()
print("=== every row can be played and removed ===")
labels = [c[0] for c in first.context]
print(f"  episode:  {labels}")
assert labels[0] == "[B]Mark Watched[/B]", labels
assert labels[-1] == "[B]Delete[/B]", labels
assert "[B]Browse Folder[/B]" in labels, "no way to see the file itself"
browse = next(c for c in first.context if "Browse" in c[0])[1]
assert "action=browse_download" in browse, browse

# Kodi has no builtin for the desktop's file manager, so each platform gets
# its own command and anything else falls back to Kodi's own browser.
import xbmc as _xbmc
_was = _xbmc.getCondVisibility
downloads.xbmcvfs.exists = lambda p: True
for platform, expected in (
        ("System.Platform.Windows", "System.Exec(explorer.exe"),
        ("System.Platform.OSX", "System.Exec(open"),
        ("System.Platform.Linux", "System.Exec(xdg-open"),
        (None, "ActivateWindow(Videos,")):
    _xbmc.getCondVisibility = lambda c, want=platform: c == want
    kodistub.recorder.reset()
    downloads.browse({"path": "D:/Downloads/One Pace Premium/One Pace/Season 01/1x01 - A.mkv"})
    ran = kodistub.recorder.builtins[0]
    print(f"  {platform or 'a TV box with none':<26} -> {ran[:62]}")
    assert ran.startswith(expected), ran
    assert "1x01 - A.mkv" not in ran, "that opens the file, not the folder it is in"
_xbmc.getCondVisibility = lambda c: c == "System.Platform.Windows"
kodistub.recorder.reset()
downloads.browse({"path": "D:/Downloads/One Pace/Season 01/1x01 - A.mkv"})
assert chr(92) in kodistub.recorder.builtins[0], "explorer wants backslashes"
kodistub.recorder.reset()
downloads.xbmcvfs.exists = lambda p: False
downloads.browse({"path": "D:/gone/1x01 - A.mkv"})
assert kodistub.recorder.builtins == [], "it would open a folder that is not there"
downloads.xbmcvfs.exists = lambda p: True
_xbmc.getCondVisibility = _was

kodistub.recorder.reset()
downloads.list_downloads({"series": "One Pace"})
_, season_row, _ = kodistub.recorder.directories[0]
print(f"  season :  {[c[0] for c in season_row.context]}")
assert "Delete Specials" in season_row.context[0][0], season_row.context

kodistub.recorder.reset()
downloads.list_downloads(None)
_, series_row, _ = kodistub.recorder.directories[0]
print(f"  series :  {[c[0] for c in series_row.context]}")
assert "Delete Series" in series_row.context[0][0], series_row.context

taken = []
downloads._remove = lambda paths: taken.extend(paths) or len(paths)
kodistub.Dialog.yesno = lambda self, *a, **k: True

for scope, expect in (
        ({"path": "/d/One Pace/Season 01/1x01 - Romance Dawn.mkv"}, 1),
        ({"series": "One Pace", "season": "1"}, 2),
        ({"series": "One Pace"}, 3),
        ({"series": "Muhn Pace"}, 1)):
    taken.clear()
    downloads.delete(scope)
    print(f"  {str(scope)[:44]:<46} -> {len(taken)} file(s)")
    assert len(taken) == expect, (scope, taken)
print("  one episode, one season, or a whole series  OK")

print()
print("=== a downloaded episode is the same episode everywhere ===")
kodistub.recorder.reset()
downloads.list_downloads({"series": "One Pace", "season": "1"})
_, row, _ = kodistub.recorder.directories[0]
assert row.properties.get("Downloaded") == "true", row.properties
menu = [c[0] for c in row.context]
assert any("Mark" in m for m in menu), menu
print(f"  downloads row: Downloaded property + {menu}")

listing = STREAMS[STREAMS.index("        label = _download_mark("):]
listing = listing[:listing.index("_set_episode_art")]
assert "on_disk" in listing, "the episode list never checks what is on disk"
assert 'setProperty("Downloaded", "true")' in listing, "skins get nothing to key off"
lists_src = (harness.ADDON / "lib" / "my_lists.py").read_text(encoding="utf-8")
assert "_download_mark(display_label" in lists_src, "In Progress and Next Episodes are unmarked"
print("  episode list, In Progress and Next Episodes all use the same mark  OK")

print()
print("=== the mark is one green arrow in front of the title ===")
store["download_marker"] = "true"
store["downloads_enabled"] = "true"
marked = downloads.mark("1x01. Romance Dawn", "RO_1", {"RO_1"})
print("  " + marked.encode("unicode_escape").decode())
assert marked.startswith("[COLOR FF2ECC71][B]"), "a list renders the bold fine"
assert marked.endswith("1x01. Romance Dawn"), "a list view clips the tail, not the head"
assert downloads.mark("x", "RO_9", {"RO_1"}) == "x", "an undownloaded row must stay plain"
store["download_marker"] = "false"
assert downloads.mark("x", "RO_1", {"RO_1"}) == "x", "the marker setting does nothing"
store["download_marker"] = "true"

print()
print("=== turning downloads off hides the whole feature ===")
store["downloads_enabled"] = "false"
assert downloads.downloaded_ids() == set(), "rows would still be marked"
assert downloads.mark("x", "RO_1", {"RO_1"}) == "x", "the mark would still show"
assert downloads.path_for("RO_1") == "", "the local copy would still be preferred"
root = (harness.ADDON / "lib" / "catalog_routes.py").read_text(encoding="utf-8")
assert "if _downloads_enabled():" in root, "the menu entry would stay"
menu = STREAMS[STREAMS.index("        if downloads_on:"):]
assert "Download" in menu[:200], "the Download item would stay on the episode row"
store["downloads_enabled"] = "true"
print("  menu entry, Download item, marks and preference all gone  OK")

print()
print("=== an episode on disk plays from disk, without asking a provider ===")
resume = STREAMS[STREAMS.index("def check_resume(params):"):]
resume = resume[:resume.index("_play_video(chosen)")]
assert "local_playback" in resume, "it would fetch streams for a file we already hold"
assert resume.index("local_playback") < resume.index("_choose_stream"), "asked first"
assert 'get_setting("prefer_downloads")' in SRC, "the preference cannot be turned off"
print("  local copy is checked before any stream request  OK")

print()
print("=== with the preference off, the copy is offered rather than ignored ===")
pickr = STREAMS[STREAMS.index("    choices = _preferred_streams(binge_groups)"):]
pickr = pickr[:pickr.index("    if downloadable_only:")]
assert "local_options" in pickr, "the download would be invisible in the picker"
assert "valid_streams.insert(offset, fields)" in pickr, "the cuts would not come first"
assert "[i + shift for i in choices]" in pickr, "the other picks would shift onto the wrong stream"
assert "if not downloadable_only:" in pickr, "downloading would offer the file to itself"

downloads.read_index = lambda: {"files": {
    "/d/6x05 - Arlong Park.mkv": {"episode_id": "AR_5", "video_size": 763871543,
                                  "variant": "standard", "series_id": "pp"},
    "/d/6x05 - Arlong Park (Extended).mkv": {"episode_id": "AR_5", "video_size": 783660152,
                                             "variant": "extended", "series_id": "pp"},
}, "series": {}}
held = downloads.local_options("AR_5")
for label, fields in held:
    print("  " + label.encode("unicode_escape").decode())
assert len(held) == 2, "holding both cuts must offer both"
assert "Standard" in held[0][0] and "763.87 MB" in held[0][0], held[0][0]
assert "Extended" in held[1][0] and "783.66 MB" in held[1][0], held[1][0]
assert held[0][0].startswith("[COLOR FF2ECC71][↓]"), "the dialog would show the [/B]"
assert "[/B]" not in held[0][0], "the select dialog shows the closing tag"
assert downloads.local_options("NOPE") == [], "an undownloaded episode must offer nothing"
store["downloads_enabled"] = "false"
assert downloads.local_options("AR_5") == [], "the feature is off, so the picks must go"
store["downloads_enabled"] = "true"
print("  both cuts offered with their sizes, and gone when downloads are off  OK")

print()
print("=== holding both cuts, the version preference decides which plays ===")
store["prefer_downloads"] = "true"
for setting, want in (("2", "extended"), ("1", "standard"), ("", "standard")):
    store["preferred_version"] = setting
    chosen = downloads.local_playback("AR_5")
    got = "extended" if "Extended" in chosen["video_url"] else "standard"
    print(f"  preferred_version={setting or '(unset)'}  ->  {got}")
    assert got == want, (setting, chosen)
store["preferred_version"] = ""
print("  the same setting the stream picker already uses  OK")

print()
print("=== standard leads, whatever order the index happens to be in ===")
downloads.read_index = lambda: {"files": {
    "/d/x/6x05 - Arlong Park (Extended).mkv": {
        "series_name": "One Pace", "season": "6", "episode": "5", "series_id": "pp",
        "episode_title": "Arlong Park", "episode_id": "AR_5", "variant": "extended"},
    "/d/x/6x05 - Arlong Park.mkv": {
        "series_name": "One Pace", "season": "6", "episode": "5", "series_id": "pp",
        "episode_title": "Arlong Park", "episode_id": "AR_5", "variant": "standard"},
    "/d/x/6x06 - Next.mkv": {
        "series_name": "One Pace", "season": "6", "episode": "6", "series_id": "pp",
        "episode_title": "Next", "episode_id": "AR_6", "variant": "standard"},
}, "series": {"One Pace": {"name": "One Pace"}}}
kodistub.recorder.reset()
downloads.list_downloads({"series": "One Pace", "season": "6"})
rows = [i.label for _, i, _ in kodistub.recorder.directories]
for r in rows:
    print(f"  {r}")
assert rows == ["6x05. Arlong Park", "6x05. Arlong Park  (Extended)", "6x06. Next"], rows
assert [l.split("|")[1].strip() for l, _ in downloads.local_options("AR_5")] ==     ["Standard", "Extended"], "extended sorts before standard alphabetically"
print("  both cuts sit together, standard above  OK")

play = SRC[SRC.index("def play_url("):SRC.index("def downloaded_ids(")]
assert '"video_url": path' in play, "the file would play outside the add-on"

# play_video builds its plot from these; with neither it never calls setPlot
# and the info panel reads "Not available".
fields = downloads._play_fields("C:/dl/x.mkv", {
    "series_id": "pp", "episode_id": "AR_5", "season": "6", "episode": "5",
    "variant": "extended", "series_name": "One Pace", "episode_title": "Arlong Park",
    "video_size": 783660152, "video": {"overview": "Nami goes home."}})
print(f"  stream_name : {fields['stream_name']}")
print(f"  episode_plot: {fields['episode_plot']}")
assert fields["episode_plot"] == "Nami goes home.", fields
assert fields["stream_name"] == "Downloaded  |  Extended  |  783.66 MB", fields

# Resolution, chapters, runtime, bitrate and the source hash are composed by
# the provider and cannot be rebuilt offline, so they are kept at download.
DESC = ("Romance Dawn 01 | 1080p" + chr(10) + "Ch. [1] | Dur: 17:57" + chr(10)
        + "Size: 397.70 MB" + chr(10) + "[One Pace] [E5F09F49]")
kept = downloads._play_fields("C:/dl/x.mkv", {
    "episode_id": "RO_1", "variant": "standard", "video_size": 397698454,
    "stream_desc": DESC, "video": {"overview": "Luffy sets out."}})
print("  the player shows:")
print("    [B]" + kept["stream_name"] + "[/B]")
for _line in kept["stream_desc"].split(chr(10)):
    print("    " + _line)
print("    " + kept["episode_plot"])
assert kept["stream_desc"] == DESC, kept
assert '"stream_desc": params.get("stream_desc"' in SRC, "the description is never stored"
assert "stream_desc" not in downloads._play_fields("C:/dl/x.mkv", {"episode_id": "X"}),     "an older download with none stored would send a blank line"
plot_src = STREAMS if False else (harness.ADDON / "lib" / "playback.py").read_text(encoding="utf-8")
assert 'params.get("episode_plot"' in plot_src, "playback stopped reading the plot"
bare = downloads._play_fields("C:/dl/x.mkv", {"episode_id": "X"})
assert "episode_plot" not in bare, "an empty plot would blank the panel instead"
assert "sub_id" not in play.split('"""')[2], "an offline fetch would stall playback"
print("  playing a download runs through play_video, so watched state follows  OK")

print()
print("=== Kodi keeps Play above our Delete ===")
assert "replaceItems=True" not in SRC, "replacing the menu buries Play under Delete"
print("  the Delete item is appended, not a replacement  OK")

print()
print("=== finishing a download redraws the list ===")
dl2 = SRC[SRC.index("def download("):SRC.index("def delete(")]
assert "refresh_container()" in dl2, "the list stays stale until you leave and return"
print("  refresh_container runs once the file is saved  OK")

print()
print("=== the route is reachable and lazy ===")
from lib import router
for action in ("list_downloads", "delete_download", "download_episode"):
    target = router._ACTIONS[action]
    router._resolve(target)
    print(f"  {action:16} -> {target}")

root = (harness.ADDON / "lib" / "catalog_routes.py").read_text(encoding="utf-8")
assert "downloads.png" in root and "list_downloads" in root, "not on the root menu"
media = harness.ADDON / "resources" / "skins" / "Default" / "media" / "downloads.png"
assert media.exists(), "the menu icon is missing"
print("  on the root menu, with its icon present  OK")

print()
print("=== every notification decides about its own sound ===")
import re
q = chr(34)
silent, loud, unset = [], [], []
for f in sorted((harness.ADDON / "lib").glob("*.py")):
    src = f.read_text(encoding="utf-8")
    for m in re.finditer(r"\.notification\(", src):
        depth, i = 0, m.end() - 1
        while i < len(src):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        call = " ".join(src[m.end():i].split())
        label = f"{f.name}: " + (call.split(q)[3] if call.count(q) > 3 else call)[:44]
        if call.rstrip(",").endswith("True"):
            loud.append(label)
        elif call.rstrip(",").endswith("False"):
            silent.append(label)
        else:
            unset.append(label)

print("  with sound:")
for x in loud:
    print(f"    {x}")
print("  silent:")
for x in silent:
    print(f"    {x}")
assert not unset, f"the sound flag defaults to on, so it must be explicit: {unset}"

print()
print("all assertions passed")
