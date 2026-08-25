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
assert dl.count("xbmcvfs.delete(partial)") == 3, "errors, short reads and a failed swap"
assert dl.index("xbmcvfs.rename(partial, destination)") < dl.index("_remember(destination"), "partial indexed"
assert "raise_for_status()" in dl, "a 404 body would be written to disk"
assert "abortRequested()" in dl, "closing Kodi would not stop the transfer"
print("  the real file only appears once the transfer finished  OK")

print()
print("=== the same episode never lands twice ===")
sames = SRC[SRC.index("def _existing("):SRC.index("def _remove_rows(")]
assert 'meta.get("episode_id") == episode_id' in sames, "a different extension would slip past"
swap = SRC[SRC.index("if already and already != destination:"):]
assert "xbmcvfs.delete(already)" in swap[:200], "the old file would sit beside the new one"
for kind, ext in (("1080p mkv", ".mkv"), ("720p mp4", ".mp4")):
    p2 = {"series_name": "One Pace", "episode_title": "Romance Dawn", "season": "1",
          "episode": "1", "filename": "x" + ext, "video_url": "https://x/play/RO_1"}
    print(f"  {kind:<10} -> {downloads._target(p2)[1]}")
print("  matched on episode id, so replacing swaps rather than duplicates  OK")

print()
print("=== the index never outlives the files ===")
lst = SRC[SRC.index("def _sweep("):SRC.index("def _empty(")]
assert "not xbmcvfs.exists(p)" in lst, "deleting a file by hand would leave a dead row"
assert "_write_index(data)" in lst, "the sweep would not be saved"
assert "_sweep(read_index())" in SRC, "the list never sweeps"
print("  files removed by hand are swept on the next visit  OK")

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
assert "local_option" in pickr, "the download would be invisible in the picker"
assert "valid_streams.insert(0, fields)" in pickr, "it would not be first"
assert "[i + 1 for i in choices]" in pickr, "the other picks would shift onto the wrong stream"
assert "if not downloadable_only:" in pickr, "downloading would offer the file to itself"

downloads.read_index = lambda: {"files": {"/d/a.mkv": {
    "episode_id": "RO_1", "video_size": 397698454, "series_id": "pp",
    "season": "1", "episode": "1"}}, "series": {}}
label, fields = downloads.local_option("RO_1")
print("  " + label.encode("unicode_escape").decode())
assert label.startswith("[COLOR FF2ECC71][↓]"), "the dialog would show the [/B]"
assert "397.70 MB" in label, "MiB was shown where the provider says MB"
assert "[/B]" not in label, "the select dialog shows the closing tag"
assert fields["video_url"] == "/d/a.mkv", fields
assert downloads.local_option("NOPE") is None, "an undownloaded episode must offer nothing"
store["downloads_enabled"] = "false"
assert downloads.local_option("RO_1") is None, "the feature is off, so the pick must go"
store["downloads_enabled"] = "true"
print("  shown with its size, and gone when downloads are disabled  OK")

play = SRC[SRC.index("def play_url("):SRC.index("def downloaded_ids(")]
assert '"video_url": path' in play, "the file would play outside the add-on"
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
