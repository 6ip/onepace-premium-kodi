"""Keep episodes on disk, and list what is already there."""
import json
import os
import re

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from . import download_queue
from .route_common import (_add_directory_items, _notify_error, _notify_info,
                           end_directory, set_category, set_content)
from .utils import (ADDON_ID, background, build_url, get_setting, log,
                    refresh_container, session)

# Only ever asked for the profile path, which never changes between calls.
_ADDON = xbmcaddon.Addon()

_INDEX = "downloads.json"
_CHUNK = 1024 * 256
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Anything else we cannot fetch ourselves, like an Elementum magnet.
_DOWNLOADABLE = ("http://", "https://")


def _profile():
    p = xbmcvfs.translatePath(_ADDON.getAddonInfo("profile"))
    return p if p.endswith(("/", "\\")) else p + "/"


DEFAULT_FOLDER = f"special://profile/addon_data/{ADDON_ID}/downloads/"

# Our own folder inside theirs, so a media drive is not scattered.
# Never rename it: a new name hides every episode already downloaded.
CONTAINER = "One Pace Premium"


def folder():
    """Where episodes are kept, always with forward slashes."""
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
    """Which cut this is, from the provider's own bingeGroup."""
    return str(params.get("variant") or "").strip().lower()


def variant_label(variant):
    """"Standard", "Extended", or whatever else the provider starts sending."""
    return (variant or "standard").replace("_", " ").title()


def cut_of(variant):
    """The one name for a cut, so two spellings of it are not two cuts."""
    return (variant or "standard").strip().lower()


def _variant_order(variant):
    """Standard sorts first; anything else follows it alphabetically."""
    return "" if cut_of(variant) == "standard" else cut_of(variant)


def _suffix(variant):
    """Standard keeps the plain name, so older downloads are left alone."""
    if not variant or variant == "standard":
        return ""
    return f" ({variant_label(variant)})"


# A debrid account that cannot serve a file redirects to a why video.
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

# The ambiguous codes are left out; the fallback quotes the server.


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
    # behaviorHints names the real file; a debrid link has no extension.
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


# Only the fields the season and episode lists actually draw.
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


def _remember(path, params, meta=None, subtitles=(), artwork=None):
    """The list reads this, so a download looks like the rest of the add-on."""
    data = read_index()
    remember_into(data, path, params, meta, subtitles, artwork)
    _write_index(data)


def remember_into(data, path, params, meta=None, subtitles=(), artwork=None):
    """Add one row to an index already in hand."""
    series = params.get("series_name") or "Downloads"
    art = artwork or {}
    if meta:
        # Fresh metadata must not forget the pictures already on disk.
        kept = (data["series"].get(series) or {}).get("art")
        data["series"][series] = {k: meta[k] for k in _META_KEYS if k in meta}
        if kept:
            data["series"][series]["art"] = kept
    if art.get("series"):
        data["series"].setdefault(series, {})["art"] = art["series"]
    data["files"][path] = {
        "video": _video_entry(meta, params.get("episode_id") or params.get("video_id")),
        # videoSize is what tells us later that the episode was re-released.
        "video_size": params.get("video_size") or 0,
        "duration": params.get("duration") or 0,
        "variant": variant_of(params),
        # The release we took, to tell it from a newer one later.
        "info_hash": _info_hash_of(params.get("video_url", "")),
        # Kept so they go when the episode goes, and travel when it moves.
        "subtitles": list(subtitles),
        "artwork": art.get("episode") or {},
        # The provider composes this; it cannot be rebuilt offline.
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
        # A file we only adopted, somewhere of the viewer's choosing.
        "imported": bool(params.get("imported")),
    }


def _info_hash_of(url):
    """The debrid link carries the release hash: /play/<key>/<hash>/<size>/..."""
    parts = [p for p in url.split("/") if len(p) == 40 and all(
        c in "0123456789abcdef" for c in p.lower())]
    return parts[0].lower() if parts else ""


def _existing(params, destination):
    """The file we hold for this episode *and* this cut."""
    episode_id = params.get("episode_id") or params.get("video_id")
    if episode_id:
        variant = cut_of(variant_of(params))
        for path, meta in read_index()["files"].items():
            if (meta.get("episode_id") == episode_id
                    and cut_of(meta.get("variant")) == variant
                    and xbmcvfs.exists(path)):
                return path
    return destination if xbmcvfs.exists(destination) else ""


def _other_cut(params):
    """A flagged copy of this episode in a different cut, and which cut that is."""
    episode_id = params.get("episode_id") or params.get("video_id")
    if not episode_id:
        return "", ""
    variant = cut_of(variant_of(params))
    for path, meta in read_index()["files"].items():
        held = cut_of(meta.get("variant"))
        if (meta.get("episode_id") == episode_id and meta.get("outdated")
                and not meta.get("imported") and held != variant
                and xbmcvfs.exists(path)):
            return path, held
    return "", ""


def _rehome_series_art(data, series):
    """No copy of ours is left to sit beside, so bring the pictures home."""
    art = (data["series"].get(series) or {}).get("art") or {}
    if not art:
        return
    here = _art_dir()
    xbmcvfs.mkdirs(here)
    stem, names = safe_name(series, "series"), dict(_SERIES_ART)
    brought = {}
    for key, picture in art.items():
        if not picture or picture.startswith(here):
            brought[key] = picture
            continue
        brought[key] = _moved_to(
            picture, f"{here}{stem}-{names[key]}{os.path.splitext(picture)[1]}")
    data["series"][series]["art"] = brought


def _drop_artwork(data, row):
    """A picture goes once no row and no series still points at it."""
    series = row.get("series_name") or "Downloads"
    rows = list(data["files"].values())
    mine = [m for m in rows if (m.get("series_name") or "Downloads") == series]
    # Their copies outlive ours, and our pictures must not outlive our folder.
    if mine and not any(not m.get("imported") for m in mine):
        _rehome_series_art(data, series)
    going = list((row.get("artwork") or {}).values())
    if not mine:
        going += list(((data["series"].get(series) or {}).get("art") or {}).values())
        if series in data["series"]:
            data["series"][series].pop("art", None)
    still = {p for m in rows for p in (m.get("artwork") or {}).values() if p}
    still |= {p for entry in data["series"].values()
              for p in (entry.get("art") or {}).values() if p}
    for picture in going:
        if picture and picture not in still:
            xbmcvfs.delete(picture)


def _remove_rows(paths):
    data = read_index()
    if any(data["files"].pop(x, None) is not None for x in paths):
        _write_index(data)


# What download() tells its caller, so a queue knows whether to carry on.
OK, FAILED, CANCELLED, BLOCKED = "ok", "failed", "cancelled", "blocked"


def _said(title, message):
    """Say it in a dialog, not a notification."""
    xbmcgui.Dialog().ok(title, message)


def _blocked(response):
    """Say what the provider refused, and stop. Nothing has been written yet."""
    code = error_code(response.url)
    message, settings_help = explain_error(code)
    log(f"[downloads] the server answered with {code}")
    xbmcgui.Dialog().ok("Download", message)
    if settings_help:
        xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
    return BLOCKED


