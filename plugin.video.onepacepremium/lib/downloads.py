"""Keep episodes on disk, and list what is already there."""
import json
import os
import re

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from .route_common import _add_directory_items, _notify_error, _notify_info, end_directory
from .utils import (ADDON_HANDLE, ADDON_ID, build_url, get_setting, log,
                    refresh_container, session)

# Only ever asked for the profile path, which never changes between calls.
_ADDON = xbmcaddon.Addon()

_INDEX = "downloads.json"
_CHUNK = 1024 * 256
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Anything else is a stream we cannot fetch ourselves, like an Elementum magnet.
_DOWNLOADABLE = ("http://", "https://")


def _profile():
    p = xbmcvfs.translatePath(_ADDON.getAddonInfo("profile"))
    return p if p.endswith(("/", "\\")) else p + "/"


DEFAULT_FOLDER = f"special://profile/addon_data/{ADDON_ID}/downloads/"

# A folder of our own inside whatever the viewer picked, so pointing this at
# an existing media drive does not scatter show folders through it.
CONTAINER = "One Pace Premium"


def folder():
    """Where episodes are kept, always with forward slashes.

    Kodi takes either separator on Windows, but translatePath hands back
    backslashes and the rest of the path is built with slashes — comparing
    and removing those folders needs one shape, not two.
    """
    chosen = get_setting("download_folder")
    if not chosen or chosen == DEFAULT_FOLDER:
        # Already a folder of ours, so it needs no folder of ours inside it.
        base = _profile().rstrip("/\\") + "/downloads"
    else:
        base = xbmcvfs.translatePath(chosen).rstrip("/\\") + "/" + CONTAINER
    return base.replace("\\", "/") + "/"


def is_downloadable(url):
    return url.startswith(_DOWNLOADABLE)


def safe_name(text, fallback="Unknown"):
    """Strip what Windows, Linux and macOS each refuse in a filename."""
    cleaned = _ILLEGAL.sub("", text or "").strip().strip(".")
    return cleaned[:120] or fallback


def variant_of(params):
    """Which cut this is, from the provider's own bingeGroup.

    The display name is free text — the same cut is called "Extended" on one
    show and "Fillerver" on another — while the third part of the bingeGroup
    is a fixed vocabulary the version preference is already built on.
    """
    return str(params.get("variant") or "").strip().lower()


def variant_label(variant):
    """"Standard", "Extended", or whatever else the provider starts sending."""
    return (variant or "standard").replace("_", " ").title()


def _variant_order(variant):
    """Standard sorts first; anything else follows it alphabetically."""
    variant = (variant or "standard").lower()
    return "" if variant == "standard" else variant


def _suffix(variant):
    """Standard keeps the plain name, so older downloads are left alone."""
    if not variant or variant == "standard":
        return ""
    return f" ({variant_label(variant)})"


# When the debrid account cannot serve a file, the provider redirects to a
# short video that says why. Watching it is the point when streaming; saving
# it as the episode never is.
_ERROR_PATH = "/api_errors/"

_FIXABLE_IN_SETTINGS = {
    "ACCOUNT_INVALID", "AUTH_BAD_APIKEY", "AUTH_BLOCKED", "AUTH_ERROR",
    "AUTH_MISSING_APIKEY", "AUTH_USER_BANNED", "BADTOKEN", "BAD_TOKEN",
    "EXPIRED_TOKEN", "INVALID_ACCOUNT_OR_PASSWORD", "INVALID_CLIENT", "NO_AUTH",
    "UNAUTHENTICATED", "UNAUTHORIZED", "UNAUTHORIZED_CLIENT",
}
_ACCOUNT_LIMITS = {
    "ACTIVE_LIMIT", "COOLDOWN_LIMIT", "FREE_TRIAL_LIMIT_REACHED",
    "MAGNET_MUST_BE_PREMIUM", "MONTHLY_LIMIT", "MUST_BE_PREMIUM",
    "PAYMENT_REQUIRED", "PLAN_RESTRICTED_FEATURE", "PROXY_LIMIT_REACHED",
    "STORE_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS",
}
_TEMPORARY = {
    "BAD_GATEWAY", "DEBRID_SYNC_ALREADY_RUNNING", "DEBRID_SYNC_TRIGGERED",
    "DOWNLOAD_SERVER_ERROR", "INTERNAL_SERVER_ERROR", "MAINTENANCE",
    "NO_SERVER", "NO_SERVERS_AVAILABLE_ERROR", "SERVER_ERROR",
    "SERVICE_UNAVAILABLE", "STORE_SERVER_DOWN",
}
_THIS_STREAM = {
    "GONE", "LINK_OFFLINE", "MEDIA_NOT_CACHED_YET", "NOT_FOUND",
    "STORE_MAGNET_INVALID", "STORE_NAME_INVALID",
    "UNAVAILABLE_FOR_LEGAL_REASONS",
}

# BAD_REQUEST, CONFLICT, FORBIDDEN, LOCKED and the rest are left out on
# purpose: they could mean a key, a plan or a server, and the fallback says
# what the server said rather than sending someone to the wrong screen.


def error_code(url):
    """The provider's own name for what went wrong, if this is one of those."""
    if _ERROR_PATH not in (url or ""):
        return ""
    tail = url.split(_ERROR_PATH, 1)[1].split("?")[0].split("/")[0]
    return os.path.splitext(tail)[0].upper()


def explain_error(code):
    """A sentence a viewer can act on, and whether settings would help."""
    plain = code.replace("_", " ").capitalize()
    if code in _FIXABLE_IN_SETTINGS:
        return (f"Your configuration key was rejected.{chr(10)}{chr(10)}{plain}."
                f"{chr(10)}{chr(10)}Settings will open so you can enter a valid one."), True
    if code in _ACCOUNT_LIMITS:
        return (f"Your debrid account will not allow this right now."
                f"{chr(10)}{chr(10)}{plain}."), False
    if code in _TEMPORARY:
        return (f"The server is having trouble.{chr(10)}{chr(10)}{plain}."
                f"{chr(10)}{chr(10)}Try again in a little while."), False
    if code in _THIS_STREAM:
        return (f"That stream is not available.{chr(10)}{chr(10)}{plain}."
                f"{chr(10)}{chr(10)}Pick a different one and try again."), False
    return f"The server refused the download.{chr(10)}{chr(10)}{plain}.", False


