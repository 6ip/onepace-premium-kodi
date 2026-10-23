"""Adopting episodes the viewer already has, wherever they keep them."""
import os

import xbmcgui
import xbmcvfs

from . import cache as _cache
from . import download_queue, import_index, import_match
from .downloads import (_sidecars, _size_on_disk, _write_index, enabled,
                        folder, read_index, remember_into, write_report)
from .route_common import _notify_error, _notify_info
from .utils import log, refresh_container

# The list changes only when a release does.
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


def _ours():
    """Our own download folders, current and former, to be walked around."""
    from .downloads import _roots
    return tuple(r.replace(chr(92), "/") for r in _roots())


def _walk(root, progress, ours=()):
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
            # A leading dot is ours, or something the system wants left alone.
            if name.startswith("."):
                continue
            where = f"{here}{name}/"
            # Choosing a parent of our own folder is a fair thing to do, so
            # step around it rather than adopting what we fetched ourselves.
            if any(where.startswith(o) for o in ours):
                log(f"[import] stepping around our own {where!r}")
                continue
            stack.append(where)
        for name in names:
            if os.path.splitext(name)[1].lower() in import_match.VIDEO_EXTS:
                found.append(here + name)
        looked += 1
        # The modal dialog takes a percentage and one line, with no heading.
        progress.update(min(40, looked),
                        f"Looking through {looked} folder(s) — "
                        f"{len(found)} episode(s) so far")
    return found, False


def _flat(text):
    """A title with the punctuation and case taken out of the comparison."""
    return "".join(c for c in (text or "").lower() if c.isalnum())


def _cut_titles(metas):
    """The Kuma catalogue by title."""
    series = import_match.SERIES_NAMES[import_match.KUMA]
    found = {}
    for video in (metas.get(series) or {}).get("videos", ()):
        title = _flat(video.get("name") or video.get("title") or "")
        if title:
            found.setdefault(title, (series, video))
    return found


def _by_cut_title(parsed, titles):
    """The episode a Kuma filename names, if the catalogue has it."""
    wanted = _flat(import_match.kuma_title(parsed))
    return titles.get(wanted) if wanted else None


def _catalogues():
    """Every series we serve, by the stream id each of its episodes carries."""
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
        files, stopped = _walk(root.replace(chr(92), "/"), progress, _ours())
        if stopped:
            return
        if not files:
            xbmcgui.Dialog().ok("Import", "No video files in that folder")
            return

        progress.update(45, "Reading the release list")
        try:
            index, config = _releases()
        except Exception as exc:
            log(f"[import] could not read the release list: {exc}")
            xbmcgui.Dialog().ok("Import", "Could not read the release list")
            return
        arc_map = config.get("ARC_MAP", {})
        aliases = config.get("ALIASES", {})

        progress.update(55, "Looking episodes up")
        by_stream_id, metas = _catalogues()
        if not by_stream_id:
            xbmcgui.Dialog().ok("Import", "Could not read the catalogue")
            return
        cut_titles = _cut_titles(metas)

        from .downloads import cut_of
        rows = read_index()["files"]
        known = set(rows)
        # An episode we hold already, as some other file.
        taken = {(r.get("episode_id"), cut_of(r.get("variant"))) for r in rows.values()}
        sure, guessed, clash, unknown, already = [], [], [], [], 0
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
                found = _by_cut_title(answer["parsed"], cut_titles)
                if found:
                    answer["episode_id"] = str(found[1].get("id") or "")
                    answer["how"] = "title"
            if not found:
                unknown.append((path, answer, None))
                continue
            seat = (answer["episode_id"], cut_of(answer["variant"]))
            if seat in taken:
                clash.append((path, answer, found))
                continue
            taken.add(seat)
            (guessed if answer["how"] == "guess" else sure).append(
                (path, answer, found))
    finally:
        progress.close()

    _offer(root, sure, guessed, clash, unknown, already, metas)


def _ask_episode(metas):
    """Series, then season, then episode. None if they back out of any of it."""
    names = sorted(metas)
    at = xbmcgui.Dialog().select("Which series?", names)
    if at < 0:
        return None
    series = names[at]
    videos = [v for v in (metas[series] or {}).get("videos", ()) if v.get("id")]
    if not videos:
        return None

    seasons = sorted({int(v.get("season") or 0) for v in videos})
    labels = ["Specials" if s == 0 else f"Season {s}" for s in seasons]
    at = xbmcgui.Dialog().select(f"{series} — which season?", labels)
    if at < 0:
        return None
    season = seasons[at]

    # One arc numbers an episode 3.5, so the order is read as a number.
    here = sorted((v for v in videos if int(v.get("season") or 0) == season),
                  key=lambda v: float(v.get("episode") or 0))
    labels = [f"{v.get('episode')}.  {v.get('name') or v.get('title') or ''}"
              for v in here]
    at = xbmcgui.Dialog().select(f"{series} — which episode?", labels)
    if at < 0:
        return None
    return series, here[at]