def download(params, meta=None, ticket=None):
    """Fetch one episode to disk, waiting its turn if another is running."""
    url = params.get("video_url", "")
    if not is_downloadable(url):
        _notify_error("This stream cannot be downloaded")
        return FAILED

    directory, name = _target(params)
    destination = directory + name

    # Match on the episode, or another extension of it arrives twice.
    already = _existing(params, destination)
    if already:
        # A copy they imported is not ours to replace, so say so and keep both.
        question, yeslabel = "is already here. Fetch it again?", "Replace"
        if imported(already):
            question, yeslabel = ("is one you imported. Download our own copy "
                                  "as well?"), "Download"
        if not xbmcgui.Dialog().yesno(
                "Download",
                os.path.basename(already) + chr(10) + chr(10) + question,
                nolabel="Keep", yeslabel=yeslabel):
            return CANCELLED
    else:
        # The cut we flagged is gone, so replacing it is a choice, not a guess.
        other, other_cut = _other_cut(params)
        # A run picked its cut at the start, so it replaces without stopping.
        if other and (params.get("in_season") or xbmcgui.Dialog().yesno(
                "Download",
                os.path.basename(other) + chr(10) + chr(10)
                + f"You hold the {variant_label(other_cut)} cut, and this one is "
                  f"{variant_label(cut_of(variant_of(params)))}." + chr(10) + chr(10)
                + "Replace it, or keep both?",
                nolabel="Keep both", yeslabel="Replace")):
            already = other
    if not xbmcvfs.mkdirs(directory) and not xbmcvfs.exists(directory):
        _notify_error("Could not create the download folder")
        return FAILED

    progress = xbmcgui.DialogProgressBG()
    progress.create("Downloading", name)
    mine = ticket or download_queue.acquire(
        name, progress, detail=params.get("series_name") or "")
    if mine is None:
        progress.close()
        return CANCELLED
    try:
        return _fetch(params, meta, directory, name, already, progress, mine)
    finally:
        progress.close()
        if not ticket:
            download_queue.release(mine)


def _fetch(params, meta, directory, name, already, progress, ticket):
    """The transfer itself, with the download slot already held."""
    url = params.get("video_url", "")
    destination = directory + name
    # Beside the real name, so a failed swap cannot destroy the copy.
    partial = destination + ".part"
    monitor = xbmc.Monitor()
    written, total, stopped = 0, 0, False
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
                if download_queue.cancelled(ticket):
                    # Not straight out: the part file is still open.
                    stopped = True
                    break
                if not chunk:
                    continue
                handle.write(bytearray(chunk))
                written += len(chunk)
                if total:
                    # A short Content-Length would pass 100; Kodi refuses.
                    pct = min(100, int(written * 100 / total))
                    progress.update(pct, "Downloading", name)
                    download_queue.beat(ticket, pct)
    except Exception as exc:
        progress.close()
        log(f"[downloads] {name!r} failed after {written} bytes: {exc}")
        xbmcvfs.delete(partial)
        _notify_error(f"Download failed: {exc}")
        return FAILED

    progress.close()
    if stopped:
        log(f"[downloads] {name!r} cancelled after {written} bytes")
        xbmcvfs.delete(partial)
        _notify_info(f"Cancelled {name}")
        return CANCELLED
    if total and written < total:
        log(f"[downloads] {name!r} stopped short: {written} of {total} bytes")
        xbmcvfs.delete(partial)
        _notify_error("Download ended early, nothing kept")
        return FAILED

    # Swap through a side name.
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
    if already and already != destination and not imported(already):
        gone = read_index()["files"].get(already) or {}
        xbmcvfs.delete(already)
        for sidecar in gone.get("subtitles", ()):
            xbmcvfs.delete(sidecar)
        # Empty now, and rmdir refuses it otherwise.
        xbmcvfs.rmdir(_subs_dir(already))
        picture = (gone.get("artwork") or {}).get("thumb")
        if picture:
            xbmcvfs.delete(picture)
        _remove_rows([already])
        # Its folders are ours too, wherever that cut happened to be kept.
        _prune(already, _root_of(already))

    _remember(destination, params, meta, save_subtitles(destination, params),
              save_artwork(destination, params, meta))
    log(f"[downloads] saved {destination!r} ({written} bytes)")
    _notify_info(f"Downloaded {name}")
    if not params.get("in_season"):
        # A season writes one summary for the whole run instead.
        write_report(name, [describe(read_index()["files"].get(destination, {}))])
    refresh_container()
    return OK


# Not "Subs" or "Subtitles": Kodi scans those too, so tracks double up.
SUBS_DIR = ".onepace-subs"


def _subs_dir(video):
    """A folder of their own beside the video, that Kodi will not scan."""
    return f"{os.path.dirname(_normalised(video))}/{SUBS_DIR}/"


def save_subtitles(destination, params):
    """Write subtitle files beside the video, so Kodi finds them offline."""
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
        # Only alternate cuts get a label, so the plain track gets no tag.
        match = _VARIANT_RE.search(track.get("label") or "")
        tag = "." + re.sub(r"[^\w.-]", "_", match.group(1)) if match else ""
        path = f"{stem}{tag}.{lang}.vtt"
        if _fetch_subtitle(url, path):
            saved.append(path)
    log(f"[subs] kept {len(saved)} file(s) beside {os.path.basename(destination)}")
    return saved


# Bigger than this is not a poster, whatever the server claims.
_ART_MAX = 8 * 1024 * 1024
_ART_EXTS = (".jpg", ".jpeg", ".png", ".webp")
# Our key for it, and the name Kodi looks for.
_SERIES_ART = (("poster", "poster"), ("background", "fanart"),
               ("logo", "clearlogo"))


def _series_dir(path):
    """The series folder, one above the season the episode sits in."""
    return os.path.dirname(os.path.dirname(_normalised(path))) + "/"


def _art_ext(url):
    """Keep the picture's own extension, falling back to the common one."""
    ext = os.path.splitext(_normalised(url).split("?")[0])[1].lower()
    return ext if ext in _ART_EXTS else ".jpg"


def _fetch_art(url, path):
    """One picture, written through a side name. Failing is never fatal."""
    if not url:
        return ""
    if xbmcvfs.exists(path):
        return path
    partial = path + ".part"
    try:
        response = session().get(url, timeout=10)
        response.raise_for_status()
        content = response.content
        if not content or len(content) > _ART_MAX:
            log(f"[art] {url} came back as {len(content)} bytes, skipping")
            return ""
        with xbmcvfs.File(partial, "w") as handle:
            handle.write(bytearray(content))
        if not xbmcvfs.rename(partial, path):
            xbmcvfs.delete(partial)
            return ""
    except Exception as exc:
        log(f"[art] could not keep {url}: {exc}")
        xbmcvfs.delete(partial)
        return ""
    return path


def _season_art_name(season):
    """Kodi's own name for a season's art, specials included."""
    return "season-specials" if season == 0 else f"season{season:02d}"


