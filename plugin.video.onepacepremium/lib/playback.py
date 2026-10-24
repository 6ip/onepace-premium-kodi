import json
import os
import re
import threading

import xbmc
import xbmcgui
import xbmcvfs

from . import bookmarks as _bookmarks
from . import elementum as _elementum
from . import watched as _watched
from .route_common import set_resolved
from .utils import (ADDON_ID, build_url, get_setting, is_widget,
                    log, ping_widgets, session)

_SUBS_URL = "https://6ip.github.io/onepace-premium-subs/meta/subtitles.json"

# Safety cap only.
_START_CAP_DIRECT = 60
_START_CAP_HANDOFF = 900

# Polls to wait before trusting the player's clock after a file switch.
_SETTLE_POLLS = 5
# Long enough for a new list to be taken before the scan starts.
_REDRAW_SETTLE = 2.0
# Kodi 21 exits when two busy dialogs overlap.
_BUSY = "Window.IsActive(busydialog) | Window.IsActive(busydialognocancel)"
_BUSY_CLEAR = 0.5
_BUSY_CAP = 10.0
_BUSY_TICK = 0.1


def _wait_for_busy_dialog(monitor):
    """Hold the hand-off until Kodi's own busy dialog is gone. False on abort."""
    # Whole ticks, not summed seconds, so the cap cannot drift by float error.
    need, clear = round(_BUSY_CLEAR / _BUSY_TICK), 0
    for tick in range(round(_BUSY_CAP / _BUSY_TICK)):
        if xbmc.getCondVisibility(_BUSY):
            clear = 0
        else:
            clear += 1
            if clear >= need:
                if tick >= need:
                    log(f"[autoplay] waited {tick * _BUSY_TICK:.1f}s for Kodi's busy dialog")
                return True
        if monitor.waitForAbort(_BUSY_TICK):
            return False
    log(f"[autoplay] busy dialog still up after {_BUSY_CAP:.0f}s, going anyway")
    return True


def _keep_resume_cleared(episode_id, monitor, attempts=6, delay=0.5):
    """Kodi can save a resume point after we delete it, so watch for it."""
    from .episode_routes import _clear_kodi_episode_state, kodi_episode_has_bookmark
    for _ in range(attempts):
        if monitor.waitForAbort(delay):
            return
        if not kodi_episode_has_bookmark(episode_id):
            return
        _clear_kodi_episode_state(episode_id, ("bookmark",))
        log(f"[monitor] Kodi re-saved a resume point for {episode_id!r}, cleared again")


def _showing_other_season(path, season):
    """True if the list behind the player is a different season's episodes."""
    from urllib import parse
    query = dict(parse.parse_qsl(path.split("?", 1)[-1]))
    return (query.get("action") == "list_episodes"
            and query.get("season") != str(season))


def _int_setting(key, fallback, low, high):
    try:
        value = int(get_setting(key))
    except (TypeError, ValueError):
        value = fallback
    return min(high, max(low, value))


def _next_episode(series_id, episode_id):
    """The episode after this one, or None. Skips specials and notice cards."""
    from .episode_routes import _NOTICE_ID_PREFIX, episode_has_stream
    from .provider_api import _fetch_provider_meta, episode_play_url
    from .art import _episode_number
    from .downloads import path_for

    meta = _fetch_provider_meta("series", series_id)
    if not meta:
        return None

    videos = [
        v for v in meta.get("videos", ())
        if v.get("id") and v.get("season") != 0
        and not str(v["id"]).startswith(_NOTICE_ID_PREFIX)
    ]
    videos.sort(key=lambda v: (v.get("season") or 0, _episode_number(v) or 0))

    current = next((i for i, v in enumerate(videos) if v["id"] == episode_id), None)
    if current is None:
        return None

    # Hiding watched episodes should hide them from the card too.
    skip = (_watched.get_watched(series_id)
            if get_setting("hide_watched") == "true" else ())
    stay_in_season = get_setting("autoplay_next_season") == "false"

    for nxt in videos[current + 1:]:
        if stay_in_season and nxt.get("season") != videos[current].get("season"):
            return None
        if nxt["id"] not in skip:
            break
    else:
        return None

    # A copy on disk plays with no connection, so ask the server only without one.
    if not path_for(nxt["id"]) and not episode_has_stream("series", nxt["id"]):
        log(f"[autoplay] {nxt['id']} has no playable stream, not offering it")
        return None

    from .art import _upgrade_metahub_url
    season_poster = next(
        (s["poster"] for s in meta.get("seasons", ())
         if s.get("season") == nxt.get("season") and s.get("poster")), ""
    )
    title = nxt.get("name") or nxt.get("title") or nxt["id"]
    code = f"S{nxt.get('season') or 0:02d}E{_episode_number(nxt) or 0:02d}"
    return {
        "series": meta.get("name") or "",
        "episode": f"{title} ({code})",
        "title": title,
        "thumb": _upgrade_metahub_url(nxt.get("thumbnail")) or "",
        "url": episode_play_url(nxt, meta, series_id, "series", season_poster,
                                nxt["id"], autoplay=True),
    }


