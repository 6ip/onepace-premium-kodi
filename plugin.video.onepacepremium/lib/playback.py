import json
import os
import re
from concurrent import futures

import requests
import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs

from . import bookmarks as _bookmarks
from . import elementum as _elementum
from . import watched as _watched
from .utils import ADDON_HANDLE, HTTP_SESSION, get_setting, log

_SUBS_URL = "https://6ip.github.io/onepace-premium-subs/meta/subtitles.json"

# Safety cap only. Real failure is detected via Kodi's error dialog, so a
# plugin:// handoff can buffer as long as it needs.
_START_CAP_DIRECT = 60
_START_CAP_HANDOFF = 900


class _WatchMonitor(xbmc.Player):
    """xbmc.Player subclass that records whether playback ended naturally."""
    def __init__(self):
        super().__init__()
        self.ended_naturally = False

    def onPlayBackEnded(self):
        self.ended_naturally = True


# Mutable counter so each monitor session can detect when it has been superseded.
# Using a list avoids needing `global` declarations in nested functions.
_MONITOR_GEN = [0]




def _monitor_playback(series_id, episode_id, video_url=""):
    """Block until playback ends, then auto-mark the episode watched if appropriate.

    Called from play_video after setResolvedUrl so it runs inside the plugin
    action thread — keeping the process alive for the duration of playback.
    """
    _MONITOR_GEN[0] += 1
    my_gen = _MONITOR_GEN[0]
    # _MONITOR_GEN is per-process, so pid distinguishes duplicate invocations.
    pid = os.getpid()

    kodi_monitor = xbmc.Monitor()
    player = _WatchMonitor()
    last_time, total_time = 0.0, 0.0

    is_handoff = video_url.startswith("plugin://")
    cap = _START_CAP_HANDOFF if is_handoff else _START_CAP_DIRECT

    # Wait for playback. Kodi's error dialog is the real failure signal; the
    # cap is only a safety net so a wedged process can't live forever.
    waited = 0
    while not player.isPlaying():
        if kodi_monitor.waitForAbort(1):
            return
        waited += 1
        if xbmc.getCondVisibility("Window.IsTopMost(okdialog)"):
            log(f"[monitor] playback failed for {episode_id!r} "
                f"after {waited}s (error dialog shown)")
            return
        if waited >= cap:
            log(f"[monitor] playback never started for {episode_id!r} "
                f"after {waited}s, giving up (cap)")
            return

    log(f"[monitor] tracking {episode_id!r} (pid={pid}, gen={my_gen}"
        f"{', handoff' if is_handoff else ''}, waited={waited}s)")

    # If a bookmark exists, detect whether Kodi's native dialog resumed (seeked
    # near the saved position) or the user picked "Play from beginning" (stayed
    # near 0) — and clear the stale bookmark in the latter case.
    bm = _bookmarks.get(episode_id) if episode_id else None
    if bm and bm.get("pos", 0) > 60:
        bookmark_pos = bm["pos"]
        for _ in range(6):
            if kodi_monitor.waitForAbort(1):
                return
        try:
            if player.getTime() < bookmark_pos * 0.5:
                _bookmarks.clear(episode_id)
                log(f"[monitor] played from beginning, cleared stale bookmark for {episode_id!r}")
        except Exception:
            pass

    # Poll every 1 s; mark as soon as the 85% threshold is reached during playback
    marked = False
    while player.isPlaying():
        if kodi_monitor.waitForAbort(1):
            return
        try:
            last_time  = player.getTime()
            total_time = player.getTotalTime()
        except Exception:
            pass
        if not marked and series_id and episode_id and total_time > 0:
            pct = last_time / total_time
            if pct >= 0.85:
                marked = True
                _watched.set_episodes_watched(series_id, [episode_id], True)
                _bookmarks.clear(episode_id)

                log(f"[monitor] marked watched at {pct*100:.0f}% for {episode_id!r}")

    # If a newer monitor session has started, let it handle the rest
    if _MONITOR_GEN[0] != my_gen:
        log(f"[monitor] superseded by gen={_MONITOR_GEN[0]}, skipping for {episode_id!r}")
        return

    # If threshold wasn't hit during playback, decide now based on end-of-stream signals
    if not marked:
        pct = (last_time / total_time) if total_time > 0 else 0.0
        if player.ended_naturally or pct >= 0.85:
            _watched.set_episodes_watched(series_id, [episode_id], True)
            _bookmarks.clear(episode_id)
            _clear_kodi_bookmark(episode_id)
            log(f"[monitor] marked watched at end (natural={player.ended_naturally} pct={pct*100:.0f}%) for {episode_id!r}")
        elif last_time > 60 and total_time > 0:
            _bookmarks.set_bookmark(episode_id, last_time, total_time, series_id)
            log(f"[monitor] saved bookmark {episode_id!r} at {last_time:.1f}s / {total_time:.1f}s")
    # No Container.Refresh here — cacheToDisc=False means Kodi re-runs the plugin
    # fresh when the user navigates back, so the watched state is already correct.