def save_artwork(destination, params, meta=None):
    """Keep the pictures beside the video, in the names Kodi looks for."""
    series_dir = _series_dir(destination)
    try:
        season = int(params.get("season"))
    except (TypeError, ValueError):
        season = 0

    episode = {}
    thumb = params.get("thumb", "")
    if thumb:
        stem = os.path.splitext(_normalised(destination))[0]
        episode["thumb"] = _fetch_art(thumb, f"{stem}-thumb{_art_ext(thumb)}")
    poster = params.get("season_poster", "")
    if poster:
        episode["season_poster"] = _fetch_art(
            poster,
            f"{series_dir}{_season_art_name(season)}-poster{_art_ext(poster)}")

    # One set per series, and only for the seasons actually held.
    show = {}
    for key, name in _SERIES_ART:
        kept = _fetch_art((meta or {}).get(key, ""),
                          f"{series_dir}{name}{_art_ext((meta or {}).get(key, ''))}")
        if kept:
            show[key] = kept

    episode = {k: v for k, v in episode.items() if v}
    log(f"[art] kept {len(episode) + len(show)} picture(s) for "
        f"{os.path.basename(destination)}")
    return {"episode": episode, "series": show}


def _art_dir():
    """Our own folder, for pictures that cannot sit beside the video."""
    return _profile() + "art/"


def save_imported_artwork(params, meta=None):
    """Their file stays untouched, so our pictures live in our own folder."""
    here = _art_dir()
    xbmcvfs.mkdirs(here)
    stem = safe_name(params.get("episode_id") or "", "episode")
    series = safe_name(params.get("series_name") or "", "series")
    try:
        season = int(params.get("season"))
    except (TypeError, ValueError):
        season = 0

    episode = {}
    thumb = params.get("thumb", "")
    if thumb:
        episode["thumb"] = _fetch_art(thumb, f"{here}{stem}-thumb{_art_ext(thumb)}")
    poster = params.get("season_poster", "")
    if poster:
        episode["season_poster"] = _fetch_art(
            poster,
            f"{here}{series}-{_season_art_name(season)}-poster{_art_ext(poster)}")

    show = {}
    for key, name in _SERIES_ART:
        url = (meta or {}).get(key, "")
        kept = _fetch_art(url, f"{here}{series}-{name}{_art_ext(url)}")
        if kept:
            show[key] = kept

    episode = {k: v for k, v in episode.items() if v}
    log(f"[art] kept {len(episode) + len(show)} picture(s) for "
        f"{params.get('episode_id') or 'an imported file'}")
    return {"episode": episode, "series": show}


# Three in a row is the connection, not three unlucky pictures.
_ART_GIVE_UP = 3


def _cancelled(progress):
    """Only a modal bar has a Cancel; a background one has no button at all."""
    return bool(progress) and hasattr(progress, "iscanceled") and progress.iscanceled()


def _say(progress, percent, text):
    """A modal bar takes one message, a background one a heading as well."""
    if hasattr(progress, "iscanceled"):
        progress.update(percent, text)
    else:
        progress.update(percent, "Artwork", text)


def fill_artwork(only=(), progress=None):
    """Fetch what any row is missing: ours beside it, theirs in our folder."""
    monitor = xbmc.Monitor()
    data = read_index()
    rows = [(p, m) for p, m in data["files"].items() if not only or p in only]
    filled, changed, in_a_row = 0, False, 0
    for number, (path, meta) in enumerate(rows, 1):
        if monitor.abortRequested() or _cancelled(progress):
            log("[art] stopped before it had been through them all")
            break
        if progress:
            _say(progress, int(number * 100 / max(len(rows), 1)),
                 meta.get("episode_title", ""))
        name = meta.get("series_name") or "Downloads"
        show = data["series"].get(name) or {}
        art, kept = meta.get("artwork") or {}, show.get("art") or {}
        # Recorded is not enough: one deleted by hand is missing as well.
        wants = [key for key in ("thumb", "season_poster")
                 if meta.get(key)
                 and not (art.get(key) and xbmcvfs.exists(art[key]))]
        lacks = [key for key, _ in _SERIES_ART
                 if show.get(key)
                 and not (kept.get(key) and xbmcvfs.exists(kept[key]))]
        if not wants and not lacks:
            continue
        if not lacks:
            # One set per series: a second copy would be drawn by nothing.
            show = {k: v for k, v in show.items()
                    if k not in {key for key, _ in _SERIES_ART}}
        if meta.get("imported"):
            got = save_imported_artwork(meta, show)
        elif xbmcvfs.exists(path):
            got = save_artwork(path, meta, show)
        else:
            continue
        if got["episode"] and dict(art, **got["episode"]) != art:
            meta["artwork"] = dict(art, **got["episode"])
            changed = True
        if got["series"] and dict(kept, **got["series"]) != kept:
            data["series"].setdefault(name, {})["art"] = dict(kept, **got["series"])
            changed = True
        # What was owed and has really arrived, rather than what we asked for.
        mine = [key for key in wants
                if got["episode"].get(key) and xbmcvfs.exists(got["episode"][key])]
        shared = [key for key in lacks
                  if got["series"].get(key) and xbmcvfs.exists(got["series"][key])]
        if mine:
            filled += 1
        if mine or shared:
            in_a_row = 0
            continue
        in_a_row += 1
        if in_a_row >= _ART_GIVE_UP:
            log(f"[art] gave up after {in_a_row} failures in a row")
            break
    if changed:
        _write_index(data)
    log(f"[art] filled in {filled} of {len(rows)} row(s)")
    return filled


def refresh_artwork(progress=None):
    """Forget a picture whose link has moved, so it is fetched again."""
    from .provider_api import _fetch_provider_meta

    data = read_index()
    rows = [(p, m) for p, m in data["files"].items() if m.get("episode_id")]
    shows, moved = {}, 0
    for number, (path, meta) in enumerate(rows, 1):
        if progress:
            progress.update(int(number * 100 / max(len(rows), 1)), "Artwork",
                            meta.get("episode_title", ""))
        series_id = meta.get("series_id") or ""
        if series_id not in shows:
            shows[series_id] = ((_fetch_provider_meta("series", series_id) or {})
                                if series_id else {})
        show = shows[series_id]
        if not show:
            continue
        video = _video_entry(show, meta["episode_id"])
        season = next((s.get("poster") for s in show.get("seasons", ())
                       if s.get("season") == _season_of(meta) and s.get("poster")), "")
        art = dict(meta.get("artwork") or {})
        for key, now in (("thumb", video.get("thumbnail") or ""),
                         ("season_poster", season)):
            if not now or now == meta.get(key):
                continue
            meta[key] = now
            old = art.pop(key, "")
            if old:
                xbmcvfs.delete(old)
            moved += 1
        meta["artwork"] = art

    for name, entry in data["series"].items():
        show = next((s for s in shows.values() if s and s.get("name") == name), {})
        kept = dict(entry.get("art") or {})
        for key, _ in _SERIES_ART:
            now = (show or {}).get(key) or ""
            if not now or now == entry.get(key):
                continue
            entry[key] = now
            old = kept.pop(key, "")
            if old:
                xbmcvfs.delete(old)
            moved += 1
        if entry.get("art") is not None:
            entry["art"] = kept

    if moved:
        _write_index(data)
        fill_artwork(progress=progress)
    log(f"[art] {moved} picture link(s) had moved")
    return moved


