import json
import os
import re

import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs

from . import bookmarks as _bookmarks
from . import elementum as _elementum
from . import watched as _watched
from .utils import ADDON_HANDLE, ADDON_ID, get_setting, log, session

_SUBS_URL = "https://6ip.github.io/onepace-premium-subs/meta/subtitles.json"

# Safety cap only. Real failure is detected via Kodi's error dialog, so a
# plugin:// handoff can buffer as long as it needs.
_START_CAP_DIRECT = 60
_START_CAP_HANDOFF = 900

# Polls to wait before trusting the player's clock after a file switch.
_SETTLE_POLLS = 5


def _keep_resume_cleared(episode_id, monitor, attempts=6, delay=0.5):
    """Kodi can save its resume point after we delete it — Elementum's teardown
    is slow enough to lose that race. Re-clear until it stops coming back.
    """
    from .episode_routes import _clear_kodi_episode_state, kodi_episode_has_bookmark
    for _ in range(attempts):
        if monitor.waitForAbort(delay):
            return
        if not kodi_episode_has_bookmark(episode_id):
            return
        _clear_kodi_episode_state(episode_id, ("bookmark",))
        log(f"[monitor] Kodi re-saved a resume point for {episode_id!r}, cleared again")


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
    if current is None or current + 1 >= len(videos):
        return None

    nxt = videos[current + 1]
    if (nxt.get("season") != videos[current].get("season")
            and get_setting("autoplay_next_season") == "false"):
        return None

    # Check it can actually play before offering it — otherwise the card appears,
    # the player stops on click, and only then does it fail.
    if not episode_has_stream("series", nxt["id"]):
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


def _prompt_next_episode(up_next, player, monitor):
    """Ask via the corner card. Nothing happens unless the viewer says so."""
    from .next_episode_card import ask_next_episode
    return ask_next_episode(
        up_next["series"], up_next["episode"], up_next["thumb"], player, monitor
    )


def _watched_threshold():
    """Fraction of an episode that counts as watched."""
    try:
        percent = int(get_setting("watched_threshold"))
    except (TypeError, ValueError):
        percent = 85
    return min(100, max(50, percent)) / 100.0


class _WatchMonitor(xbmc.Player):
    """xbmc.Player subclass that records whether playback ended naturally."""
    def __init__(self):
        super().__init__()
        self.ended_naturally = False

    def onPlayBackEnded(self):
        self.ended_naturally = True


# Which monitor session is current, and for which episode. Lists avoid needing
# `global` declarations in nested functions.
_MONITOR_GEN = [0]
_MONITOR_EPISODE = [""]