def _target(params):
    """Series folder, season folder and filename, in our own list's style."""
    series = safe_name(params.get("series_name"), "One Pace")
    title = safe_name(params.get("episode_title"), "Episode")
    try:
        season = int(params.get("season"))
    except (TypeError, ValueError):
        season = 0
    try:
        episode = int(params.get("episode"))
    except (TypeError, ValueError):
        episode = 0
    # The provider names the real file in behaviorHints, so the URL is only a
    # fallback — a debrid link often ends in an id with no extension at all.
    source = params.get("filename") or params.get("video_url", "").split("?")[0]
    ext = os.path.splitext(source)[1].lower()
    if len(ext) > 5 or not ext:
        ext = ".mkv"
    # Season 0 is where Kodi keeps specials, so 0x01 reads correctly there.
    name = f"{season}x{episode:02d} - {title}{_suffix(variant_of(params))}{ext}"
    return f"{folder()}{series}/Season {season:02d}/", name


def _normalised(path):
    return path.replace("\\", "/")


def _index_path():
    return _profile() + _INDEX


# Only the fields the season and episode lists actually draw. The provider
# meta also carries every video, which would be megabytes on disk.
_META_KEYS = ("name", "poster", "background", "logo", "description", "genres",
              "cast", "released", "releaseInfo", "year", "imdbRating", "ageRating",
              "country", "status", "writer", "runtime", "seasons")


def read_index():
    """{"files": {path: meta}, "series": {name: trimmed provider meta}}."""
    data = {}
    try:
        p = _index_path()
        if xbmcvfs.exists(p):
            with xbmcvfs.File(p, "r") as handle:
                data = json.loads(handle.read() or "{}")
    except Exception as exc:
        log(f"[downloads] could not read the index: {exc}")
    if "files" not in data:
        data = {"files": data, "series": {}}
    data.setdefault("series", {})
    return data


def _write_index(data):
    try:
        xbmcvfs.mkdirs(_profile())
        with xbmcvfs.File(_index_path(), "w") as handle:
            handle.write(json.dumps(data, indent=2))
    except Exception as exc:
        log(f"[downloads] could not write the index: {exc}")


def _video_entry(meta, episode_id):
    """The provider's own entry for this episode, so the list can use it whole."""
    for video in (meta or {}).get("videos", ()):
        if str(video.get("id")) == str(episode_id):
            return video
    return {}


def _remember(path, params, meta=None, subtitles=()):
    """The list reads this, so a download looks like the rest of the add-on."""
    data = read_index()
    series = params.get("series_name") or "Downloads"
    if meta:
        data["series"][series] = {k: meta[k] for k in _META_KEYS if k in meta}
    data["files"][path] = {
        "video": _video_entry(meta, params.get("episode_id") or params.get("video_id")),
        # videoSize is what tells us later that the episode was re-released.
        "video_size": params.get("video_size") or 0,
        "duration": params.get("duration") or 0,
        "variant": variant_of(params),
        # The release we took, so a later check can tell it apart from a new one.
        "info_hash": _info_hash_of(params.get("video_url", "")),
        # Kept so they go when the episode goes, and travel when it moves.
        "subtitles": list(subtitles),
        # The provider composes resolution, chapters, runtime, bitrate and
        # source hash into this. Rebuilding it offline is not possible.
        "stream_desc": params.get("stream_desc", ""),
        "series_name": params.get("series_name", ""),
        "episode_title": params.get("episode_title", ""),
        "season": params.get("season", ""),
        "episode": params.get("episode", ""),
        "episode_id": params.get("episode_id", ""),
        "series_id": params.get("series_id", ""),
        "thumb": params.get("thumb", ""),
        "season_poster": params.get("season_poster", ""),
        "logo": params.get("logo", ""),
    }
    _write_index(data)


def _info_hash_of(url):
    """The debrid link carries the release hash: /play/<key>/<hash>/<size>/..."""
    parts = [p for p in url.split("/") if len(p) == 40 and all(
        c in "0123456789abcdef" for c in p.lower())]
    return parts[0].lower() if parts else ""


def _existing(params, destination):
    """The file we hold for this episode *and* this cut.

    Standard and Extended are different episodes as far as a viewer is
    concerned, so fetching one must never quietly replace the other.
    """
    episode_id = params.get("episode_id") or params.get("video_id")
    if episode_id:
        variant = variant_of(params)
        for path, meta in read_index()["files"].items():
            if (meta.get("episode_id") == episode_id
                    and (meta.get("variant") or "") == variant
                    and xbmcvfs.exists(path)):
                return path
    return destination if xbmcvfs.exists(destination) else ""


def _remove_rows(paths):
    data = read_index()
    if any(data["files"].pop(x, None) is not None for x in paths):
        _write_index(data)


# What download() tells its caller, so a queue knows whether to carry on.
OK, FAILED, CANCELLED, BLOCKED = "ok", "failed", "cancelled", "blocked"


def _blocked(response):
    """Say what the provider refused, and stop. Nothing has been written yet."""
    code = error_code(response.url)
    message, settings_help = explain_error(code)
    log(f"[downloads] the server answered with {code}")
    xbmcgui.Dialog().ok("Download", message)
    if settings_help:
        xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
    return BLOCKED