def sweep_artwork():
    """Our own art folder, cleared of pictures nothing points at."""
    here = _art_dir()
    try:
        _, names = xbmcvfs.listdir(here)
    except Exception as exc:
        log(f"[art] could not read {here!r}: {exc}")
        return 0
    data = read_index()
    keep = {p for m in data["files"].values()
            for p in (m.get("artwork") or {}).values() if p}
    keep |= {p for entry in data["series"].values()
             for p in (entry.get("art") or {}).values() if p}
    gone = 0
    for name in names:
        if here + name not in keep and xbmcvfs.delete(here + name):
            gone += 1
    if gone:
        log(f"[art] removed {gone} picture(s) nothing points at")
    return gone


def fetch_artwork(_params=None):
    """Fill in the pictures every episode is missing, and tidy up after it."""
    if download_queue.busy():
        _said("Artwork", "Wait for the downloads to finish first")
        return
    if not read_index()["files"]:
        _said("Artwork", "Nothing downloaded or imported yet")
        return

    progress = xbmcgui.DialogProgress()
    progress.create("Artwork", "Looking for missing pictures")
    try:
        filled = fill_artwork(progress=progress)
        swept = sweep_artwork()
    finally:
        progress.close()

    lines = [f"{filled} episode(s) given their pictures"]
    if swept:
        lines.append(f"{swept} picture(s) nothing wanted were removed")
    write_report("Artwork", lines)
    _said("Artwork", chr(10).join(lines))
    refresh_container()


# A public repo, so checking it costs our own server nothing.
_TREE_URL = ("https://api.github.com/repos/6ip/onepace-streams/git/trees/"
             "main?recursive=1")
_STREAM_URL = "https://6ip.github.io/onepace-streams/stream/{path}.json"

# A few arcs sit in subfolders; the rest are at the root of /stream.
_STREAM_DIRS = (("MUHN_", "Muhn"), ("ONIG_", "ONIG"),
                ("KUMA_SHAVED_", "KUMA_SHAVED"),
                ("special_", "Specials"), ("fools", "Specials"))


def stream_path(episode_id):
    # The meta prefixes some catalogs with "pp_" and leaves Muhn and ONIG bare.
    if episode_id.startswith("pp_"):
        episode_id = episode_id[3:]
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


# What our own naming produces: "6x05 - Arlong Park (Extended).mkv".
_NAMED = re.compile(r"^(\d+)x(\d+) - (.+?)(?: \(([^)]+)\))?$")
_VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm")


def _profile_root():
    """Where downloads start out, before anyone chooses somewhere else."""
    return _profile().rstrip("/" + chr(92)).replace(chr(92), "/") + "/downloads/"


def _roots():
    """Where downloads can be, not only where they go now."""
    here, was = folder(), _profile_root()
    return [here] + ([was] if was != here else [])


def _root_of(path):
    """The download root this file sits under, including one we have left."""
    here = _normalised(path)
    marker = "/" + CONTAINER + "/"
    if marker in here:
        # The last one: a folder of theirs may carry the name as well.
        return here[:here.rindex(marker) + len(marker)]
    was = _profile_root()
    return was if here.startswith(was) else ""


def _walk_downloads():
    """Every video file under a download folder, with what its name says."""
    found = []
    for root in _roots():
        found.extend(_walk_one(root))
    return found


def _walk_one(root):
    found = []
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
                season, episode, title, cut = match.groups()
                found.append({"path": here + name, "series_name": series,
                              "season": int(season), "episode": int(episode),
                              "title": title, "cut": cut or "",
                              "variant": (cut or "").lower()})
    return found


def rescan(_params=None):
    """Rebuild the index from what is actually on disk."""
    # A transfer in flight writes a .part this would adopt half-written.
    if download_queue.busy():
        _said("Rescan", "Wait for the downloads to finish first")
        return
    from .provider_api import _fetch_provider_meta

    data = _sweep(read_index())
    dead = _dead_imported(data["files"])
    known = set(data["files"])
    try:
        on_disk = _walk_downloads()
    except Exception as exc:
        log(f"[rescan] could not read the download folder: {exc}")
        _said("Rescan", "Could not read the download folder")
        return

    missing = [f for f in on_disk if f["path"] not in known]
    if not missing and not dead:
        _said("Rescan", f"All {len(on_disk)} file(s) already listed")
        return

    asking = []
    if missing:
        asking.append(f"{len(missing)} file(s) here are not in the list.")
    if dead:
        asking.append(f"{len(dead)} imported file(s) are no longer where "
                      f"the list says.")
    if not xbmcgui.Dialog().yesno(
            "Rescan", chr(10).join(asking) + chr(10) + chr(10) + "Tidy the list up?",
            nolabel="Cancel", yeslabel="Tidy"):
        return

    if dead:
        _remove_rows(dead)
        data = read_index()
        log(f"[rescan] forgot {len(dead)} imported file(s) that had moved")

    added, unknown = 0, 0
    progress = xbmcgui.DialogProgressBG()
    if missing:
        progress.create("Rescanning downloads")
    ids = _series_ids() if missing else {}
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
        # "Farewell (Filler)" is the episode's name, not a cut of it.
        real = safe_name(video.get("name") or video.get("title") or "")
        if entry["cut"] and f"{entry['title']} ({entry['cut']})" == real:
            log(f"[rescan] {real!r} is the title, not a cut")
            entry["variant"] = ""
        _remember(entry["path"], _rebuilt(entry, video, meta), meta,
                  _sidecars(entry["path"]),
                  _adopted_art(entry["path"], entry["season"]))
        added += 1
    progress.close()

    lines = [f"Added {added} of {len(missing)} found"] if missing else []
    if unknown:
        lines.append(f"{unknown} could not be matched to an episode")
    if dead:
        lines.append(f"{len(dead)} imported file(s) had moved and were forgotten")
    write_report("Rescanned downloads", lines)
    done = [f"Added {added} download(s)"] if added else []
    if dead:
        done.append(f"Forgot {len(dead)} that had moved")
    _said("Rescan", chr(10).join(done) if done else "Nothing could be added")
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


def _folders_present(paths):
    """Whether each file's folder is attached, checked once per folder."""
    seen = {}
    for path in paths:
        where = path.rsplit("/", 1)[0] + "/" if "/" in path else path
        if where not in seen:
            seen[where] = xbmcvfs.exists(where)
    return seen


def _dead_imported(files):
    """Imported rows whose file has really gone, not merely gone offline."""
    gone = [p for p, m in files.items()
            if m.get("imported") and not xbmcvfs.exists(p)]
    attached = _folders_present(gone)
    return [p for p in gone
            if attached.get(p.rsplit("/", 1)[0] + "/", False)]


def _size_on_disk(path):
    try:
        return int(xbmcvfs.Stat(path).st_size())
    except Exception as exc:
        log(f"[rescan] could not measure {path!r}: {exc}")
        return 0


def _sidecars(path):
    """Subtitles already sitting beside a file we are adopting."""
    directory = _subs_dir(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        _, names = xbmcvfs.listdir(directory)
    except Exception:
        return ()
    # Plain before tagged, which is the order the feed itself lists them in.
    found = sorted(n for n in names
                   if n.startswith(stem + ".") and n.lower().endswith(".vtt"))
    return tuple(directory + n for n in sorted(found, key=lambda n: n.count(".")))


def _adopted_art(path, season):
    """Pictures already beside a file the rescan just found."""
    def _first(stem):
        for ext in _ART_EXTS:
            if xbmcvfs.exists(stem + ext):
                return stem + ext
        return ""

    series_dir = _series_dir(path)
    episode = {
        "thumb": _first(os.path.splitext(_normalised(path))[0] + "-thumb"),
        "season_poster": _first(
            f"{series_dir}{_season_art_name(season)}-poster"),
    }
    series = {key: _first(series_dir + name) for key, name in _SERIES_ART}
    return {"episode": {k: v for k, v in episode.items() if v},
            "series": {k: v for k, v in series.items() if v}}


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
        "logo": meta.get("logo", ""), "duration": 0,
        "video_size": _size_on_disk(entry["path"]),
    }