# Never covers more than this share of a short episode.
_SHORTEST_SHARE = 4
# How playback finished, straight from Kodi rather than inferred.
ENDED, STOPPED, ERROR, UNKNOWN = "ended", "stopped", "error", "unknown"
# The callback is queued, so it lands shortly after playback actually stops.
_ENDED_WAIT = 5.0
_ENDED_TICK = 0.1


def _announce_next_episode(up_next, player, monitor, window):
    """Show the corner bar. Returns (cancelled, anyone was there, left the range)."""
    from .next_episode_card import show_next_episode
    dismissed, touched, left_range = show_next_episode(
        up_next["series"], up_next["episode"], up_next["thumb"], player, monitor,
        window,
    )
    log("[autoplay] left on this episode" if dismissed
        else "[autoplay] the bar is out of the way, still queued")
    return dismissed, touched, left_range


def _may_start_next(up_next, monitor, touched):
    """Count the run, and ask whether anyone is there once it gets long."""
    from . import still_watching
    limit = _int_setting("autoplay_still_watching", 3, 0, 10)
    count = still_watching.note_episode(touched)
    log(f"[autoplay] {count} episode(s) in a row, asking at {limit or 'never'}"
        + (" (somebody was here, so the run restarted)" if touched else ""))
    if not limit or count < limit:
        return True
    if still_watching.ask(count, up_next["episode"], monitor):
        still_watching.reset()
        return True
    still_watching.reset()
    return False


def _watched_threshold():
    """Fraction of an episode that counts as watched."""
    try:
        percent = int(get_setting("watched_threshold"))
    except (TypeError, ValueError):
        percent = 85
    return min(100, max(50, percent)) / 100.0


class _WatchMonitor(xbmc.Player):
    """Records how playback finished, and lets us wait for it to say so."""

    def __init__(self):
        super().__init__()
        self.finished = threading.Event()
        self.outcome = None

    def _done(self, outcome):
        if self.outcome is None:
            self.outcome = outcome
        self.finished.set()

    def onPlayBackEnded(self):
        self._done(ENDED)

    def onPlayBackStopped(self):
        self._done(STOPPED)

    def onPlayBackError(self):
        self._done(ERROR)

    @property
    def ended_naturally(self):
        return self.outcome == ENDED

    def why_it_finished(self, monitor, seconds):
        """ENDED, STOPPED, ERROR — or UNKNOWN if nothing ever said."""
        waited = 0.0
        while waited < seconds:
            if self.finished.is_set():
                return self.outcome or UNKNOWN
            if monitor.waitForAbort(_ENDED_TICK):
                break
            waited += _ENDED_TICK
        return self.outcome or UNKNOWN


# Which monitor session is current, and for which episode.
_MONITOR_GEN = [0]
_MONITOR_EPISODE = [""]