def _held_rows(episode_id, variant):
    """Rows already pointing at this exact episode and cut."""
    from .downloads import cut_of
    wanted = cut_of(variant)
    return [path for path, row in read_index()["files"].items()
            if row.get("episode_id") == episode_id
            and cut_of(row.get("variant")) == wanted]


def _by_hand(items, metas):
    """Let them settle the files the add-on would not decide alone."""
    picked, replace, left = [], [], list(items)
    while left:
        at = xbmcgui.Dialog().select(
            f"{len(left)} to settle — pick one",
            [os.path.basename(path) for path, _, _ in left])
        if at < 0:
            break
        path, answer, found = left[at]
        chosen = found if found else _ask_episode(metas)
        if chosen is None:
            continue
        series, video = chosen
        episode_id = str(video.get("id") or "")
        answer = dict(answer, episode_id=episode_id,
                      how=answer["how"] if found else "hand")

        from .downloads import cut_of
        seat = (episode_id, cut_of(answer["variant"]))
        held = _held_rows(episode_id, answer["variant"])
        # Settled onto earlier in this same pass, which the stored index
        # cannot know about yet.
        earlier = [i for i, (_, a, _) in enumerate(picked)
                   if (a["episode_id"], cut_of(a["variant"])) == seat]
        if held or earlier:
            what = xbmcgui.Dialog().select(
                f"{video.get('name') or video.get('title') or 'That episode'} "
                f"is already listed",
                ["Add this one as well", "Use this one instead",
                 "Leave this file out"])
            if what < 0 or what == 2:
                left.pop(at)
                continue
            if what == 1:
                replace.extend(held)
                for i in reversed(earlier):
                    picked.pop(i)

        picked.append((path, answer, (series, video)))
        left.pop(at)
    return picked, replace


def _offer(root, sure, guessed, clash, unknown, already, metas):
    """Say what was found, and write nothing until it is asked for."""
    by_hand, replace = [], []
    try:
        _add(root, sure, guessed, clash, unknown, already, metas,
             by_hand, replace)
    finally:
        # Written last, so the count is the one that ended up true.
        _report(root, sure, guessed, clash, unknown, already, by_hand)


def _add(root, sure, guessed, clash, unknown, already, metas, by_hand, replace):
    """The dialogs, and the one write at the end of them."""
    lines = [f"{len(sure)} episode(s) identified"]
    if guessed:
        lines.append(f"{len(guessed)} matched by name only")
    if clash:
        lines.append(f"{len(clash)} you already have as another file")
    if unknown:
        lines.append(f"{len(unknown)} not recognised")
    if already:
        lines.append(f"{already} already in the list")
    question = os.linesep.join(
        lines + ["", "Add them to Downloads?",
                 "Your files stay where they are."])
    settle = clash + unknown
    if settle:
        # Only the viewer can settle these; adding them unasked duplicates.
        choice = xbmcgui.Dialog().yesnocustom(
            "Import", question, f"Review {len(settle)}", "Cancel", "Add")
        if choice == 2:
            picked, instead = _by_hand(settle, metas)
            by_hand.extend(picked)
            replace.extend(instead)
        elif choice != 1:
            return
    elif not sure and not guessed:
        xbmcgui.Dialog().ok("Import", "Nothing here could be identified.")
        return
    elif not xbmcgui.Dialog().yesno("Import", question,
                                    nolabel="Cancel", yeslabel="Add"):
        return

    if not sure and not guessed and not by_hand:
        return

    data = read_index()
    for path in replace:
        data["files"].pop(path, None)
    for path, answer, (series_name, video) in sure + guessed + by_hand:
        remember_into(data, path, _row(path, answer, series_name, video,
                                       metas.get(series_name) or {}),
                      metas.get(series_name), _sidecars(path))
    _write_index(data)
    added = len(sure) + len(guessed) + len(by_hand)
    log(f"[import] adopted {added} file(s) from {root!r}"
        + (f", {len(by_hand)} chosen by hand" if by_hand else ""))
    _notify_info(f"Imported {added} episode{'s' if added != 1 else ''}")
    refresh_container()


def _report(root, sure, guessed, clash, unknown, already, by_hand=()):
    """The names it could not settle are the part worth keeping to look at."""
    settled = {path for path, _, _ in by_hand}
    left = [u for u in unknown if u[0] not in settled]
    skipped = [c for c in clash if c[0] not in settled]
    lines = [f"From {root}",
             f"{len(sure)} identified from the release list",
             f"{len(guessed)} matched by name only",
             f"{already} already in the list"]
    if settled:
        lines.append(f"{len(settled)} settled by hand")
    if skipped:
        lines.append(f"{len(skipped)} left out, already held as another file:")
        lines.extend(f"  {os.path.basename(p)}" for p, _, _ in skipped[:20])
    if left:
        lines.append(f"{len(left)} not recognised:")
        lines.extend(f"  {os.path.basename(p)}" for p, _, _ in left[:40])
        if len(left) > 40:
            lines.append(f"  ... and {len(left) - 40} more")
    write_report("Import", lines)