def check_updates(_params=None):
    """See which downloads the repo has re-released since we fetched them."""
    data = read_index()
    # Never fetched, so there is no release of ours to compare.
    held = [(path, meta) for path, meta in data["files"].items()
            if meta.get("episode_id") and not meta.get("imported")]
    if not held:
        _said("Check for Updates", "Nothing downloaded yet")
        return

    progress = xbmcgui.DialogProgressBG()
    progress.create("Checking downloads")
    changed, checked, failed = 0, 0, 0
    try:
        hashes = _tree_hashes()
    except Exception as exc:
        progress.close()
        log(f"[updates] could not read the file list: {exc}")
        _said("Check for Updates", "Could not check for updates")
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
        # An edited file is not a new release; the infoHash decides.
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
    _write_index(data)
    pictures = refresh_artwork(progress)
    progress.close()

    lines = [f"{changed} of {len(held)} look re-released",
             f"{checked} needed a closer look, {len(held) - checked} unchanged"]
    if pictures:
        lines.append(f"{pictures} picture(s) had changed and were fetched again")
    if failed:
        lines.append(f"{failed} could not be checked")
    write_report("Checked downloads", lines)
    _said("Check for Updates", f"{changed} update(s) found" if changed else "Everything is current")
    refresh_container()


def _prune(path, extra=""):
    """Drop the season and series folders once nothing is left in them."""
    # The folder being left behind counts too, or its shells stand forever.
    roots = [r.rstrip("/") for r in _roots() + ([extra] if extra else [])]
    directory = os.path.dirname(_normalised(path)).rstrip("/")
    # Series and season, and nothing above them, however the paths compare.
    for _ in range(2):
        if not directory or directory in roots or not any(
                directory.startswith(r + "/") for r in roots):
            return
        if not xbmcvfs.rmdir(directory):
            return
        log(f"[downloads] removed the empty {directory!r}")
        directory = os.path.dirname(directory).rstrip("/")


# Kodi flips right-to-left text for display on its way to the shell, and a
# path cannot survive that. Hebrew, Arabic, Syriac, Thaana and their forms.
_RTL = (("\u0590", "\u08ff"), ("\ufb1d", "\ufdff"), ("\ufe70", "\ufeff"))


def _right_to_left(text):
    """Whether handing this path to the shell would reorder it."""
    return any(low <= ch <= high for ch in text for low, high in _RTL)


# The setting stores an index, like every other enum we ship.
_BROWSE_WITH = {"0": "auto", "1": "kodi", "2": "system"}


# Whatever opens a folder on this desktop.
_FILE_MANAGERS = (
    ("System.Platform.Windows", 'explorer.exe "{win}"'),
    ("System.Platform.OSX", 'open "{path}"'),
    ("System.Platform.Linux", 'xdg-open "{path}"'),
)


def _ask_where(series, season):
    """The folder they meant, out of the ones this row actually spans."""
    files = read_index()["files"]
    paths = [p for p, meta in files.items()
             if (meta.get("series_name") or "Downloads") == series
             and (season is None or _season_of(meta) == int(season))]
    here = [spot for spot in _destinations(paths, files)
            if xbmcvfs.exists(spot[0] + "/")]
    if not here:
        _notify_error("That folder is not there any more")
        return ""
    if len(here) == 1:
        return here[0][0]
    # Named by side first: a path in another script may draw as empty boxes.
    pick = xbmcgui.Dialog().select("Which folder?", [
        f"{'Downloaded' if mine else 'Imported'} — {where}"
        f"  [COLOR FF888899]({count})[/COLOR]" for where, count, mine in here])
    return here[pick][0] if pick >= 0 else ""


def browse(params):
    """Open the folder in the desktop's file manager, or Kodi's if there is none."""
    directory = _normalised(params.get("dir", "")).rstrip("/")
    if not directory and params.get("series") is not None:
        # It says what is wrong, or they backed out of choosing.
        directory = _ask_where(params["series"], params.get("season"))
        if not directory:
            return
    if not directory:
        path = params.get("path") or path_for(params.get("episode_id", ""))
        directory = os.path.dirname(_normalised(path)) if path else ""
    if not directory or not xbmcvfs.exists(directory + "/"):
        _notify_error("That folder is not there any more")
        return

    # Only Windows converts the command for the shell, so only it is affected.
    flipped = (_right_to_left(directory)
               and xbmc.getCondVisibility("System.Platform.Windows"))
    choice = _BROWSE_WITH.get(get_setting("browse_with"), "auto")
    # Automatic keeps the desktop's own, except where it could not work.
    desktop = choice != "kodi" and (choice == "system" or not flipped)
    for condition, template in _FILE_MANAGERS:
        if desktop and xbmc.getCondVisibility(condition):
            command = template.format(path=directory,
                                      win=directory.replace("/", chr(92)))
            log(f"[downloads] opening {directory!r} with the file manager")
            xbmc.executebuiltin(f"System.Exec({command})")
            return

    log(f"[downloads] showing {directory!r} in Kodi"
        + (", as asked" if choice == "kodi" else
           ", which reads right to left" if flipped else ", with no file manager"))
    # Quoted, or a folder with a comma in its name is cut in half.
    xbmc.executebuiltin(f'ActivateWindow(Videos,"{directory}",return)')


_REPORT = "last_download.txt"

_REPORT_KEEP = 50
# One import summary runs to forty lines where a download is one, so a count
# on its own is not a size.
_REPORT_BYTES = 60 * 1024


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
    """Add a summary at the top, keeping the last few."""
    import time

    when = time.strftime("%d %B %Y, %H:%M") + time.strftime(" (%I:%M %p)").replace(" 0", " ")
    entry = [f"[B]{heading}[/B]  [COLOR FF888899]{when}[/COLOR]", ""]
    entry += [f"  {chr(8226)}  {line}" for line in lines]

    older = [block for block in _read_report().split(chr(10) * 3) if block.strip()]
    kept = [chr(10).join(entry)]
    for block in older[:_REPORT_KEEP - 1]:
        if len((chr(10) * 3).join(kept + [block]).encode("utf-8")) > _REPORT_BYTES:
            break
        kept.append(block)
    body = (chr(10) * 3).join(kept)
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

    # The same window What's New uses, without the donation button.
    from .changelog import show_text
    show_text(text, "", heading="Last Download Summary", asking=False)


def imported(path, files=None):
    """Whether this one was adopted from the viewer's own disk."""
    rows = files if files is not None else read_index()["files"]
    return bool(rows.get(path, {}).get("imported"))