def _monitor_playback(series_id, episode_id, video_url="", autoplay=False,
                      season=None):
    """Block until playback ends, then auto-mark the episode watched if appropriate."""
    _MONITOR_GEN[0] += 1
    _MONITOR_EPISODE[0] = episode_id
    my_gen = _MONITOR_GEN[0]
    # _MONITOR_GEN is per-process, so pid distinguishes duplicate invocations.
    pid = os.getpid()

    kodi_monitor = xbmc.Monitor()
    player = _WatchMonitor()
    last_time, total_time = 0.0, 0.0
    threshold = _watched_threshold()

    is_handoff = video_url.startswith("plugin://")
    cap = _START_CAP_HANDOFF if is_handoff else _START_CAP_DIRECT

    # Wait for playback.
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

    # Drop the bookmark if resume was answered "from beginning".
    bm = None if autoplay else (_bookmarks.get(episode_id) if episode_id else None)
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

    # Prepared once so the prompt does not stall on a lookup near the end.
    up_next = None
    if series_id and episode_id and get_setting("autoplay_next") == "true":
        up_next = _next_episode(series_id, episode_id)
        log(f"[autoplay] up next: {up_next['title']!r}" if up_next else "[autoplay] no next episode")
    prompt_at = _int_setting("autoplay_prompt_secs", 20, 5, 300)
    play_next_url = None
    queued_next = None
    was_touched = False
    may_show = True
    if not autoplay:
        # Picked by hand, so whatever ran unattended before this is history.
        from . import still_watching as _sw
        if _sw.episodes_in_a_row():
            log(f"[autoplay] {episode_id!r} was picked by hand, "
                "so the run starts over")
        _sw.reset()

    # Poll every 1 s; mark as soon as the threshold is reached during playback
    marked = False
    polls = 0
    while player.isPlaying():
        polls += 1
        if kodi_monitor.waitForAbort(1):
            return
        try:
            last_time  = player.getTime()
            total_time = player.getTotalTime()
        except Exception:
            pass
        if not marked and series_id and episode_id and total_time > 0:
            pct = last_time / total_time
            if pct >= threshold:
                marked = True
                _watched.set_episodes_watched(series_id, [episode_id], True)
                _bookmarks.clear(episode_id)

                log(f"[monitor] marked watched at {pct*100:.0f}% for {episode_id!r}")

        # getTime can report the previous file just after a switch.
        window = min(prompt_at, total_time / _SHORTEST_SHARE) if total_time else 0
        in_range = bool(window) and (total_time - last_time) <= window
        if not in_range:
            # Out of the range again, so it may come back on the way in.
            may_show = True
        if up_next and may_show and in_range and polls >= _SETTLE_POLLS:
            card = up_next
            dismissed, touched, left_range = _announce_next_episode(
                card, player, kodi_monitor, window)
            was_touched = was_touched or touched
            if dismissed:
                up_next = None          # they said no, so stop offering
            else:
                queued_next = card
                # Only leaving the range re-arms it; stepping aside was meant.
                may_show = left_range

    # A newer session for the *same* episode owns its state, so stand aside.
    if _MONITOR_GEN[0] != my_gen and _MONITOR_EPISODE[0] == episode_id:
        log(f"[monitor] superseded by gen={_MONITOR_GEN[0]} for the same episode, "
            f"skipping {episode_id!r}")
        return

    if queued_next:
        why = player.why_it_finished(kodi_monitor, _ENDED_WAIT)
        left = (total_time - last_time) if total_time else None
        if why != ENDED:
            # Anything but a clean end fails closed.
            log(f"[autoplay] playback {why}"
                + (f" with {left:.0f}s left" if left is not None else "")
                + ", so nothing follows it")
        elif _may_start_next(queued_next, kodi_monitor, was_touched):
            # Running on counts as finishing this one, whatever the threshold.
            play_next_url = queued_next["url"]
            if not marked and series_id and episode_id:
                marked = True
                _watched.set_episodes_watched(series_id, [episode_id], True)
                _bookmarks.clear(episode_id)
            log(f"[autoplay] starting {queued_next['title']!r}")
    elif was_touched:
        # Somebody was here, so the run starts over.
        from . import still_watching as _sw
        _sw.note_episode(touched=True)

    # Replaying something already finished must not put it back in progress.
    was_watched = bool(episode_id) and episode_id in _watched.get_watched(series_id)

    # Decide from end-of-stream signals if the threshold was missed.
    if not marked:
        pct = (last_time / total_time) if total_time > 0 else 0.0
        if player.ended_naturally or pct >= threshold:
            marked = True
            _watched.set_episodes_watched(series_id, [episode_id], True)
            _bookmarks.clear(episode_id)
            log(f"[monitor] marked watched at end (natural={player.ended_naturally} pct={pct*100:.0f}%) for {episode_id!r}")
        elif was_watched:
            _bookmarks.clear(episode_id)
            log(f"[monitor] left {episode_id!r} watched, no resume point kept")
        elif last_time > 60 and total_time > 0:
            _bookmarks.set_bookmark(episode_id, last_time, total_time, series_id)
            log(f"[monitor] saved bookmark {episode_id!r} at {last_time:.1f}s / {total_time:.1f}s")

    # Kodi saves its own resume point when playback stops short.
    if play_next_url:
        # Stop first.
        player.stop()
        for _ in range(20):
            if not player.isPlaying():
                break
            if kodi_monitor.waitForAbort(0.1):
                break
        # Kodi is behind a busy dialog; PlayMedia would raise a second.
        if not _wait_for_busy_dialog(kodi_monitor):
            return
        # noresume: we position playback ourselves, so Kodi must not also ask.
        xbmc.executebuiltin(f"PlayMedia({play_next_url},noresume)")

    # For the episode we just left, so it runs behind the switch.
    if episode_id:
        from .episode_routes import (_clear_kodi_episode_state,
                                     _update_kodi_episode_playcount)
        if marked or was_watched:
            # Kodi keeps its own resume point when playback stops short.
            _clear_kodi_episode_state(episode_id)
            _update_kodi_episode_playcount(episode_id, 1)
        else:
            # Kodi calls the last few percent watched (ignorepercentatend).
            _update_kodi_episode_playcount(episode_id, 0)
        # Skipped on hand-off: the incoming monitor owns the list.
        path = ""
        if not play_next_url:
            # Kodi restores the list while the player tears down.
            for _ in range(15):
                path = xbmc.getInfoLabel("Container.FolderPath")
                if ADDON_ID in path or kodi_monitor.waitForAbort(0.2):
                    break
        # A widget's FolderPath is ours too, so ask who owns the list.
        ours = ADDON_ID in path and not is_widget()
        if ours:
            if season and _showing_other_season(path, season):
                # Plain Update only.
                url = build_url("list_episodes", catalog_type="series",
                                video_id=series_id, season=season)
                xbmc.executebuiltin(f"Container.Update({url})")
                log(f"[monitor] moved the list to season {season}")
            else:
                xbmc.executebuiltin("Container.Refresh")
                log(f"[monitor] refreshed list for {episode_id!r}")
        if not play_next_url:
            # The resume point moved, so every shelf showing it is now stale.
            if ours and kodi_monitor.waitForAbort(_REDRAW_SETTLE):
                return
            ping_widgets()
        if marked or was_watched:
            _keep_resume_cleared(episode_id, kodi_monitor)


