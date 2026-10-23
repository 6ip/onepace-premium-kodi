"""Adopting episodes the viewer already has, wherever they keep them.

Nothing is copied: an import writes index rows that point at their own files,
where those files already are. Nothing of theirs is moved or deleted either —
that is what the imported flag in downloads.py protects.

The folders are not read for meaning. People keep a hard disk of One Pace in
one flat folder as often as in neat seasons, so only the filenames matter, and
what identifies them is the release list rather than their shape.
"""
import os

import xbmcgui
import xbmcvfs

from . import cache as _cache
from . import download_queue, import_index, import_match
from .downloads import (_sidecars, _size_on_disk, _write_index, enabled,
                        folder, read_index, remember_into, write_report)
from .route_common import _notify_error, _notify_info
from .utils import log, refresh_container

# The release list changes only when a release does, and an import is a rare
# thing to do twice in a day.
_INDEX_KEY = "import_releases"
_INDEX_TTL = 86400


def _releases():
    """The release index and the arc map, fetched once and kept for a day."""
    held = _cache.get(_INDEX_KEY)
    if held:
        return import_index.build(held["entries"]), held["config"]
    entries, config = import_index.fetch()
    _cache.set(_INDEX_KEY, {"entries": entries, "config": config}, _INDEX_TTL)
    return import_index.build(entries), config


def _walk(root, progress):
    """Every video file under a folder, however the folders are arranged."""
    found, stack, looked = [], [root], 0
    while stack:
        if progress.iscanceled():
            return found, True
        here = stack.pop()
        try:
            dirs, names = xbmcvfs.listdir(here)
        except Exception as exc:
            log(f"[import] could not read {here!r}: {exc}")
            continue
        for name in dirs:
            # Our own subtitle folders start with a dot, and so does most of
            # what an operating system would rather we left alone.
            if not name.startswith("."):
                stack.append(f"{here}{name}/")
        for name in names:
            if os.path.splitext(name)[1].lower() in import_match.VIDEO_EXTS:
                found.append(here + name)
        looked += 1
        # The modal dialog takes a percentage and one line, with no heading.
        progress.update(min(40, looked),
                        f"Looking through {looked} folder(s) — "
                        f"{len(found)} episode(s) so far")
    return found, False


def _catalogues():
    """Every series we serve, by the stream id each of its episodes carries.

    The provider prefixes some ids with pp_ and leaves others bare, so both
    spellings are indexed and a release id finds its episode either way.
    """
    from .downloads import _series_ids
    from .provider_api import _fetch_provider_meta

    found, metas = {}, {}
    for name, series_id in _series_ids().items():
        meta = _fetch_provider_meta("series", series_id)
        if not meta:
            continue
        metas[name] = meta
        for video in meta.get("videos", ()):
            video_id = str(video.get("id") or "")
            if not video_id:
                continue
            found.setdefault(video_id, (name, video))
            if video_id.startswith("pp_"):
                found.setdefault(video_id[3:], (name, video))
    return found, metas


def _row(path, answer, series_name, video, meta):
    """What _remember wants, from the provider rather than from the file."""
    season = video.get("season")
    season_poster = next(
        (s["poster"] for s in meta.get("seasons", ())
         if s.get("season") == season and s.get("poster")), "")
    return {
        "series_name": series_name,
        "series_id": meta.get("id", ""),
        "season": str(season if season is not None else 0),
        "episode": str(video.get("episode") or video.get("number") or 0),
        "episode_id": str(video.get("id") or ""),
        "episode_title": video.get("name") or video.get("title") or "",
        "thumb": video.get("thumbnail", ""),
        "season_poster": season_poster,
        "logo": meta.get("logo", ""),
        "video_size": _size_on_disk(path),
        "variant": answer["variant"],
        "imported": True,
    }