def _remove(paths):
    """Delete what we fetched, and only forget what we adopted."""
    data = read_index()
    removed = 0
    for path in paths:
        if imported(path, data["files"]):
            # Their file is theirs; the pictures we fetched are ours to drop.
            _drop_artwork(data, data["files"].pop(path, None) or {})
            removed += 1
            continue
        if not xbmcvfs.exists(path) or xbmcvfs.delete(path):
            row = data["files"].get(path, {})
            for sidecar in row.get("subtitles", ()):
                xbmcvfs.delete(sidecar)
            # Empty now, and rmdir refuses it otherwise.
            xbmcvfs.rmdir(_subs_dir(path))
            data["files"].pop(path, None)
            # Before the prune, or rmdir refuses the folder they sit in.
            _drop_artwork(data, row)
            _prune(path, _root_of(path))
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

    # Say what will actually happen: an imported file is not ours to delete.
    adopted = [p for p in targets if imported(p, files)]
    question, yeslabel = f"Remove {what} from disk?", "Delete"
    if len(adopted) == len(targets):
        question = (f"Remove {what} from the list?{chr(10)}{chr(10)}"
                    "The files stay where they are.")
        yeslabel = "Remove"
    elif adopted:
        question = (f"Remove {what}?{chr(10)}{chr(10)}"
                    f"{len(targets) - len(adopted)} will be deleted. "
                    f"{len(adopted)} imported only leave the list.")
    if not xbmcgui.Dialog().yesno("Delete", question,
                                  nolabel="Keep", yeslabel=yeslabel):
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


def _moved_to(picture, target):
    """The new home if it got there, and the old one if it did not."""
    if xbmcvfs.exists(target) or _relocate(picture, target):
        return target
    log(f"[art] could not move {picture!r}")
    return picture


def _move_artwork(artwork, was, now):
    """A picture only helps where the video is, so it goes with it."""
    art = artwork or {}
    moved = {}
    thumb = art.get("thumb")
    if thumb:
        tail = _normalised(thumb)[len(os.path.splitext(_normalised(was))[0]):]
        moved["thumb"] = _moved_to(thumb, os.path.splitext(_normalised(now))[0] + tail)
    poster = art.get("season_poster")
    if poster:
        moved["season_poster"] = _moved_to(
            poster, _series_dir(now) + os.path.basename(_normalised(poster)))
    return moved


def _move_series_art(data, series, now):
    """The series' own pictures, once one of its episodes has moved."""
    art = (data["series"].get(series) or {}).get("art") or {}
    if not art:
        return
    data["series"][series]["art"] = {
        key: _moved_to(picture, _series_dir(now) + os.path.basename(_normalised(picture)))
        for key, picture in art.items() if picture}


def _move_subtitles(paths, was, now):
    """Move each sidecar into the Subs folder beside the video's new home."""
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
    """Bring everything already downloaded under the current folder."""
    # A transfer in flight would finish into the folder just emptied.
    if download_queue.busy():
        _said("Move Downloads", "Wait for the downloads to finish first")
        return
    data = read_index()
    planned = []
    for path, meta in data["files"].items():
        # An imported file sits where its owner put it.
        if meta.get("imported"):
            continue
        directory, name = _target(dict(meta, filename=path))
        if directory + name != path and xbmcvfs.exists(path):
            planned.append((path, directory, name))

    if not planned:
        _said("Move Downloads", "Everything is already in the download folder")
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
    moved, failed, left = 0, 0, set()
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
        entry["artwork"] = _move_artwork(entry.get("artwork"), path, destination)
        _move_series_art(data, entry.get("series_name") or "Downloads", destination)
        data["files"][destination] = entry
        was = _root_of(path)
        _prune(path, was)
        if was:
            left.add(was)
        moved += 1
    progress.close()

    # Only ever rmdir: anything of theirs in there keeps its folder, and the
    # summary says so rather than leaving them to find it.
    stayed = 0
    for root in sorted(left - set(_roots())):
        if xbmcvfs.rmdir(root.rstrip("/")):
            log(f"[downloads] removed the empty {root!r}")
        else:
            stayed += 1
            log(f"[downloads] {root!r} still has files in it")

    if moved:
        _write_index(data)
    log(f"[downloads] moved {moved}, failed {failed}")
    note = ""
    if stayed:
        note = (chr(10) + chr(10)
                + f"{stayed} old folder{'s' if stayed != 1 else ''} still "
                  f"{'have' if stayed != 1 else 'has'} other files in "
                  f"{'them' if stayed != 1 else 'it'}.")
    if failed:
        _said("Move Downloads", f"Moved {moved}, could not move {failed}" + note)
    else:
        _said("Move Downloads",
              f"Moved {moved} file{'s' if moved != 1 else ''}" + note)
    refresh_container()


def _sweep(data):
    """Forget rows whose file someone deleted outside the add-on."""
    gone = [p for p, m in data["files"].items()
            if not m.get("imported") and not xbmcvfs.exists(p)]
    if gone:
        for p in gone:
            data["files"].pop(p, None)
        _write_index(data)
        log(f"[downloads] dropped {len(gone)} entries with no file left")
    return data


def _series_order(data):
    """Name to catalog position, so On Device reads like every other list."""
    known = {name: meta["order"] for name, meta in data["series"].items()
             if isinstance(meta.get("order"), int)}
    # Only a row that names a series can be looked up.
    named = {(m.get("series_name") or "Downloads"): m.get("series_id")
             for m in data["files"].values() if m.get("series_id")}
    if all(name in known for name in named):
        return known
    from .provider_api import series_order
    ranks = series_order()
    if not ranks:
        return known
    changed = False
    for name, series_id in named.items():
        if name in known or series_id not in ranks:
            continue
        known[name] = ranks[series_id]
        data["series"].setdefault(name, {})["order"] = ranks[series_id]
        changed = True
    if changed:
        _write_index(data)
        log(f"[downloads] noted where {len(known)} series sit in the catalog")
    return known


def _shared_folder(paths):
    """The folder to open for a row, which is where most of it lives."""
    folders = [_normalised(p).rsplit("/", 1)[0] for p in paths if p]
    if not folders:
        return ""
    tally = {}
    for f in folders:
        tally["/".join(f.split("/")[:-1])] = tally.get("/".join(f.split("/")[:-1]), 0) + 1
    home = max(tally, key=lambda k: (tally[k], -list(tally).index(k)))
    seasons = {f for f in folders if f == home or f.startswith(home + "/")}
    # All of it in one season: open that. Spread over several: open the series.
    return seasons.pop() if len(seasons) == 1 else home

def _destinations(paths, files=None):
    """Folders worth opening, each with how many are in it and whose it is."""
    rows = files if files is not None else read_index()["files"]
    mine = {}
    for path in paths:
        if path and not imported(path, rows):
            mine.setdefault(_root_of(path), []).append(path)
    found = []
    for group in mine.values():
        shared = _shared_folder(group)
        if shared:
            found.append((shared, len(group), True))
    found.sort()
    theirs = {}
    for path in paths:
        if path and imported(path, rows):
            here = _normalised(path).rsplit("/", 1)[0]
            theirs[here] = theirs.get(here, 0) + 1
    return found + [(where, count, False) for where, count in sorted(theirs.items())]