# Kodi reads the language from the filename, and variant files end in an extra
# token ("_en_cc") it can't parse. Saving a copy as "CC.eng.vtt" fixes that.
_SUBS_CACHE = "special://temp/onepace-subs/"
_SUBS_BUDGET = 3.0          # seconds spent fetching before we stop
_VARIANT_RE = re.compile(r"\(([^)]*)\)\s*$")


def _cache_path(track, sub_id):
    """Where a variant would live, or None if it isn't one."""
    label = track.get("label")
    if not (label and track.get("url") and track.get("lang")):
        return None
    match = _VARIANT_RE.search(label)
    if not match:
        return None
    variant = re.sub(r"[^\w.-]", "_", match.group(1))
    return f"{_SUBS_CACHE}{sub_id}/{variant}.{track['lang']}.vtt"


def _fetch_subtitle(url, path):
    """Save one .vtt. Returns the path, or None so the caller falls back.

    Written under a .part name and renamed, so an interrupted write can never
    leave a truncated file that later looks cached.
    """
    name = path.rsplit("/", 1)[-1]
    partial = path + ".part"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        content = response.content
        if not content.lstrip()[:6] == b"WEBVTT":
            log(f"[subs] {name} is not a WEBVTT file, skipping")
            return None
        with xbmcvfs.File(partial, "w") as handle:
            handle.write(content)
        if not xbmcvfs.rename(partial, path):
            xbmcvfs.delete(partial)
            return None
        return path
    except Exception as exc:
        log(f"[subs] could not cache {name}: {exc}")
        try:
            xbmcvfs.delete(partial)
        except Exception:
            pass
        return None


def _subtitle_paths(subs, sub_id):
    """Local paths for variants, plain URLs for everything else.

    Variants are fetched in parallel; whatever misses the budget stays a URL.
    """
    targets = {}
    for index, track in enumerate(subs):
        path = _cache_path(track, sub_id)
        if path and not xbmcvfs.exists(path):
            targets[index] = (track["url"], path)

    done = {}
    if targets:
        xbmcvfs.mkdirs(f"{_SUBS_CACHE}{sub_id}/")
        # Not a `with` block: that waits for stragglers on exit, which would
        # defeat the budget. Any that land late still warm the cache.
        pool = futures.ThreadPoolExecutor(max_workers=8)
        try:
            pending = {pool.submit(_fetch_subtitle, url, path): index
                       for index, (url, path) in targets.items()}
            try:
                for future in futures.as_completed(pending, timeout=_SUBS_BUDGET):
                    result = future.result()
                    if result:
                        done[pending[future]] = result
            except futures.TimeoutError:
                pass
        except Exception as exc:
            log(f"[subs] caching stopped: {exc}")
        finally:
            pool.shutdown(wait=False)

    paths, cached = [], 0
    for index, track in enumerate(subs):
        path = done.get(index) or _cache_path(track, sub_id)
        if path and xbmcvfs.exists(path):
            paths.append(path)
            cached += 1
        else:
            paths.append(track["url"])

    if targets:
        missed = len(targets) - len(done)
        log(f"[subs] {sub_id}: {cached} variant(s) local"
            + (f", {missed} fell back (over {_SUBS_BUDGET:.0f}s)" if missed else ""))
    return paths