def download(params, meta=None):
    """Fetch one episode to disk. Runs for as long as the transfer takes."""
    url = params.get("video_url", "")
    if not is_downloadable(url):
        _notify_error("This stream cannot be downloaded")
        return FAILED

    directory, name = _target(params)
    destination = directory + name

    # A different stream of the same episode can carry a different extension,
    # so match on the episode itself or the same file would arrive twice.
    already = _existing(params, destination)
    if already:
        if not xbmcgui.Dialog().yesno(
                "Download",
                os.path.basename(already) + chr(10) + chr(10) + "is already here. Fetch it again?",
                nolabel="Keep", yeslabel="Replace"):
            return CANCELLED
    if not xbmcvfs.mkdirs(directory) and not xbmcvfs.exists(directory):
        _notify_error("Could not create the download folder")
        return FAILED

    # Written beside the real name, so a failed replacement cannot destroy
    # the copy that already worked.
    partial = destination + ".part"

    progress = xbmcgui.DialogProgressBG()
    progress.create("Downloading", name)
    monitor = xbmc.Monitor()
    written, total = 0, 0
    try:
        response = session().get(url, stream=True, timeout=30)
        response.raise_for_status()
        # Checked before a byte is written: the redirect is the whole answer.
        if error_code(response.url):
            progress.close()
            return _blocked(response)
        total = int(params.get("video_size") or 0) or int(
            response.headers.get("Content-Length") or 0)
        with xbmcvfs.File(partial, "w") as handle:
            for chunk in response.iter_content(chunk_size=_CHUNK):
                if monitor.abortRequested():
                    raise InterruptedError("Kodi is shutting down")
                if not chunk:
                    continue
                handle.write(bytearray(chunk))
                written += len(chunk)
                if total:
                    # A server that undercounts Content-Length would push this
                    # past 100 and Kodi refuses that.
                    progress.update(min(100, int(written * 100 / total)),
                                    "Downloading", name)
    except Exception as exc:
        progress.close()
        log(f"[downloads] {name!r} failed after {written} bytes: {exc}")
        xbmcvfs.delete(partial)
        _notify_error(f"Download failed: {exc}")
        return FAILED

    progress.close()
    if total and written < total:
        log(f"[downloads] {name!r} stopped short: {written} of {total} bytes")
        xbmcvfs.delete(partial)
        _notify_error("Download ended early, nothing kept")
        return FAILED

    # Swap through a side name. Deleting the old file first would mean a
    # rename that fails leaves neither copy.
    backup = destination + ".old"
    replacing = xbmcvfs.exists(destination)
    if replacing and not xbmcvfs.rename(destination, backup):
        xbmcvfs.delete(partial)
        _notify_error("Could not replace the file already there")
        return FAILED
    if not xbmcvfs.rename(partial, destination):
        xbmcvfs.delete(partial)
        if replacing:
            xbmcvfs.rename(backup, destination)
        _notify_error("Could not put the file in place")
        return FAILED
    if replacing:
        xbmcvfs.delete(backup)

    # Now the new file is in place, a cut under a different name can go.
    if already and already != destination:
        xbmcvfs.delete(already)
        _remove_rows([already])

    _remember(destination, params, meta, save_subtitles(destination, params))
    log(f"[downloads] saved {destination!r} ({written} bytes)")
    _notify_info(f"Downloaded {name}")
    if not params.get("in_season"):
        # A season writes one summary for the whole run instead.
        write_report(name, [describe(read_index()["files"].get(destination, {}))])
    refresh_container()
    return OK


# Not "Subs" or "Subtitles": Kodi scans subfolders with those names as well
# as the video's own, so the files it found there were added on top of the
# ordered list we hand it and every track appeared twice.
SUBS_DIR = ".onepace-subs"


def _subs_dir(video):
    """A folder of their own beside the video, that Kodi will not scan."""
    return f"{os.path.dirname(_normalised(video))}/{SUBS_DIR}/"


def save_subtitles(destination, params):
    """Write subtitle files beside the video, so Kodi finds them offline.

    Only for languages actually chosen. Playback already works this way — it
    fetches nothing when the setting is "all languages" — and downloading
    thirty files an episode for a setting nobody narrowed is worse than none.
    """
    from .playback import (_SUBS_URL, _fetch_subtitle, _filter_subtitles,
                           _wanted_langs, _VARIANT_RE)

    sub_id = params.get("sub_id", "")
    if not sub_id or get_setting("subs_enabled") == "false":
        return []
    wanted = _wanted_langs()
    if not wanted:
        log("[subs] no languages chosen, so none are kept beside the download")
        return []

    try:
        response = session().get(_SUBS_URL, timeout=10)
        response.raise_for_status()
        tracks = _filter_subtitles(response.json().get(sub_id, []), sub_id, wanted)
    except Exception as exc:
        log(f"[subs] could not read the subtitle list: {exc}")
        return []

    folder_for_subs = _subs_dir(destination)
    xbmcvfs.mkdirs(folder_for_subs)
    stem = folder_for_subs + os.path.splitext(os.path.basename(destination))[0]
    saved = []
    for track in tracks:
        url, lang = track.get("url"), track.get("lang")
        if not (url and lang):
            continue
        # A label only appears on alternate cuts; the plain track is the one
        # most people actually want, and it has none — so it gets no tag in
        # the filename either.
        match = _VARIANT_RE.search(track.get("label") or "")
        tag = "." + re.sub(r"[^\w.-]", "_", match.group(1)) if match else ""
        path = f"{stem}{tag}.{lang}.vtt"
        if _fetch_subtitle(url, path):
            saved.append(path)
    log(f"[subs] kept {len(saved)} file(s) beside {os.path.basename(destination)}")
    return saved


# The stream data lives in a public repo, so checking it costs the add-on's
# own server nothing. One call lists every file with the git hash of its
# contents, which is all that is needed to know whether anything changed.
_TREE_URL = ("https://api.github.com/repos/6ip/onepace-streams/git/trees/"
             "main?recursive=1")
_STREAM_URL = "https://6ip.github.io/onepace-streams/stream/{path}.json"

# A few arcs sit in subfolders; the rest are at the root of /stream.
_STREAM_DIRS = (("MUHN_", "Muhn"), ("ONIG_", "ONIG"),
                ("KUMA_SHAVED_", "KUMA_SHAVED"), ("fan_", "Specials"))


def stream_path(episode_id):
    for prefix, directory in _STREAM_DIRS:
        if episode_id.startswith(prefix):
            return f"{directory}/{episode_id}"
    return episode_id


def _tree_hashes():
    """path -> content hash, for every stream file, in one request."""
    tree = session().get(_TREE_URL, timeout=20).json().get("tree", ())
    return {t["path"]: t["sha"] for t in tree
            if t.get("path", "").startswith("stream/")}


def _release_of(episode_id, variant):
    """The infoHash the repo currently lists for this episode and cut."""
    url = _STREAM_URL.format(path=stream_path(episode_id))
    for stream in session().get(url, timeout=20).json().get("streams", ()):
        name = (stream.get("releaseName") or stream.get("filename") or "").lower()
        is_extended = "extended" in name
        if is_extended == (variant not in ("", "standard")):
            return stream.get("infoHash") or "", stream.get("videoSize") or 0
    return "", 0


