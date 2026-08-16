import os

import xbmcgui
import xbmcplugin

from . import bookmarks as _bookmarks
from . import watched as _watched
from .art import (_episode_number, _set_episode_art, _upgrade_metahub_url)
from .provider_api import (SERIES_STUDIOS, _fetch_provider_meta,
                            _parse_air_date, _parse_release_year,
                            _parse_runtime_seconds, _prefetch_metas)
from .route_common import _add_directory_items, end_directory

from .utils import ADDON_DIR, ADDON_ID, ADDON_HANDLE, build_url, log

_CATALOG_TYPE = "series"
_SKIN_MEDIA  = os.path.join(ADDON_DIR, "resources", "skins", "Default", "media")

_LISTS_ICON  = os.path.join(_SKIN_MEDIA, "lists2.png")
_PLAYER_ICON = os.path.join(_SKIN_MEDIA, "player2.png")
_NEXT_ICON   = os.path.join(_SKIN_MEDIA, "next_episodes2.png")
_FANART      = os.path.join(ADDON_DIR, "resources", "fanart.png")


def _folder_item(label, icon):
    item = xbmcgui.ListItem(label=label, offscreen=True)
    item.setArt({
        "icon": icon,
        "thumb": icon,
        "poster": icon,
        "fanart": _FANART,
        "banner": icon,
        "landscape": icon,
    })
    item.getVideoInfoTag().setPlot("​")
    return item


def _build_episode_item(video, ep_id, series_id, meta, show_title,
                         season_poster_map, bm=None, is_watched=False):
    """Build an IsPlayable episode ListItem."""
    episode_number = _episode_number(video)
    if episode_number is None:
        return None

    selected_season = video.get("season")
    title = video.get("name") or video.get("title") or f"Episode {episode_number}"
    display_label = f"[[COLOR fff502f4]{show_title}[/COLOR]] {title}" if show_title else title
    list_item = xbmcgui.ListItem(label=display_label, offscreen=True)
    tags = list_item.getVideoInfoTag()
    tags.setTitle(title)
    tags.setTvShowTitle(show_title)
    if selected_season is not None:
        tags.setSeason(selected_season)
    tags.setEpisode(int(episode_number))
    tags.setMediaType("episode")

    if bm:
        pos, total = bm.get("pos", 0), bm.get("total", 0)
        if total > 0:
            pct = min(99, max(1, int(pos / total * 100)))
            list_item.setProperty("WatchedProgress", str(pct))
            list_item.setProperty("PercentPlayed", str(pct))
            tags.setResumePoint(pos, total)

    plot = video.get("overview") or meta.get("description") or ""
    if plot:
        tags.setPlot(plot)

    release_year = _parse_release_year(video.get("released") or meta.get("releaseInfo"))
    if release_year:
        tags.setYear(release_year)

    # Air date and age rating are what the skin renders as its
    # "28/06/2026 • TV-14" info line. ageRating is series-level, so every
    # episode inherits it — but only alongside a date: some skins join these
    # with a literal separator and would leave a stray "• TV-14" otherwise.
    air_date = _parse_air_date(video)
    if air_date:
        tags.setPremiered(air_date)
        tags.setFirstAired(air_date)

    age_rating = meta.get("ageRating")
    if age_rating:
        tags.setMpaa(age_rating)

    runtime = _parse_runtime_seconds(video)
    if runtime:
        tags.setDuration(runtime)

    tags.setStudios(SERIES_STUDIOS)

    genres = meta.get("genres")
    if genres:
        tags.setGenres(genres)

    list_item.setProperty("IsPlayable", "true")
    _set_episode_art(list_item, video, meta, season_poster_map.get(selected_season))

    ep_ctx_label = "[B]Mark Unwatched[/B]" if is_watched else "[B]Mark Watched[/B]"
    ctx_items = [(
        ep_ctx_label,
        f"RunPlugin({build_url('mark_watched', scope='episode', series_id=series_id, episode_id=ep_id)})",
    )]
    if bm:
        ctx_items.append((
            "[B]Clear Progress[/B]",
            f"RunPlugin({build_url('clear_progress', episode_id=ep_id)})",
        ))
    if selected_season is not None:
        ctx_items.append((
            "[B]Browse Season...[/B]",
            f"ActivateWindow(Videos,{build_url('list_episodes', catalog_type=_CATALOG_TYPE, video_id=series_id, season=selected_season)},return)",
        ))
    list_item.addContextMenuItems(ctx_items, replaceItems=True)

    episode_thumb = _upgrade_metahub_url(video.get("thumbnail")) or ""
    season_poster = season_poster_map.get(selected_season) or ""

    url = build_url(
        "check_resume",
        catalog_type=_CATALOG_TYPE,
        video_id=ep_id,
        thumb=episode_thumb,
        logo=meta.get("logo") or "",
        parent_id=series_id,
        series_name=show_title,
        episode_title=title,
        season=selected_season,
        episode=episode_number,
        season_poster=season_poster,
        episode_plot=plot,
    )
    sort_key = (selected_season or 0, int(episode_number))
    return sort_key, url, list_item