def _empty(message):
    """Dressed like the root menu, since that is what an empty section is."""
    media = f"special://home/addons/{ADDON_ID}/resources/skins/Default/media"
    icon = f"{media}/info.png"
    set_content("")
    item = xbmcgui.ListItem(label="Nothing downloaded yet", offscreen=True)
    item.setArt({"icon": icon, "thumb": icon, "poster": icon, "banner": icon,
                 "landscape": icon,
                 "fanart": background()})
    item.getVideoInfoTag().setPlot(message)
    _add_directory_items([(build_url("list_downloads"), item, False)])
    end_directory()


def _counts(item, metas):
    """The same properties the season list sets, so skins read them the same."""
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
    """Play a downloaded file through the add-on, not straight off disk."""
    return build_url("play_video", **_play_fields(path, meta))


def _size_label(meta):
    """Decimal MB, which is what the provider prints beside its own streams."""
    size = meta.get("video_size") or 0
    return f"{size / 1_000_000:.2f} MB" if size else ""


def describe(meta):
    """What this copy is, for the row in the picker and the player's OSD."""
    parts = ["Imported" if meta.get("imported") else "Downloaded",
             variant_label(meta.get("variant"))]
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
              # Without these the info panel falls back to "Not available".
              "episode_plot": video.get("overview", ""),
              # Our own header, then what the provider said when we fetched it.
              "stream_name": describe(meta),
              "stream_desc": meta.get("stream_desc", "")}
    return {k: v for k, v in fields.items() if v}


# In front, not after: a list truncates the tail, and the eye scans.
_T_HELD, _T_STALE, _T_MINE = "[D]", "[!]", "[I]"
_MARK = f"[COLOR FFA8E6A3][B]{_T_HELD}[/B][/COLOR]"
_MARK_PLAIN = f"[COLOR FFA8E6A3]{_T_HELD}[/COLOR]"
# Held, but the repo has moved on.
_STALE = f"[COLOR FFFFD166][B]{_T_STALE}[/B][/COLOR]"
_STALE_PLAIN = f"[COLOR FFFFD166]{_T_STALE}[/COLOR]"
# Theirs, not ours: Delete only forgets it, and no update applies to it.
_MINE = f"[COLOR FF8CC8FF][B]{_T_MINE}[/B][/COLOR]"
_MINE_PLAIN = f"[COLOR FF8CC8FF]{_T_MINE}[/COLOR]"


def enabled():
    """Off until asked for, so an update does not rearrange anyone's menu."""
    return get_setting("downloads_enabled") == "true"


def outdated_ids():
    """Episode ids whose release moved on since we fetched them."""
    if not enabled():
        return set()
    return {m.get("episode_id") for m in read_index()["files"].values()
            if m.get("outdated") and m.get("episode_id")}


def held_cut(episode_id):
    """The flagged copy's cut, spelled as the provider spells it, or nothing."""
    if not episode_id or not enabled():
        return ""
    for meta in read_index()["files"].values():
        if (meta.get("episode_id") == episode_id and meta.get("outdated")
                and not meta.get("imported")):
            return variant_of(meta)
    return ""


def mark(label, episode_id, on_disk, plain=False, stale=(), mine=()):
    """Flag a title we hold, one worth fetching again, or one of their own."""
    if not episode_id or episode_id not in on_disk:
        return label
    if not enabled() or get_setting("download_marker") == "false":
        return label
    if episode_id in stale:
        return f"{_STALE_PLAIN if plain else _STALE} {label}"
    if episode_id in mine:
        return f"{_MINE_PLAIN if plain else _MINE} {label}"
    return f"{_MARK_PLAIN if plain else _MARK} {label}"


# The version picker stores an index, the same one Preferred Version uses.
_PREFERRED_VARIANT = {"1": "standard", "2": "extended"}


def copies_of(episode_id):
    """Every cut of this episode we hold, standard first."""
    if not episode_id or not enabled():
        return []
    held = [(path, meta) for path, meta in read_index()["files"].items()
            if meta.get("episode_id") == episode_id and xbmcvfs.exists(path)]
    # Ours before theirs: it sits on storage we keep, and updates reach it.
    return sorted(held, key=lambda kv: (_variant_order(kv[1].get("variant")),
                                        bool(kv[1].get("imported"))))


def path_for(episode_id):
    """Where this episode lives on disk, if we hold it at all."""
    held = copies_of(episode_id)
    return held[0][0] if held else ""


def local_playback(episode_id):
    """play_video params for the copy on disk, when we should prefer it."""
    if not enabled():
        return None
    if get_setting("prefer_downloads") == "false":
        # Says which reason it is, so a failed stream is not read as a fault.
        log("[downloads] streaming instead: Play the Downloaded Copy is off")
        return None
    held = copies_of(episode_id)
    if not held:
        if any(m.get("episode_id") == episode_id
               for m in read_index()["files"].values()):
            log(f"[downloads] the list has {episode_id!r} but its file is gone")
        return None
    # Holding both cuts, honour the same preference the stream picker uses.
    wanted = _PREFERRED_VARIANT.get(get_setting("preferred_version"))
    if wanted and len(held) > 1:
        held = [kv for kv in held if cut_of(kv[1].get("variant")) == wanted] or held
    path, meta = held[0]
    return _play_fields(path, meta)


def local_subtitles(path):
    """The sidecars we saved for this file, in the order the feed listed them."""
    kept = [p for p in read_index()["files"].get(path, {}).get("subtitles", ())
            if xbmcvfs.exists(p)]
    return kept


def offer_manual(episode_id, on_disk):
    """True when pressing play would silently use the copy on disk."""
    return (enabled() and episode_id in on_disk
            and get_setting("prefer_downloads") != "false")


def local_options(episode_id):
    """Each cut on disk, offered alongside the streams."""
    options = []
    for path, meta in copies_of(episode_id):
        tag = _MINE_PLAIN if meta.get("imported") else _MARK_PLAIN
        options.append((f"{tag} {describe(meta)}", _play_fields(path, meta)))
    return options


def downloaded_ids():
    """Episode ids we hold on disk, for marking them in the ordinary lists."""
    if not enabled():
        return set()
    return {m.get("episode_id") for m in read_index()["files"].values()
            if m.get("episode_id")}


def imported_ids():
    """Episode ids held only as the viewer's own files."""
    if not enabled():
        return set()
    ours, theirs = set(), set()
    for meta in read_index()["files"].values():
        episode_id = meta.get("episode_id")
        if episode_id:
            (theirs if meta.get("imported") else ours).add(episode_id)
    return theirs - ours


def _season_of(meta):
    season = meta.get("season")
    return int(season) if str(season).lstrip("-").isdigit() else 0


def _local_art(show):
    """A series' own pictures from disk, where we kept them."""
    art = (show or {}).get("art") or {}
    if not art:
        return show
    local = dict(show)
    for key, picture in art.items():
        if picture and xbmcvfs.exists(picture):
            local[key] = picture
    return local


def _drawn_art(meta):
    """This episode's own pictures, only while they are still on disk."""
    art = meta.get("artwork") or {}
    thumb, poster = art.get("thumb") or "", art.get("season_poster") or ""
    return (thumb if thumb and xbmcvfs.exists(thumb) else "",
            poster if poster and xbmcvfs.exists(poster) else "")


def _season_art(meta_for_series, metas):
    """Prefer the season poster we kept, then the one the provider gave us."""
    for meta in metas:
        kept = (meta.get("artwork") or {}).get("season_poster")
        if kept and xbmcvfs.exists(kept):
            return kept
    for entry in meta_for_series.get("seasons", ()):
        if entry.get("season") == _season_of(metas[0]) and entry.get("poster"):
            return entry["poster"]
    return metas[0].get("season_poster")