# What our own naming produces: "6x05 - Arlong Park (Extended).mkv". Only the
# numbers and an optional trailing cut are read back; the title and everything
# else come from the provider, which is far more reliable than a filename.
_NAMED = re.compile(r"^(\d+)x(\d+) - (.+?)(?: \(([^)]+)\))?$")
_VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm")


def _walk_downloads():
    """Every video file under the download folder, with what its name says."""
    found = []
    root = folder()
    series_dirs, _ = xbmcvfs.listdir(root)
    for series in series_dirs:
        seasons, _ = xbmcvfs.listdir(f"{root}{series}/")
        for season_dir in seasons:
            if season_dir.startswith("."):
                continue
            here = f"{root}{series}/{season_dir}/"
            _, names = xbmcvfs.listdir(here)
            for name in names:
                stem, ext = os.path.splitext(name)
                if ext.lower() not in _VIDEO_EXTS:
                    continue
                match = _NAMED.match(stem)
                if not match:
                    log(f"[rescan] cannot read a season and episode from {name!r}")
                    continue
                season, episode, _title, cut = match.groups()
                found.append({"path": here + name, "series_name": series,
                              "season": int(season), "episode": int(episode),
                              "variant": (cut or "standard").lower()})
    return found


def rescan(_params=None):
    """Rebuild the index from what is actually on disk.

    A reinstall leaves the files but takes the index with it, and every
    download becomes invisible. Names carry the season and episode; the
    provider supplies the rest, so the rebuilt rows are as good as the
    originals apart from the release hash.
    """
    from .provider_api import _fetch_provider_meta

    data = _sweep(read_index())
    known = set(data["files"])
    try:
        on_disk = _walk_downloads()
    except Exception as exc:
        log(f"[rescan] could not read the download folder: {exc}")
        _notify_error("Could not read the download folder")
        return

    missing = [f for f in on_disk if f["path"] not in known]
    if not missing:
        _notify_info(f"All {len(on_disk)} file(s) already listed")
        return
    if not xbmcgui.Dialog().yesno(
            "Rescan", f"{len(missing)} file(s) here are not in the list."
            + chr(10) + chr(10) + "Look them up and add them?",
            nolabel="Cancel", yeslabel="Add"):
        return

    progress = xbmcgui.DialogProgressBG()
    progress.create("Rescanning downloads")
    added, unknown = 0, 0
    ids = _series_ids()
    for index, entry in enumerate(missing, 1):
        progress.update(int(index * 100 / len(missing)), "Rescanning downloads",
                        os.path.basename(entry["path"]))
        series_id = ids.get(entry["series_name"])
        meta = _fetch_provider_meta("series", series_id) if series_id else None
        video = _match_episode(meta, entry) if meta else None
        if not video:
            unknown += 1
            log(f"[rescan] no provider entry for {entry['path']!r}")
            continue
        _remember(entry["path"], _rebuilt(entry, video, meta), meta)
        added += 1
    progress.close()

    lines = [f"Added {added} of {len(missing)} found"]
    if unknown:
        lines.append(f"{unknown} could not be matched to an episode")
    write_report("Rescanned downloads", lines)
    _notify_info(f"Added {added} download(s)" if added else "Nothing could be added")
    refresh_container()


def _series_ids():
    """Series name -> provider id, from the catalog, in one request."""
    from .provider_api import _catalog_specs, _catalog_url, _fetch_provider_manifest

    try:
        specs = _catalog_specs(_fetch_provider_manifest() or {}, "series")
        if not specs:
            return {}
        from .provider_api import _fetch_catalog
        response = _fetch_catalog(_catalog_url("series", specs[0]["id"], "skip=0"))
        return {v["name"]: v["id"] for v in (response or {}).get("metas", ())
                if v.get("name") and v.get("id")}
    except Exception as exc:
        log(f"[rescan] could not read the catalog: {exc}")
        return {}


def _match_episode(meta, entry):
    for video in meta.get("videos", ()):
        if (video.get("season") == entry["season"]
                and (video.get("episode") or video.get("number")) == entry["episode"]):
            return video
    return None


def _rebuilt(entry, video, meta):
    """The params _remember expects, from the provider rather than the file."""
    season_poster = next(
        (s["poster"] for s in meta.get("seasons", ())
         if s.get("season") == entry["season"] and s.get("poster")), "")
    return {
        "series_name": entry["series_name"], "season": str(entry["season"]),
        "episode": str(entry["episode"]), "variant": entry["variant"],
        "episode_id": video.get("id", ""), "series_id": meta.get("id", ""),
        "episode_title": video.get("name") or video.get("title") or "",
        "thumb": video.get("thumbnail", ""), "season_poster": season_poster,
        "logo": meta.get("logo", ""), "video_size": 0, "duration": 0,
    }


def check_updates(_params=None):
    """See which downloads the repo has re-released since we fetched them.

    One request lists every stream file with a content hash. Only the ones
    whose hash moved are fetched individually, so a library of four hundred
    episodes normally costs a single request — and none of them touch the
    add-on's own server.
    """
    data = read_index()
    held = [(path, meta) for path, meta in data["files"].items()
            if meta.get("episode_id")]
    if not held:
        _notify_info("Nothing downloaded yet")
        return

    progress = xbmcgui.DialogProgressBG()
    progress.create("Checking downloads")
    changed, checked, failed = 0, 0, 0
    try:
        hashes = _tree_hashes()
    except Exception as exc:
        progress.close()
        log(f"[updates] could not read the file list: {exc}")
        _notify_error("Could not check for updates")
        return

    for index, (path, meta) in enumerate(held, 1):
        episode_id = meta["episode_id"]
        progress.update(int(index * 100 / len(held)), "Checking downloads",
                        meta.get("episode_title", ""))
        current = hashes.get(f"stream/{stream_path(episode_id)}.json", "")
        if current and current == meta.get("stream_sha"):
            continue                      # untouched since we fetched it
        checked += 1
        try:
            info_hash, size = _release_of(episode_id, meta.get("variant", ""))
        except Exception as exc:
            log(f"[updates] {episode_id}: {exc}")
            failed += 1
            continue
        # The file's hash only says the file was edited — adding a field to
        # the JSON changes it while the release stays put. What decides is the
        # release itself: its infoHash, or the byte count for a download made
        # before we stored one. With neither, there is nothing to compare, so
        # record what is there now and call it current.
        was, was_size = meta.get("info_hash") or "", meta.get("video_size") or 0
        if info_hash and was:
            stale = info_hash != was
        elif size and was_size:
            stale = size != was_size
        else:
            stale = False
            meta.setdefault("info_hash", info_hash)
            meta.setdefault("video_size", size)
        meta["stream_sha"] = current
        meta["outdated"] = stale
        if stale:
            changed += 1
            log(f"[updates] {episode_id} was re-released")
    progress.close()
    _write_index(data)

    lines = [f"{changed} of {len(held)} look re-released",
             f"{checked} needed a closer look, {len(held) - checked} unchanged"]
    if failed:
        lines.append(f"{failed} could not be checked")
    write_report("Checked downloads", lines)
    _notify_info(f"{changed} update(s) found" if changed else "Everything is current")
    refresh_container()