# Kodi reads the language from the name, and variants add a token.
_SUBS_CACHE = "special://temp/onepace-subs/"
_SUBS_BUDGET = 3.0          # seconds spent fetching before we stop
_VARIANT_RE = re.compile(r"\(([^)]*)\)\s*$")


def _cache_path(track, sub_id):
    """Where this track would live on disk, or None if it cannot be fetched."""
    if not (track.get("url") and track.get("lang")):
        return None
    match = _VARIANT_RE.search(track.get("label") or "")
    variant = re.sub(r"[^\w.-]", "_", match.group(1)) if match else "main"
    return f"{_SUBS_CACHE}{sub_id}/{variant}.{track['lang']}.vtt"


def _fetch_subtitle(url, path):
    """Save one .vtt. Returns the path, or None so the caller falls back."""
    name = path.rsplit("/", 1)[-1]
    partial = path + ".part"
    try:
        import requests
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        content = response.content
        if not content.lstrip()[:6] == b"WEBVTT":
            log(f"[subs] {name} is not a WEBVTT file, skipping")
            return None
        with xbmcvfs.File(partial, "w") as handle:
            handle.write(content)
        # Windows refuses a rename onto a name already taken.
        if xbmcvfs.exists(path):
            xbmcvfs.delete(path)
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