def _filter_subtitles(subs, sub_id):
    """Narrow the track list to the user's languages, dropping variants by default.

    Variant tracks (CC, DUB, ALT) are the ones Kodi shows as "Unknown", since
    their filenames carry an extra token it can't parse.
    """
    if not subs:
        return subs

    total = len(subs)
    # Defaults on: getSetting returns "" before the settings dialog is opened.
    if get_setting("sub_variants") == "false":
        subs = [s for s in subs if not s.get("label")]

    wanted = [c for c in get_setting("sub_langs").split(",") if c]
    if wanted:
        subs = [s for s in subs if s.get("lang") in wanted]

    log(f"Subtitles for {sub_id}: {len(subs)} of {total} tracks after filtering")
    return subs


def play_video(params):
    series_id = params.get("series_id", "")
    episode_id = params.get("episode_id", "")

    video_url = params["video_url"]

    # Resolve Elementum here rather than handing off, so our ListItem stays the
    # playing item. Any failure falls back to the plugin:// URL unchanged.
    if _elementum.direct_enabled() and video_url.startswith(_elementum.PLUGIN_PREFIX):
        direct_url = _elementum.resolve_plugin_url(video_url)
        if direct_url:
            video_url = direct_url

    imdb = params.get("imdb")
    season = params.get("season")
    episode = params.get("episode")
    sub_id = params.get("sub_id", "")
    logo = params.get("logo", "")
    series_name   = params.get("series_name", "")
    episode_title = params.get("episode_title", "")
    season_poster = params.get("season_poster", "")
    stream_name   = params.get("stream_name", "")
    stream_desc   = params.get("stream_desc", "")
    episode_plot  = params.get("episode_plot", "")
    list_item = xbmcgui.ListItem(path=video_url)
    tags = list_item.getVideoInfoTag()

    if episode_title:
        tags.setTitle(episode_title)
    if season and episode:
        tags.setMediaType("episode")
        tags.setSeason(int(season))
        tags.setEpisode(int(episode))
        if series_name:
            tags.setTvShowTitle(series_name)

    plot_parts = []
    if stream_name:
        plot_parts.append(f"[B]{stream_name}[/B]")
    if stream_desc:
        plot_parts.append(stream_desc)
    if episode_plot:
        if plot_parts:
            plot_parts.append("─" * 30)
        plot_parts.append(episode_plot)
    if plot_parts:
        tags.setPlot("\n".join(plot_parts))
    if imdb:
        tags.setIMDBNumber(imdb)
        xbmcgui.Window(10000).setProperty(
            "script.trakt.ids", json.dumps({"imdb": imdb})
        )

    art = {}
    if logo:
        art["clearlogo"] = logo
        art["tvshow.clearlogo"] = logo
    if season_poster:
        art["poster"] = season_poster
        art["tvshow.poster"] = season_poster
        art["season.poster"] = season_poster
    if art:
        list_item.setArt(art)

    if sub_id:
        try:
            resp = HTTP_SESSION.get(_SUBS_URL, timeout=10)
            if resp.ok:
                all_subs = resp.json()
                subs = _filter_subtitles(all_subs.get(sub_id, []), sub_id)
                if subs:
                    list_item.setSubtitles(_subtitle_paths(subs, sub_id))
            else:
                log(f"Subtitles fetch failed: HTTP {resp.status_code}")
        except Exception as e:
            log(f"Subtitles error: {e}")

    xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)

    if series_id and episode_id:
        _monitor_playback(series_id, episode_id, video_url)
