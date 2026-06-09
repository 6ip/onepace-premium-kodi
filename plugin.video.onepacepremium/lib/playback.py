import json

import xbmc
import xbmcgui
import xbmcplugin

from . import bookmarks as _bookmarks
from . import watched as _watched
from .utils import ADDON_HANDLE, HTTP_SESSION, log

_SUBS_URL = "https://6ip.github.io/onepace-premium-subs/meta/subtitles.json"


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


def _monitor_playback(series_id, episode_id):
    """Block until playback ends, then auto-mark the episode watched if appropriate.

    Called from play_video after setResolvedUrl so it runs inside the plugin
    action thread — keeping the process alive for the duration of playback.
    """
    _MONITOR_GEN[0] += 1
    my_gen = _MONITOR_GEN[0]

    kodi_monitor = xbmc.Monitor()
    player = _WatchMonitor()
    last_time, total_time = 0.0, 0.0

    # Wait up to 15 s for the player to actually start
    for _ in range(15):
        if player.isPlaying():
            break
        if kodi_monitor.waitForAbort(1):
            return
    if not player.isPlaying():
        log(f"[monitor] playback never started for {episode_id!r}, giving up")
        return

    log(f"[monitor] tracking {episode_id!r} (gen={my_gen})")

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
            log(f"[monitor] marked watched at end (natural={player.ended_naturally} pct={pct*100:.0f}%) for {episode_id!r}")
        elif last_time > 180 and total_time > 0:
            _bookmarks.set_bookmark(episode_id, last_time, total_time)
            log(f"[monitor] saved bookmark {episode_id!r} at {last_time:.1f}s / {total_time:.1f}s")
    # No Container.Refresh here — cacheToDisc=False means Kodi re-runs the plugin
    # fresh when the user navigates back, so the watched state is already correct.


def play_video(params):
    series_id = params.get("series_id", "")
    episode_id = params.get("episode_id", "")

    video_url = params["video_url"]
    imdb = params.get("imdb")
    season = params.get("season")
    episode = params.get("episode")
    sub_id = params.get("sub_id", "")
    logo = params.get("logo", "")

    list_item = xbmcgui.ListItem(path=video_url)
    tags = list_item.getVideoInfoTag()

    if season and episode:
        tags.setSeason(int(season))
        tags.setEpisode(int(episode))
    if imdb:
        tags.setIMDBNumber(imdb)
        xbmcgui.Window(10000).setProperty(
            "script.trakt.ids", json.dumps({"imdb": imdb})
        )

    if logo:
        list_item.setArt({"clearlogo": logo, "tvshow.clearlogo": logo})

    if sub_id:
        try:
            resp = HTTP_SESSION.get(_SUBS_URL, timeout=10)
            if resp.ok:
                all_subs = resp.json()
                subs = all_subs.get(sub_id, [])
                log(f"Subtitles for {sub_id}: {len(subs)} tracks found")
                if subs:
                    list_item.setSubtitles([s["url"] for s in subs])
            else:
                log(f"Subtitles fetch failed: HTTP {resp.status_code}")
        except Exception as e:
            log(f"Subtitles error: {e}")

    xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)

    if series_id and episode_id:
        _monitor_playback(series_id, episode_id)