def list_downloads(params=None):
    """Two ways in: what is transferring now, and what is already here."""
    params = params or {}
    # Menus elsewhere link straight to a series or season on disk.
    if params.get("series") is not None or params.get("season") is not None:
        list_on_device(params)
        return

    media = f"special://home/addons/{ADDON_ID}/resources/skins/Default/media"
    fanart = background()

    def _nav(label, icon):
        item = xbmcgui.ListItem(label=label, offscreen=True)
        item.setArt({"icon": icon, "thumb": icon, "poster": icon,
                     "fanart": fanart, "banner": icon, "landscape": icon})
        item.getVideoInfoTag().setPlot("​")
        return item

    running = len(download_queue.snapshot())
    set_content("")
    set_category("Downloads")
    _add_directory_items([
        # Not a folder: it opens a window rather than navigating.
        (build_url("open_downloads_manager"),
         _nav(f"Downloading  ({running})" if running else "Downloading",
              f"{media}/downloading.png"), False),
        (build_url("list_on_device"), _nav("On Device", f"{media}/library.png"), True),
        (build_url("list_manage"), _nav("Manage", f"{media}/settings.png"), True),
    ])
    # Never cached: the count above moves while you are looking at it.
    end_directory()


def list_manage(params=None):
    """The jobs from the settings tab, in the menu Downloads already lives in."""
    media = f"special://home/addons/{ADDON_ID}/resources/skins/Default/media"
    icon = f"{media}/tools.png"
    fanart = background()

    def _job(label, url, says, art=icon):
        item = xbmcgui.ListItem(label=label, offscreen=True)
        item.setArt({"icon": art, "thumb": art, "poster": art,
                     "fanart": fanart, "banner": art, "landscape": art})
        item.getVideoInfoTag().setPlot(says)
        return (url, item, False)

    set_content("")
    set_category("Manage")
    _add_directory_items([
        # Reading what the last run did comes before starting another.
        _job("Last Download Summary", build_url("show_download_report"),
             "What the last downloads, imports and scans actually did.",
             art=f"{media}/info.png"),
        _job("Import from a Folder", build_url("import_folder"),
             "Add episodes you already have, from anywhere on your disk."),
        _job("Rescan Download Folder", build_url("rescan_downloads"),
             "Find downloads the list has lost, and forget ones that moved."),
        _job("Check for Updates", build_url("check_updates"),
             "See which downloads One Pace has re-released since."),
        _job("Fetch Missing Artwork", build_url("fetch_artwork"),
             "Keep the posters and thumbnails beside what you already have."),
        _job("Move Existing Downloads", build_url("move_downloads"),
             "Bring everything already downloaded into the current folder."),
    ])
    # Never cached: each of these changes what the others would report.
    end_directory()


def list_on_device(params=None):
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
        by_series, paths = {}, {}
        for path, meta in files.items():
            name = meta.get("series_name") or "Downloads"
            by_series.setdefault(name, []).append(meta)
            paths.setdefault(name, []).append(path)
        # Anything the catalog does not name falls to the back, by name.
        rank = _series_order(data)
        order = sorted(by_series, key=lambda n: (rank.get(n, 10 ** 6), n.lower()))
        if len(by_series) == 1:
            series = order[0]
        else:
            set_content("tvshows")
            set_category("Downloads")
            items = []
            for name in order:
                metas = by_series[name]
                show = _local_art(series_meta.get(name, {}))
                item = xbmcgui.ListItem(label=name, offscreen=True)
                tags = item.getVideoInfoTag()
                _set_video_tags(tags, show, name)
                tags.setTvShowTitle(name)
                tags.setMediaType("tvshow")
                _set_show_tags(tags, show, actors=_cast_list(show))
                _set_art(item, show)
                _counts(item, metas)
                spots = _destinations(paths[name], files)
                item.addContextMenuItems([
                    (f"[B]Browse Folder{'s' if len(spots) > 1 else ''}[/B]",
                     f"RunPlugin({build_url('browse_download', series=name)})"),
                    ("[B]Delete Series[/B]",
                     f"RunPlugin({build_url('delete_download', series=name)})"),
                ])
                items.append((build_url("list_on_device", series=name), item, True))
            _add_directory_items(items)
            end_directory()
            return

    mine = {p: m for p, m in files.items()
            if (m.get("series_name") or "Downloads") == series}
    if not mine:
        _empty("Nothing left under that name.")
        return
    show = _local_art(series_meta.get(series, {}))
    actors = _cast_list(show)

    if season is None:
        seasons, paths = {}, {}
        for path, meta in mine.items():
            number = _season_of(meta)
            seasons.setdefault(number, []).append(meta)
            paths.setdefault(number, []).append(path)
        if len(seasons) > 1:
            set_content("seasons")
            set_category(series)
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
                spots = _destinations(paths[number], files)
                item.addContextMenuItems([
                    (f"[B]Browse Folder{'s' if len(spots) > 1 else ''}[/B]",
                     f"RunPlugin({build_url('browse_download', series=series, season=number)})"),
                    (f"[B]Delete {label}[/B]",
                     f"RunPlugin({build_url('delete_download', series=series, season=number)})"),
                ])
                items.append((build_url("list_on_device", series=series, season=number),
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
    # Only an imported row can be missing here: the sweep drops any of ours.
    attached = _folders_present(chosen)
    set_content("episodes")
    label = "Specials" if int(season) == 0 else f"Season {season}"
    set_category(f"{series} - {label}")
    items = []
    for path, meta in sorted(chosen.items(),
                             key=lambda kv: (_season_of(kv[1]),
                                             int(kv[1].get("episode") or 0),
                                             _variant_order(kv[1].get("variant")))):
        number = int(meta.get("episode") or 0)
        title = meta.get("episode_title") or os.path.basename(path)
        label = _episode_label(title, int(season), number, meta.get("episode_id", ""))
        # Two cuts sit next to each other, so say which is which.
        if meta.get("variant") and meta["variant"] != "standard":
            label += f"  ({variant_label(meta['variant'])})"
        here = attached.get(path.rsplit("/", 1)[0] + "/", True)
        if not here:
            label = f"[COLOR FF888899]{label}  (drive not connected)[/COLOR]"
        item = xbmcgui.ListItem(label=label, offscreen=True)
        video = meta.get("video") or {}
        tags = item.getVideoInfoTag()
        tags.setMediaType("episode")
        tags.setTitle(title)
        tags.setTvShowTitle(series)
        tags.setSeason(int(season))
        tags.setEpisode(number)

        # The rest mirrors the episode list, off the same provider entry.
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
        held_thumb, held_poster = _drawn_art(meta)
        drawn = dict(video) if video else {}
        drawn["thumbnail"] = (held_thumb or drawn.get("thumbnail")
                              or meta.get("thumb", ""))
        _set_episode_art(item, drawn, show,
                         held_poster or meta.get("season_poster"))

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

        # Not playable while its drive is out, rather than a missing path.
        item.setProperty("IsPlayable", "true" if here else "false")
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
        # The same wording and jump My Lists uses.
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