def _subtitle_paths(subs, sub_id, fetch=True):
    """Local paths for variants, plain URLs for everything else."""
    targets = {}
    if fetch:
        for index, track in enumerate(subs):
            path = _cache_path(track, sub_id)
            if path and not xbmcvfs.exists(path):
                targets[index] = (track["url"], path)

    done = {}
    if targets:
        from concurrent import futures
        xbmcvfs.mkdirs(f"{_SUBS_CACHE}{sub_id}/")
        # Not a `with` block: it waits on exit and defeats the budget.
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
            pool.shutdown(wait=False, cancel_futures=True)

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
        log(f"[subs] {sub_id}: {cached} file(s) local"
            + (f", {missed} fell back (over {_SUBS_BUDGET:.0f}s)" if missed else ""))
    return paths


def _wanted_langs():
    return [c for c in get_setting("sub_langs").split(",") if c]


def _filter_subtitles(subs, sub_id, wanted):
    """Narrow the track list to the user's languages. Empty means all."""
    if not subs:
        return subs

    total = len(subs)
    if wanted:
        subs = [s for s in subs if s.get("lang") in wanted]

    log(f"Subtitles for {sub_id}: {len(subs)} of {total} tracks after filtering")
    return subs


def play_video(params):
    series_id = params.get("series_id", "")
    episode_id = params.get("episode_id", "")

    video_url = params["video_url"]

    # Resolve here, so our ListItem stays the playing item.
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
    episode_thumb = params.get("thumb", "")
    stream_name   = params.get("stream_name", "")
    stream_desc   = params.get("stream_desc", "")
    episode_plot  = params.get("episode_plot", "")
    list_item = xbmcgui.ListItem(path=video_url)
    tags = list_item.getVideoInfoTag()

    # PlayMedia ignores resume points, so apply ours.
    if params.get("autoplay") and episode_id:
        bm = _bookmarks.get(episode_id) or {}
        pos, total = bm.get("pos", 0), bm.get("total", 0)
        if pos > 60 and total > 0:
            list_item.setProperty("StartPercent", str(pos / total * 100))
            log(f"[autoplay] resuming {episode_id!r} at {pos:.0f}s of {total:.0f}s")

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

    # Same values the list row uses, so the OSD and info dialog match it.
    art = {}
    if logo:
        art["clearlogo"] = logo
        art["tvshow.clearlogo"] = logo
    if episode_thumb:
        # landscape only.
        art["landscape"] = episode_thumb
    if season_poster:
        art["poster"] = season_poster
        art["tvshow.poster"] = season_poster
        art["season.poster"] = season_poster
        art["thumb"] = season_poster
    if art:
        list_item.setArt(art)

    # Kodi only writes stream details into an empty row.
    if episode_id:
        from .episode_routes import _clear_kodi_episode_state
        _clear_kodi_episode_state(episode_id, ("streamdetails",))

    # A downloaded episode carries its own, already in the right order.
    from .downloads import local_subtitles
    beside = local_subtitles(video_url)
    if beside:
        log(f"[subs] {len(beside)} file(s) beside the download")
        list_item.setSubtitles(beside)
    elif sub_id and get_setting("subs_enabled") != "false":
        try:
            resp = session().get(_SUBS_URL, timeout=10)
            if resp.ok:
                all_subs = resp.json()
                wanted = _wanted_langs()
                subs = _filter_subtitles(all_subs.get(sub_id, []), sub_id, wanted)
                if subs:
                    # Only worth downloading for languages the user picked.
                    list_item.setSubtitles(
                        _subtitle_paths(subs, sub_id, fetch=bool(wanted))
                    )
            else:
                log(f"Subtitles fetch failed: HTTP {resp.status_code}")
        except Exception as e:
            log(f"Subtitles error: {e}")

    set_resolved(list_item)

    if series_id and episode_id:
        _monitor_playback(series_id, episode_id, video_url,
                          autoplay=bool(params.get("autoplay")), season=season)