def _prune(path):
    """Drop the season and series folders once nothing is left in them.

    rmdir without force refuses a folder that still holds anything, so this
    can only ever remove the shells we made. The download root itself stays.
    """
    root = folder().rstrip("/")
    directory = os.path.dirname(_normalised(path)).rstrip("/")
    # Series and season, and nothing above them, however the paths compare.
    for _ in range(2):
        if not directory or directory == root or not directory.startswith(root + "/"):
            return
        if not xbmcvfs.rmdir(directory):
            return
        log(f"[downloads] removed the empty {directory!r}")
        directory = os.path.dirname(directory).rstrip("/")


# Whatever opens a folder on this desktop. Nothing on a TV box, which is why
# Kodi's own browser stays as the fallback.
_FILE_MANAGERS = (
    ("System.Platform.Windows", 'explorer.exe "{win}"'),
    ("System.Platform.OSX", 'open "{path}"'),
    ("System.Platform.Linux", 'xdg-open "{path}"'),
)


def browse(params):
    """Open the folder in the desktop's file manager, or Kodi's if there is none.

    Takes a path, or an episode id for the lists that only know that much.
    """
    path = params.get("path") or path_for(params.get("episode_id", ""))
    directory = os.path.dirname(_normalised(path)) if path else ""
    if not directory or not xbmcvfs.exists(directory + "/"):
        _notify_error("That folder is not there any more")
        return

    for condition, template in _FILE_MANAGERS:
        if xbmc.getCondVisibility(condition):
            command = template.format(path=directory,
                                      win=directory.replace("/", chr(92)))
            log(f"[downloads] opening {directory!r} with the file manager")
            xbmc.executebuiltin(f"System.Exec({command})")
            return

    log(f"[downloads] no file manager here, showing {directory!r} in Kodi")
    xbmc.executebuiltin(f"ActivateWindow(Videos,{directory},return)")


_REPORT = "last_download.txt"

# Enough to see what happened over an evening, few enough that the file stays
# small and the window stays readable.
_REPORT_KEEP = 10


def _read_report():
    try:
        path = _profile() + _REPORT
        if xbmcvfs.exists(path):
            with xbmcvfs.File(path) as handle:
                return handle.read() or ""
    except Exception as exc:
        log(f"[downloads] could not read the report: {exc}")
    return ""


def write_report(heading, lines):
    """Add a summary at the top, keeping the last few.

    A background notification is gone in four seconds, which is no use when
    a season took twenty minutes and you were in another room.
    """
    import time

    when = time.strftime("%d %B %Y, %H:%M") + time.strftime(" (%I:%M %p)").replace(" 0", " ")
    entry = [f"[B]{heading}[/B]  [COLOR FF888899]{when}[/COLOR]", ""]
    entry += [f"  {chr(8226)}  {line}" for line in lines]

    older = [block for block in _read_report().split(chr(10) * 3) if block.strip()]
    body = (chr(10) * 3).join([chr(10).join(entry)] + older[:_REPORT_KEEP - 1])
    try:
        xbmcvfs.mkdirs(_profile())
        with xbmcvfs.File(_profile() + _REPORT, "w") as handle:
            handle.write(body)
    except Exception as exc:
        log(f"[downloads] could not write the report: {exc}")


def show_report(_params=None):
    """Recent summaries, in the same window the changelog uses."""
    text = _read_report()
    if not text.strip():
        _notify_info("Nothing has been downloaded yet")
        return

    # The same helper What's New uses, so the donate button works here too.
    from .changelog import show_text
    show_text(text, "Downloads")


def _remove(paths):
    """Delete files and forget them. Returns how many actually went."""
    data = read_index()
    removed = 0
    for path in paths:
        if not xbmcvfs.exists(path) or xbmcvfs.delete(path):
            for sidecar in data["files"].get(path, {}).get("subtitles", ()):
                xbmcvfs.delete(sidecar)
            # Empty now, and rmdir refuses it otherwise.
            xbmcvfs.rmdir(_subs_dir(path))
            data["files"].pop(path, None)
            _prune(path)
            removed += 1
        else:
            log(f"[downloads] could not delete {path!r}")
    for name in [n for n in data["series"]
                 if not any(m.get("series_name") == n for m in data["files"].values())]:
        data["series"].pop(name, None)
    _write_index(data)
    return removed


def delete(params):
    """One episode, a whole season, or everything under one series."""
    path = params.get("path")
    series = params.get("series")
    season = params.get("season")
    files = read_index()["files"]

    if path:
        targets, what = [path], os.path.basename(path)
    else:
        targets = [p for p, m in files.items()
                   if (m.get("series_name") or "Downloads") == series
                   and (season is None or _season_of(m) == int(season))]
        if season is None:
            what = f"all {len(targets)} downloaded from {series}"
        else:
            label = "Specials" if int(season) == 0 else f"Season {season}"
            what = f"all {len(targets)} from {series} {label}"

    if not targets:
        _notify_error("Nothing left to delete")
        return
    if not xbmcgui.Dialog().yesno("Delete", f"Remove {what} from disk?",
                                  nolabel="Keep", yeslabel="Delete"):
        return

    removed = _remove(targets)
    if not removed:
        _notify_error("Could not delete that")
        return
    log(f"[downloads] deleted {removed} file(s)")
    _notify_info(f"Deleted {removed} file{'s' if removed != 1 else ''}")
    refresh_container()


