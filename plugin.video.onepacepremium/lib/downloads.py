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
from .utils import (ADDON_HANDLE, build_url, get_setting, log,
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


def folder():
    """Where episodes are kept. Blank setting falls back inside our profile."""
    chosen = get_setting("download_folder")
    path = xbmcvfs.translatePath(chosen) if chosen else _profile() + "downloads/"
    return path if path.endswith(("/", "\\")) else path + "/"


def is_downloadable(url):
    return url.startswith(_DOWNLOADABLE)


def safe_name(text, fallback="Unknown"):
    """Strip what Windows, Linux and macOS each refuse in a filename."""
    cleaned = _ILLEGAL.sub("", text or "").strip().strip(".")
    return cleaned[:120] or fallback


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
    name = f"{season}x{episode:02d} - {title}{ext}"
    return f"{folder()}{series}/Season {season:02d}/", name


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


def _remember(path, params, meta=None):
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


def download(params, meta=None):
    """Fetch one episode to disk. Runs for as long as the transfer takes."""
    url = params.get("video_url", "")
    if not is_downloadable(url):
        _notify_error("This stream cannot be downloaded")
        return

    directory, name = _target(params)
    destination = directory + name
    if xbmcvfs.exists(destination):
        if not xbmcgui.Dialog().yesno("Download", f"{name}\n\nis already here. Fetch it again?",
                                      nolabel="Keep", yeslabel="Replace"):
            return
    if not xbmcvfs.mkdirs(directory) and not xbmcvfs.exists(directory):
        _notify_error("Could not create the download folder")
        return

    progress = xbmcgui.DialogProgressBG()
    progress.create("Downloading", name)
    monitor = xbmc.Monitor()
    written, total = 0, 0
    try:
        response = session().get(url, stream=True, timeout=30)
        response.raise_for_status()
        total = int(params.get("video_size") or 0) or int(
            response.headers.get("Content-Length") or 0)
        with xbmcvfs.File(destination, "w") as handle:
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
        xbmcvfs.delete(destination)
        _notify_error(f"Download failed: {exc}")
        return

    progress.close()
    if total and written < total:
        log(f"[downloads] {name!r} stopped short: {written} of {total} bytes")
        xbmcvfs.delete(destination)
        _notify_error("Download ended early, nothing kept")
        return

    _remember(destination, params, meta)
    log(f"[downloads] saved {destination!r} ({written} bytes)")
    _notify_info(f"Downloaded {name}")
    refresh_container()


def _remove(paths):
    """Delete files and forget them. Returns how many actually went."""
    data = read_index()
    removed = 0
    for path in paths:
        if not xbmcvfs.exists(path) or xbmcvfs.delete(path):
            data["files"].pop(path, None)
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
    xbmcplugin.setContent(ADDON_HANDLE, "")
    item = xbmcgui.ListItem(label="Nothing downloaded yet", offscreen=True)
    item.getVideoInfoTag().setPlot(message)
    _add_directory_items([(build_url("list_downloads"), item, False)])
    end_directory()


def _counts(item, metas):
    """The same properties the season list sets, so skins read them the same."""
    item.setProperties({"TotalEpisodes": str(len(metas)),
                        "UnWatchedEpisodes": str(len(metas))})


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

    from .episode_routes import _episode_label

    chosen = {p: m for p, m in mine.items() if _season_of(m) == int(season)}
    xbmcplugin.setContent(ADDON_HANDLE, "episodes")
    label = "Specials" if int(season) == 0 else f"Season {season}"
    xbmcplugin.setPluginCategory(ADDON_HANDLE, f"{series} - {label}")
    items = []
    for path, meta in sorted(chosen.items(),
                             key=lambda kv: (_season_of(kv[1]),
                                             int(kv[1].get("episode") or 0))):
        number = int(meta.get("episode") or 0)
        title = meta.get("episode_title") or os.path.basename(path)
        item = xbmcgui.ListItem(
            label=_episode_label(title, int(season), number, meta.get("episode_id", "")),
            offscreen=True,
        )
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

        item.setProperty("IsPlayable", "true")
        # Kodi's own Play is not offered for a bare file path, so add ours.
        item.addContextMenuItems([
            ("[B]Play[/B]", f"PlayMedia({path})"),
            ("[B]Delete[/B]", f"RunPlugin({build_url('delete_download', path=path)})"),
        ])
        items.append((path, item, False))
    _add_directory_items(items)
    end_directory()