def _monitor_playback(series_id, episode_id, video_url="", autoplay=False):
    """Block until playback ends, then auto-mark the episode watched if appropriate.

    Called from play_video after setResolvedUrl so it runs inside the plugin
    action thread — keeping the process alive for the duration of playback.
    """
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

    # Drop the bookmark if Kodi's resume dialog was answered with "from
    # beginning". Autoplay never shows that dialog.
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
    prompt_at = _int_setting("autoplay_prompt_secs", 20, 5, 90)
    play_next_url = None

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

        # getTime/getTotalTime can still report the previous file for a moment
        # after Kodi switches, which would fire the card at the start.
        # Never before halfway, or a 2-minute episode opens with the card up.
        if (up_next and polls >= _SETTLE_POLLS and total_time > 0
                and (total_time - last_time) <= min(prompt_at, total_time / 2)):
            card = up_next
            up_next = None  # ask once per playback
            if _prompt_next_episode(card, player, kodi_monitor):
                # Accepting counts as finishing this one, whatever the threshold.
                play_next_url = card["url"]
                if not marked and series_id and episode_id:
                    marked = True
                    _watched.set_episodes_watched(series_id, [episode_id], True)
                    _bookmarks.clear(episode_id)
                log(f"[autoplay] starting {card['title']!r}")
                break
            log("[autoplay] cancelled")

    # A newer session for the *same* episode owns its state, so stand aside. One
    # for a different episode (playing the next one) leaves ours to finish.
    if _MONITOR_GEN[0] != my_gen and _MONITOR_EPISODE[0] == episode_id:
        log(f"[monitor] superseded by gen={_MONITOR_GEN[0]} for the same episode, "
            f"skipping {episode_id!r}")
        return

    # If threshold wasn't hit during playback, decide now based on end-of-stream signals
    if not marked:
        pct = (last_time / total_time) if total_time > 0 else 0.0
        if player.ended_naturally or pct >= threshold:
            marked = True
            _watched.set_episodes_watched(series_id, [episode_id], True)
            _bookmarks.clear(episode_id)
            log(f"[monitor] marked watched at end (natural={player.ended_naturally} pct={pct*100:.0f}%) for {episode_id!r}")
        elif last_time > 60 and total_time > 0:
            _bookmarks.set_bookmark(episode_id, last_time, total_time, series_id)
            log(f"[monitor] saved bookmark {episode_id!r} at {last_time:.1f}s / {total_time:.1f}s")

    # Kodi saves its own resume point when playback stops short. Left behind, a
    # skin draws "resumable" instead of the watched tick.
    if play_next_url:
        # Stop first. PlayMedia leaves this file running until the next stream is
        # ready, and a still-loaded file reports isPlaying() — so the incoming
        # monitor would attach to this clock, fire its card and mark it watched.
        player.stop()
        for _ in range(20):
            if not player.isPlaying():
                break
            if kodi_monitor.waitForAbort(0.1):
                break
        xbmc.executebuiltin(f"PlayMedia({play_next_url})")

    # Everything below is for the episode we just left, so it runs behind the
    # switch rather than delaying it. _MONITOR_EPISODE lets this session finish
    # even though a newer one has taken over.
    if episode_id:
        from .episode_routes import (_clear_kodi_episode_state,
                                     _update_kodi_episode_playcount)
        if marked:
            _clear_kodi_episode_state(episode_id)
            _update_kodi_episode_playcount(episode_id, 1)
        else:
            # Kodi calls anything stopped in the last few percent "watched"
            # (ignorepercentatend). Our threshold decides, so put it back.
            _update_kodi_episode_playcount(episode_id, 0)
        if ADDON_ID in xbmc.getInfoLabel("Container.FolderPath"):
            xbmc.executebuiltin("Container.Refresh")
            log(f"[monitor] refreshed list for {episode_id!r}")
        if marked:
            _keep_resume_cleared(episode_id, kodi_monitor)


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
        import requests
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


def _subtitle_paths(subs, sub_id, fetch=True):
    """Local paths for variants, plain URLs for everything else.

    With fetch off, cached files are still used but nothing new is downloaded.
    """
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


def _wanted_langs():
    return [c for c in get_setting("sub_langs").split(",") if c]


def _filter_subtitles(subs, sub_id, wanted):
    """Narrow the track list to the user's languages. Empty means all.

    Alternate versions (CC, DUB, ALT) are always kept — some episodes only
    have an extended or alternate cut.
    """
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
        art["thumb"] = episode_thumb
        art["icon"] = episode_thumb
        art["landscape"] = episode_thumb
    if season_poster:
        art["poster"] = season_poster
        art["tvshow.poster"] = season_poster
        art["season.poster"] = season_poster
    if art:
        list_item.setArt(art)

    # Kodi only writes stream details into an empty row, so clear the old ones
    # or the codec badges keep showing the previous encode.
    if episode_id:
        from .episode_routes import _clear_kodi_episode_state
        _clear_kodi_episode_state(episode_id, ("streamdetails",))

    if sub_id and get_setting("subs_enabled") != "false":
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

    xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, list_item)

    if series_id and episode_id:
        _monitor_playback(series_id, episode_id, video_url,
                          autoplay=bool(params.get("autoplay")))