def _relocate(src, dst):
    """Rename where we can, copy where the drive changes."""
    if xbmcvfs.rename(src, dst):
        return True
    if xbmcvfs.copy(src, dst):
        xbmcvfs.delete(src)
        return True
    return False


def _move_subtitles(paths, was, now):
    """Move each sidecar into the Subs folder beside the video's new home.

    Every one was written as the video's own name plus a tail, so swapping
    the stem is exact — no guessing where the name ends and the tail begins.
    """
    old_stem = _subs_dir(was) + os.path.splitext(os.path.basename(was))[0]
    new_stem = _subs_dir(now) + os.path.splitext(os.path.basename(now))[0]
    moved = []
    if not paths:
        return moved
    xbmcvfs.mkdirs(_subs_dir(now))
    for path in paths:
        if not _normalised(path).startswith(old_stem) or not xbmcvfs.exists(path):
            continue
        target = new_stem + _normalised(path)[len(old_stem):]
        if _relocate(path, target):
            moved.append(target)
        else:
            log(f"[subs] could not move {path!r}")
    xbmcvfs.rmdir(_subs_dir(was))
    return moved


def move_downloads(_params=None):
    """Bring everything already downloaded under the current folder.

    Changing the folder only redirects new downloads — the old files keep
    working where they are. This is for when you actually want them together.
    """
    data = read_index()
    planned = []
    for path, meta in data["files"].items():
        directory, name = _target(dict(meta, filename=path))
        if directory + name != path and xbmcvfs.exists(path):
            planned.append((path, directory, name))

    if not planned:
        _notify_info("Everything is already in the download folder")
        return
    if not xbmcgui.Dialog().yesno(
            "Move Downloads",
            f"Move {len(planned)} file{'s' if len(planned) != 1 else ''} into"
            + chr(10) + folder() + chr(10) + chr(10)
            + "Large files on another drive will take a while.",
            nolabel="Cancel", yeslabel="Move"):
        return

    progress = xbmcgui.DialogProgressBG()
    progress.create("Moving downloads")
    moved, failed = 0, 0
    for index, (path, directory, name) in enumerate(planned, 1):
        progress.update(int(index * 100 / len(planned)), "Moving downloads", name)
        if not xbmcvfs.mkdirs(directory) and not xbmcvfs.exists(directory):
            failed += 1
            continue
        destination = directory + name
        if xbmcvfs.exists(destination) or not _relocate(path, destination):
            log(f"[downloads] could not move {path!r}")
            failed += 1
            continue
        entry = data["files"].pop(path)
        # Subtitles only work where the video is, so they go with it.
        entry["subtitles"] = _move_subtitles(entry.get("subtitles", ()), path, destination)
        data["files"][destination] = entry
        _prune(path)
        moved += 1
    progress.close()

    if moved:
        _write_index(data)
    log(f"[downloads] moved {moved}, failed {failed}")
    if failed:
        _notify_error(f"Moved {moved}, could not move {failed}")
    else:
        _notify_info(f"Moved {moved} file{'s' if moved != 1 else ''}")
    refresh_container()


def _sweep(data):
    """Forget rows whose file someone deleted outside the add-on."""
    gone = [p for p in data["files"] if not xbmcvfs.exists(p)]
    if gone:
        for p in gone:
            data["files"].pop(p, None)
        _write_index(data)
        log(f"[downloads] dropped {len(gone)} entries with no file left")
    return data


def _empty(message):
    """Dressed like the root menu, since that is what an empty section is.

    Setting a content type here would have the skin draw an episode row with
    no episode in it, which is where the missing artwork came from.
    """
    media = f"special://home/addons/{ADDON_ID}/resources/skins/Default/media"
    icon = f"{media}/info.png"
    xbmcplugin.setContent(ADDON_HANDLE, "")
    item = xbmcgui.ListItem(label="Nothing downloaded yet", offscreen=True)
    item.setArt({"icon": icon, "thumb": icon, "poster": icon, "banner": icon,
                 "landscape": icon,
                 "fanart": f"special://home/addons/{ADDON_ID}/resources/fanart.png"})
    item.getVideoInfoTag().setPlot(message)
    _add_directory_items([(build_url("list_downloads"), item, False)])
    end_directory()


def _counts(item, metas):
    """The same properties the season list sets, so skins read them the same.

    Counted against the watched store, not assumed unwatched — a row saying
    4 when three have been seen is worse than saying nothing.
    """
    from . import watched as _watched

    series_id = next((m.get("series_id") for m in metas if m.get("series_id")), "")
    seen = _watched.get_watched(series_id) if series_id else set()
    total = len(metas)
    watched = sum(1 for m in metas if m.get("episode_id") in seen)
    props = {"TotalEpisodes": str(total),
             "UnWatchedEpisodes": str(total - watched)}
    if watched:
        props["WatchedEpisodes"] = str(watched)
    item.setProperties(props)
    if total and watched >= total:
        item.getVideoInfoTag().setPlaycount(1)


def play_url(path, meta):
    """Play a downloaded file through the add-on, not straight off disk.

    Going through play_video is what keeps a downloaded episode and a streamed
    one the same thing: one watched store, one set of resume points, and the
    next-episode card at the end of both. sub_id is deliberately left out —
    fetching the subtitle list would stall when the point is to be offline.
    """
    return build_url("play_video", **_play_fields(path, meta))


def _size_label(meta):
    """Decimal MB, which is what the provider prints beside its own streams."""
    size = meta.get("video_size") or 0
    return f"{size / 1_000_000:.2f} MB" if size else ""


def describe(meta):
    """What this copy is, for the row in the picker and the player's OSD."""
    parts = ["Downloaded", variant_label(meta.get("variant"))]
    size = _size_label(meta)
    if size:
        parts.append(size)
    return "  |  ".join(parts)