def _get_series_meta(series_id):
    meta = _fetch_provider_meta(_CATALOG_TYPE, series_id)
    if not meta:
        return None, {}, {}, ""
    video_map = {v.get("id"): v for v in meta.get("videos", []) if v.get("id")}
    season_poster_map = {
        s["season"]: s["poster"]
        for s in meta.get("seasons", [])
        if s.get("season") is not None and s.get("poster")
    }
    show_title = meta.get("name") or ""
    return meta, video_map, season_poster_map, show_title


def list_my_lists(params):
    xbmcplugin.setContent(ADDON_HANDLE, "")
    xbmcplugin.setPluginCategory(ADDON_HANDLE, "My Lists")
    items = [
        (build_url("list_in_progress"),   _folder_item("In Progress",   _PLAYER_ICON), True),
        (build_url("list_next_episodes"),  _folder_item("Next Episodes", _NEXT_ICON),   True),
    ]
    _add_directory_items(items)
    end_directory(cache=True)


def list_in_progress(params):
    all_bookmarks = _bookmarks.get_all()

    # Group by series_id (only bookmarks that have it saved)
    by_series = {}
    for ep_id, bm in all_bookmarks.items():
        sid = bm.get("series_id")
        if sid:
            by_series.setdefault(sid, {})[ep_id] = bm

    if not by_series:
        end_directory()
        return

    xbmcplugin.setContent(ADDON_HANDLE, "episodes")
    xbmcplugin.setPluginCategory(ADDON_HANDLE, "In Progress")
    _prefetch_metas(_CATALOG_TYPE, by_series)
    built = []

    for series_id, bookmarks in by_series.items():
        meta, video_map, season_poster_map, show_title = _get_series_meta(series_id)
        if not meta:
            continue
        series_watched = _watched.get_watched(series_id)
        for ep_id, bm in bookmarks.items():
            if ep_id in series_watched:
                continue
            video = video_map.get(ep_id)
            if not video:
                continue
            result = _build_episode_item(
                video, ep_id, series_id, meta, show_title, season_poster_map, bm
            )
            if result:
                built.append(result)

    if not built:
        end_directory()
        return

    built.sort(key=lambda x: x[0])
    _add_directory_items([(url, li, False) for _, url, li in built])
    end_directory()
    log(f"[my_lists] in_progress: {len(built)} episode(s) across {len(by_series)} series")


def list_next_episodes(params):
    """Show the next unwatched episode for each series where the user has made progress."""
    # Collect all series the user has interacted with
    all_series = set()
    for bm in _bookmarks.get_all().values():
        sid = bm.get("series_id")
        if sid:
            all_series.add(sid)
    for sid in _watched.get_all_series_ids():
        all_series.add(sid)

    if not all_series:
        end_directory()
        return

    xbmcplugin.setContent(ADDON_HANDLE, "episodes")
    xbmcplugin.setPluginCategory(ADDON_HANDLE, "Next Episodes")
    _prefetch_metas(_CATALOG_TYPE, all_series)
    built = []

    for series_id in all_series:
        meta, video_map, season_poster_map, show_title = _get_series_meta(series_id)
        if not meta:
            continue
        series_watched = _watched.get_watched(series_id)

        # Sort non-special episodes by season + episode
        videos = sorted(
            [v for v in meta.get("videos", []) if v.get("id") and v.get("season", 0) != 0],
            key=lambda v: (v.get("season", 0) or 0, _episode_number(v) or 0)
        )

        # Only show a next episode if the user has watched at least one
        if not series_watched:
            continue

        next_video = next((v for v in videos if v.get("id") not in series_watched), None)
        if not next_video:
            continue

        ep_id = next_video.get("id")
        bm = _bookmarks.get(ep_id)
        result = _build_episode_item(
            next_video, ep_id, series_id, meta, show_title, season_poster_map, bm
        )
        if result:
            built.append(result)

    if not built:
        end_directory()
        return

    built.sort(key=lambda x: x[0])
    _add_directory_items([(url, li, False) for _, url, li in built])
    end_directory()
    log(f"[my_lists] next_episodes: {len(built)} series")