def run(_params=None):
    """Point the add-on at a folder of episodes and adopt what it recognises."""
    if not enabled():
        _notify_error("Turn on Downloads first")
        return
    # A transfer in flight writes to a .part this would otherwise adopt.
    if download_queue.busy():
        xbmcgui.Dialog().ok("Import", "Wait for the downloads to finish first")
        return

    root = xbmcgui.Dialog().browseSingle(
        0, "Choose a folder of episodes", "files", "", False, False, "")
    if not root:
        return
    root = root if root.endswith(("/", "\\")) else root + "/"
    if folder().rstrip("/") in root.replace("\\", "/").rstrip("/"):
        xbmcgui.Dialog().ok("Import", "That is the add-on's own download "
                                      "folder. Use Rescan for those.")
        return

    progress = xbmcgui.DialogProgress()
    progress.create("Import", "Looking for episodes")
    try:
        files, stopped = _walk(root.replace("\\", "/"), progress)
        if stopped:
            return
        if not files:
            progress.close()
            xbmcgui.Dialog().ok("Import", "No video files in that folder")
            return

        progress.update(45, "Reading the release list")
        try:
            index, config = _releases()
        except Exception as exc:
            progress.close()
            log(f"[import] could not read the release list: {exc}")
            xbmcgui.Dialog().ok("Import", "Could not read the release list")
            return
        arc_map = config.get("ARC_MAP", {})
        aliases = config.get("ALIASES", {})

        progress.update(55, "Looking episodes up")
        by_stream_id, metas = _catalogues()
        if not by_stream_id:
            progress.close()
            xbmcgui.Dialog().ok("Import", "Could not read the catalogue")
            return

        known = set(read_index()["files"])
        sure, guessed, unknown, already = [], [], [], 0
        for number, path in enumerate(files, 1):
            if progress.iscanceled():
                return
            if number % 10 == 0 or number == len(files):
                progress.update(
                    55 + int(number * 40 / len(files)),
                    f"Looking up {number} of {len(files)} — "
                    f"{os.path.basename(path)}")
            if path in known:
                already += 1
                continue
            answer = import_index.resolve(path, _size_on_disk(path), index,
                                          arc_map, aliases)
            found = by_stream_id.get(answer["episode_id"] or "")
            if not found:
                unknown.append((path, answer))
                continue
            (guessed if answer["how"] == "guess" else sure).append(
                (path, answer, found))
    finally:
        progress.close()

    _offer(root, sure, guessed, unknown, already, metas)


def _offer(root, sure, guessed, unknown, already, metas):
    """Say what was found, and write nothing until it is asked for."""
    # Written whatever happens next: the names it could not read are only
    # listed here, and somebody who cancels to go and look for them should
    # find them rather than nothing.
    _report(root, sure, guessed, unknown, already)

    lines = [f"{len(sure)} episode(s) identified"]
    if guessed:
        lines.append(f"{len(guessed)} matched by name only")
    if unknown:
        lines.append(f"{len(unknown)} not recognised")
    if already:
        lines.append(f"{already} already in the list")
    if not sure and not guessed:
        xbmcgui.Dialog().ok("Import", os.linesep.join(
            ["Nothing here could be identified."] + lines[1:]))
        return

    question = os.linesep.join(
        lines + ["", "Add them to Downloads?",
                 "Your files stay where they are."])
    if not xbmcgui.Dialog().yesno("Import", question,
                                  nolabel="Cancel", yeslabel="Add"):
        return

    data = read_index()
    for path, answer, (series_name, video) in sure + guessed:
        remember_into(data, path, _row(path, answer, series_name, video,
                                       metas.get(series_name) or {}),
                      metas.get(series_name), _sidecars(path))
    _write_index(data)
    added = len(sure) + len(guessed)
    log(f"[import] adopted {added} file(s) from {root!r}")
    _notify_info(f"Imported {added} episode{'s' if added != 1 else ''}")
    refresh_container()


def _report(root, sure, guessed, unknown, already):
    """The unrecognised names are the part worth keeping to look at."""
    lines = [f"From {root}",
             f"{len(sure)} identified from the release list",
             f"{len(guessed)} matched by name only",
             f"{already} already in the list"]
    if unknown:
        lines.append(f"{len(unknown)} not recognised:")
        lines.extend(f"  {os.path.basename(p)}" for p, _ in unknown[:40])
        if len(unknown) > 40:
            lines.append(f"  ... and {len(unknown) - 40} more")
    write_report("Import", lines)