def _play_fields(path, meta):
    video = meta.get("video") or {}
    fields = {"video_url": path, "series_id": meta.get("series_id", ""),
              "episode_id": meta.get("episode_id", ""),
              "season": meta.get("season", ""), "episode": meta.get("episode", ""),
              "series_name": meta.get("series_name", ""),
              "episode_title": meta.get("episode_title", ""),
              "thumb": meta.get("thumb", ""),
              "season_poster": meta.get("season_poster", ""),
              "logo": meta.get("logo", ""),
              # Without these the player has no plot to show and the info
              # panel falls back to "Not available".
              "episode_plot": video.get("overview", ""),
              # Our own header, then whatever the provider said about the file
              # when we fetched it. The original name says [RD], which stopped
              # being true the moment it landed on disk.
              "stream_name": describe(meta),
              "stream_desc": meta.get("stream_desc", "")}
    return {k: v for k, v in fields.items() if v}


# In front of the title, not after it: a list view truncates the tail, and a
# flag in a fixed spot is what the eye scans down a column. A list renders the
# bold inside the colour; the select dialog leaves its closing tag showing, so
# that one goes without.
_ARROW = "[↓]"
_REDO = "[↻]"
_MARK = f"[COLOR FF2ECC71][B]{_ARROW}[/B][/COLOR]"
_MARK_PLAIN = f"[COLOR FF2ECC71]{_ARROW}[/COLOR]"
# Amber, and a different glyph: held, but the repo has moved on.
_STALE = f"[COLOR FFF5A623][B]{_REDO}[/B][/COLOR]"
_STALE_PLAIN = f"[COLOR FFF5A623]{_REDO}[/COLOR]"


def enabled():
    """Off until asked for, so an update does not rearrange anyone's menu."""
    return get_setting("downloads_enabled") == "true"


def outdated_ids():
    """Episode ids whose release moved on since we fetched them."""
    if not enabled():
        return set()
    return {m.get("episode_id") for m in read_index()["files"].values()
            if m.get("outdated") and m.get("episode_id")}


def mark(label, episode_id, on_disk, plain=False, stale=()):
    """Flag a title that is already on disk, or one worth fetching again.

    plain drops the bold, which a select dialog renders as a literal [/B].
    """
    if not episode_id or episode_id not in on_disk:
        return label
    if not enabled() or get_setting("download_marker") == "false":
        return label
    if episode_id in stale:
        return f"{_STALE_PLAIN if plain else _STALE} {label}"
    return f"{_MARK_PLAIN if plain else _MARK} {label}"


# The version picker stores an index, the same one Preferred Version uses.
_PREFERRED_VARIANT = {"1": "standard", "2": "extended"}


def copies_of(episode_id):
    """Every cut of this episode we hold, standard first."""
    if not episode_id or not enabled():
        return []
    held = [(path, meta) for path, meta in read_index()["files"].items()
            if meta.get("episode_id") == episode_id and xbmcvfs.exists(path)]
    return sorted(held, key=lambda kv: _variant_order(kv[1].get("variant")))


def path_for(episode_id):
    """Where this episode lives on disk, if we hold it at all."""
    held = copies_of(episode_id)
    return held[0][0] if held else ""


def local_playback(episode_id):
    """play_video params for the copy on disk, when we should prefer it."""
    if get_setting("prefer_downloads") == "false":
        return None
    held = copies_of(episode_id)
    if not held:
        return None
    # Holding both cuts, honour the same preference the stream picker uses.
    wanted = _PREFERRED_VARIANT.get(get_setting("preferred_version"))
    if wanted and len(held) > 1:
        held = [kv for kv in held if (kv[1].get("variant") or "standard") == wanted] or held
    path, meta = held[0]
    return _play_fields(path, meta)


def local_subtitles(path):
    """The sidecars we saved for this file, in the order the feed listed them.

    Kodi sorts what it finds in a folder by name, which puts ALT above the
    plain track. Handing it the list instead keeps the feed's own order.
    """
    kept = [p for p in read_index()["files"].get(path, {}).get("subtitles", ())
            if xbmcvfs.exists(p)]
    return kept


def offer_manual(episode_id, on_disk):
    """True when pressing play would silently use the copy on disk.

    That is what the preference is for, but it leaves no way to stream a
    better cut tonight — so the row gets a way back to the picker.
    """
    return (enabled() and episode_id in on_disk
            and get_setting("prefer_downloads") != "false")


def local_options(episode_id):
    """Each cut on disk, offered alongside the streams.

    Used when the preference is off: rather than pretending the files are not
    there, put them at the top of the list and let the choice be made.
    """
    options = []
    for path, meta in copies_of(episode_id):
        options.append((f"{_MARK_PLAIN} {describe(meta)}", _play_fields(path, meta)))
    return options


def downloaded_ids():
    """Episode ids we hold on disk, for marking them in the ordinary lists."""
    if not enabled():
        return set()
    return {m.get("episode_id") for m in read_index()["files"].values()
            if m.get("episode_id")}


def _season_of(meta):
    season = meta.get("season")
    return int(season) if str(season).lstrip("-").isdigit() else 0


def _season_art(meta_for_series, metas):
    """Prefer the dedicated season poster the provider gave us."""
    for entry in meta_for_series.get("seasons", ()):
        if entry.get("season") == _season_of(metas[0]) and entry.get("poster"):
            return entry["poster"]
    return metas[0].get("season_poster")


def list_downloads(params=None):
    """Series, then seasons, then episodes — the same walk as browsing."""
    from .art import (_cast_list, _set_art, _set_episode_art, _set_episode_rating,
                      _set_season_art, _set_show_tags, _set_video_tags)
    from .provider_api import (_parse_air_date, _parse_release_year,
                               _parse_runtime_seconds)

    params = params or {}
    data = _sweep(read_index())
    files, series_meta = data["files"], data["series"]
    if not files:
        _empty("Pick an episode, then choose Download from its menu.")
        return

    series = params.get("series")
    season = params.get("season")

    if series is None:
        by_series = {}
        for path, meta in files.items():
            by_series.setdefault(meta.get("series_name") or "Downloads", []).append(meta)
        if len(by_series) == 1:
            series = next(iter(by_series))
        else:
            xbmcplugin.setContent(ADDON_HANDLE, "tvshows")
            xbmcplugin.setPluginCategory(ADDON_HANDLE, "Downloads")
            items = []
            for name, metas in sorted(by_series.items()):
                show = series_meta.get(name, {})
                item = xbmcgui.ListItem(label=name, offscreen=True)
                tags = item.getVideoInfoTag()
                _set_video_tags(tags, show, name)
                tags.setTvShowTitle(name)
                tags.setMediaType("tvshow")
                _set_show_tags(tags, show, actors=_cast_list(show))
                _set_art(item, show)
                _counts(item, metas)
                item.addContextMenuItems([(
                    "[B]Delete Series[/B]",
                    f"RunPlugin({build_url('delete_download', series=name)})",
                )])
                items.append((build_url("list_downloads", series=name), item, True))
            _add_directory_items(items)
            end_directory()
            return

    mine = {p: m for p, m in files.items()
            if (m.get("series_name") or "Downloads") == series}
    if not mine:
        _empty("Nothing left under that name.")
        return
    show = series_meta.get(series, {})
    actors = _cast_list(show)

    if season is None:
        seasons = {}
        for path, meta in mine.items():
            seasons.setdefault(_season_of(meta), []).append(meta)
        if len(seasons) > 1:
            xbmcplugin.setContent(ADDON_HANDLE, "seasons")
            xbmcplugin.setPluginCategory(ADDON_HANDLE, series)
            items = []
            for number in sorted(seasons):
                metas = seasons[number]
                label = "Specials" if number == 0 else f"Season {number}"
                item = xbmcgui.ListItem(label=label, offscreen=True)
                tags = item.getVideoInfoTag()
                _set_video_tags(tags, show, label)
                tags.setTvShowTitle(series)
                tags.setSeason(number)
                tags.setMediaType("season")
                _set_show_tags(tags, show, premiered=False, trailer=False, actors=actors)
                _set_season_art(item, show, _season_art(show, metas))
                _counts(item, metas)
                item.addContextMenuItems([(
                    f"[B]Delete {label}[/B]",
                    f"RunPlugin({build_url('delete_download', series=series, season=number)})",
                )])
                items.append((build_url("list_downloads", series=series, season=number),
                              item, True))
            _add_directory_items(items)
            end_directory()
            return
        season = next(iter(seasons))

    from . import bookmarks as _bookmarks
    from . import watched as _watched
    from .episode_routes import _episode_label

    watched = _watched.get_watched(
        next((m.get("series_id") for m in mine.values() if m.get("series_id")), ""))

    chosen = {p: m for p, m in mine.items() if _season_of(m) == int(season)}
    xbmcplugin.setContent(ADDON_HANDLE, "episodes")
    label = "Specials" if int(season) == 0 else f"Season {season}"
    xbmcplugin.setPluginCategory(ADDON_HANDLE, f"{series} - {label}")
    items = []
    for path, meta in sorted(chosen.items(),
                             key=lambda kv: (_season_of(kv[1]),
                                             int(kv[1].get("episode") or 0),
                                             _variant_order(kv[1].get("variant")))):
        number = int(meta.get("episode") or 0)
        title = meta.get("episode_title") or os.path.basename(path)
        label = _episode_label(title, int(season), number, meta.get("episode_id", ""))
        # Two cuts of one episode sit next to each other, so say which is which.
        if meta.get("variant") and meta["variant"] != "standard":
            label += f"  ({variant_label(meta['variant'])})"
        item = xbmcgui.ListItem(label=label, offscreen=True)
        video = meta.get("video") or {}
        tags = item.getVideoInfoTag()
        tags.setMediaType("episode")
        tags.setTitle(title)
        tags.setTvShowTitle(series)
        tags.setSeason(int(season))
        tags.setEpisode(number)

        # Everything below mirrors the episode list, off the same provider entry.
        plot = video.get("overview") or show.get("description")
        if plot:
            tags.setPlot(plot)
        year = _parse_release_year(video.get("released") or show.get("releaseInfo"))
        if year:
            tags.setYear(year)
        air_date = _parse_air_date(video)
        if air_date:
            tags.setPremiered(air_date)
            tags.setFirstAired(air_date)
        runtime = meta.get("duration") or _parse_runtime_seconds(video)
        if runtime:
            tags.setDuration(runtime)
        if show.get("genres"):
            tags.setGenres(show["genres"])
        _set_episode_rating(tags, video)
        _set_show_tags(tags, show, premiered=False, trailer=False, actors=actors)
        _set_episode_art(item, video or {"thumbnail": meta.get("thumb")}, show,
                         meta.get("season_poster"))

        # The same watched tick and resume bar the episode list draws.
        episode_id = meta.get("episode_id", "")
        bookmark = _bookmarks.get(episode_id) if episode_id else None
        if episode_id and episode_id in watched:
            tags.setPlaycount(1)
        elif bookmark:
            pos, whole = bookmark.get("pos", 0), bookmark.get("total", 0)
            if whole > 0:
                pct = min(99, max(1, int(pos / whole * 100)))
                item.setProperty("WatchedProgress", str(pct))
                item.setProperty("PercentPlayed", str(pct))
                tags.setResumePoint(pos, whole)

        item.setProperty("IsPlayable", "true")
        item.setProperty("Downloaded", "true")
        menu = []
        if episode_id:
            mark = "[B]Mark Unwatched[/B]" if episode_id in watched else "[B]Mark Watched[/B]"
            menu.append((mark, "RunPlugin(%s)" % build_url(
                "mark_watched", scope="episode",
                series_id=meta.get("series_id", ""), episode_id=episode_id)))
            if bookmark:
                menu.append(("[B]Clear Progress[/B]", "RunPlugin(%s)" % build_url(
                    "clear_progress", episode_id=episode_id)))
        menu.append(("[B]Browse Folder[/B]",
                     "RunPlugin(%s)" % build_url("browse_download", path=path)))
        # The same wording My Lists uses, and the same jump: out of a filtered
        # view into the whole season, downloaded or not.
        if meta.get("series_id"):
            menu.append(("[B]Browse Season...[/B]",
                         "ActivateWindow(Videos,%s,return)" % build_url(
                             "list_episodes", catalog_type="series",
                             video_id=meta["series_id"], season=season)))
        menu.append(("[B]Delete[/B]",
                     "RunPlugin(%s)" % build_url("delete_download", path=path)))
        item.addContextMenuItems(menu)
        items.append((play_url(path, meta), item, False))
    _add_directory_items(items)
    end_directory()
