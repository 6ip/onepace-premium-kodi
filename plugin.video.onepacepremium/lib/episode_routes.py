from urllib import parse

import xbmc
import xbmcgui
import xbmcplugin

from . import bookmarks as _bookmarks
from . import cache as _cache
from . import watched as _watched
from .art import (_episode_number, _season_thumbnails, _set_episode_art,
                   _set_ids, _set_season_art, _stream_tagline,
                   _upgrade_metahub_url)
from .parser import parse_stream_info
from .provider_api import _compose_url, _fetch_provider_meta, _parse_release_year
from .route_common import _add_directory_items, _notify_error
from .utils import (ADDON_HANDLE, ALERT_ICON, build_url,
                     convert_info_hash_to_magnet, ensure_configured,
                     fetch_data, get_base_url, get_config_prefix,
                     get_secret_string,
                     is_elementum_installed_and_enabled, log)


def list_seasons(params):
    if not ensure_configured():
        return

    catalog_type = params["catalog_type"]
    video_id = params["video_id"]

    meta = _fetch_provider_meta(catalog_type, video_id)
    if not meta:
        return

    videos = meta.get("videos", ())
    if not videos:
        _notify_error("No seasons available")
        return

    xbmcplugin.setContent(ADDON_HANDLE, "tvshows")

    season_thumbnails = _season_thumbnails(videos)

    # Use dedicated season posters from meta["seasons"] if available
    season_poster_map = {
        s["season"]: s["poster"]
        for s in meta.get("seasons", [])
        if s.get("season") is not None and s.get("poster")
    }

    seasons = sorted(
        {
            season
            for video in videos
            for season in [video.get("season")]
            if season is not None
        }
    )
    if 0 in seasons:
        seasons = [season for season in seasons if season != 0] + [0]

    show_title = meta.get("name") or ""

    series_watched = _watched.get_watched(video_id)
    all_ep_ids = [v["id"] for v in videos if v.get("id")]
    if all_ep_ids:
        _watched.cache_total(video_id, len(all_ep_ids))

    # Pre-build per-season episode ID lists for count display
    season_ep_ids = {}
    for v in videos:
        s = v.get("season")
        eid = v.get("id")
        if s is not None and eid:
            season_ep_ids.setdefault(s, []).append(eid)


    items = []
    for season in seasons:
        label = "Specials" if season == 0 else f"Season {season}"
        list_item = xbmcgui.ListItem(label=label, offscreen=True)
        tags = list_item.getVideoInfoTag()
        tags.setTitle(label)
        tags.setTvShowTitle(show_title)
        # Prefer dedicated season poster, fall back to first-episode thumbnail
        season_art = season_poster_map.get(season) or season_thumbnails.get(season)
        _set_season_art(list_item, meta, season_art)
        ep_ids = season_ep_ids.get(season, [])
        season_fully_watched = False
        if ep_ids:
            s_total = len(ep_ids)
            s_watched = sum(1 for eid in ep_ids if eid in series_watched)
            season_fully_watched = s_watched >= s_total
            props = {
                "UnWatchedEpisodes": str(s_total - s_watched),
                "TotalEpisodes": str(s_total),
            }
            if s_watched > 0:
                props["WatchedEpisodes"] = str(s_watched)
            list_item.setProperties(props)
            if s_watched >= s_total:
                tags.setPlaycount(1)
        tags.setMediaType("season")
        season_ctx_label = "Mark as Unwatched" if season_fully_watched else "Mark as Watched"
        list_item.addContextMenuItems([(
            season_ctx_label,
            f"RunPlugin({build_url('mark_watched', scope='season', series_id=video_id, catalog_type=catalog_type, season=season)})",
        )])

        items.append(
            (
                build_url(
                    "list_episodes",
                    catalog_type=catalog_type,
                    video_id=video_id,
                    season=season,
                ),
                list_item,
                True,
            )
        )

    _add_directory_items(items)
    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)


def list_episodes(params):
    if not ensure_configured():
        return

    catalog_type = params["catalog_type"]
    video_id = params["video_id"]
    selected_season = int(params["season"])

    meta = _fetch_provider_meta(catalog_type, video_id)
    if not meta:
        return

    videos = meta.get("videos", ())
    if not videos:
        _notify_error("No episodes available")
        return

    xbmcplugin.setContent(ADDON_HANDLE, "episodes")
    season_videos = sorted(
        (video for video in videos if video.get("season") == selected_season),
        key=lambda video: _episode_number(video) or 0,
    )

    show_title = meta.get("name") or ""
    meta_description = meta.get("description")
    meta_genres = meta.get("genres")
    meta_release_info = meta.get("releaseInfo")
    series_watched = _watched.get_watched(video_id)
    season_poster_map = {
        s["season"]: s["poster"]
        for s in meta.get("seasons", [])
        if s.get("season") is not None and s.get("poster")
    }
    season_poster = season_poster_map.get(selected_season) or ""
    log(f"[watched] series={video_id!r} watched_count={len(series_watched)} ids={sorted(series_watched)}")

    items = []
    for video in season_videos:
        episode_number = _episode_number(video)
        if episode_number is None:
            continue

        # Compute episode ID early — needed for watched check and context menu.
        stream_video_id = video.get("id") or f"{video_id}:{selected_season}:{episode_number}"

        title = video.get("name") or video.get("title") or f"Episode {episode_number}"
        list_item = xbmcgui.ListItem(label=title, offscreen=True)
        tags = list_item.getVideoInfoTag()
        _set_ids(tags, video_id)
        tags.setTitle(title)
        tags.setTvShowTitle(show_title)
        tags.setSeason(selected_season)
        tags.setEpisode(int(episode_number))

        tags.setMediaType("episode")
        if stream_video_id in series_watched:
            tags.setPlaycount(1)
            bm = _bookmarks.get(stream_video_id)
            if bm:
                pos, total = bm.get("pos", 0), bm.get("total", 0)
                if total > 0:
                    pct = min(99, max(1, int(pos / total * 100)))
                    list_item.setProperty("WatchedProgress", str(pct))

        plot = video.get("overview") or meta_description
        if plot:
            tags.setPlot(plot)

        release_year = _parse_release_year(video.get("released") or meta_release_info)
        if release_year:
            tags.setYear(release_year)

        if meta_genres:
            tags.setGenres(meta_genres)

        _set_episode_art(list_item, video, meta)
        ep_ctx_label = "Mark as Unwatched" if stream_video_id in series_watched else "Mark as Watched"
        list_item.addContextMenuItems([(
            ep_ctx_label,
            f"RunPlugin({build_url('mark_watched', scope='episode', series_id=video_id, episode_id=stream_video_id)})",
        )], replaceItems=True)
        episode_thumb = _upgrade_metahub_url(video.get("thumbnail")) or ""
        items.append(
            (
                build_url(
                    "check_resume",
                    catalog_type=catalog_type,
                    video_id=stream_video_id,
                    thumb=episode_thumb,
                    logo=meta.get("logo") or "",
                    parent_id=video_id,
                    series_name=show_title,
                    episode_title=title,
                    season=selected_season,
                    episode=episode_number,
                    season_poster=season_poster,
                ),
                list_item,
                True,
            )
        )

    if not items:
        _notify_error("No episodes available")
        return

    _add_directory_items(items)
    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)


def check_resume(params):
    get_streams(params)


def get_streams(params):
    if not ensure_configured():
        return

    if not get_secret_string():
        xbmcplugin.setContent(ADDON_HANDLE, "files")
        list_item = xbmcgui.ListItem(label="Add-on Not Configured - Click to Set Up", offscreen=True)
        list_item.setArt({"icon": ALERT_ICON, "thumb": ALERT_ICON})
        tags = list_item.getVideoInfoTag()
        tags.setMediaType("video")
        tags.setPlot(
            "Your One Pace Premium add-on hasn't been configured yet. "
            "Click here to open Add-on Settings and complete setup."
        )
        _add_directory_items([(build_url("open_addon_settings"), list_item, True)])
        xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)
        return

    catalog_type = params["catalog_type"]
    video_id = params["video_id"]
    episode_thumb = params.get("thumb", "")
    series_logo = params.get("logo", "")
    parent_id = params.get("parent_id", "")
    series_name = params.get("series_name", "")
    episode_title = params.get("episode_title", "")
    season_poster = params.get("season_poster", "")
    episode_bookmark = _bookmarks.get(video_id)
    stream_url = _compose_url(
        get_base_url(),
        f"{get_config_prefix()}stream/{catalog_type}/{video_id}.json?kodi=1",
    )

    response = _cache.get(stream_url)
    if response is None:
        response = fetch_data(stream_url)
        if not response:
            return
        _cache.set(stream_url, response, 3600)

    streams = response.get("streams", ())
    if not streams:
        _notify_error("No streams available")
        return

    xbmcplugin.setContent(ADDON_HANDLE, "files")

    id_parts = video_id.split(":", 2)
    if len(id_parts) == 3:
        imdb_id, season, episode = id_parts
        season_number = int(season)
        episode_number = int(episode)
    else:
        imdb_id = video_id
        season = params.get("season")
        episode = params.get("episode")
        season_number = int(season) if season is not None else None
        episode_number = int(episode) if episode is not None else None
    is_imdb = imdb_id.startswith("tt")
    sub_id = video_id if not is_imdb and ":" not in video_id else ""
    if sub_id.startswith("pp_"):
        sub_id = sub_id[3:]
    log(f"get_streams video_id={video_id!r} sub_id={sub_id!r}")

    stream_items = []
    stream_count = len(streams)
    elementum_available = None
    elementum_warning_sent = False

    for stream in streams:
        stream_name = stream.get("name", "")
        # Support both 'description' (new Stremio spec) and 'title' (legacy field)
        stream_description = stream.get("description") or stream.get("title", "")
        behavior_hints = stream.get("behaviorHints", {})
        video_info = parse_stream_info(stream_name, stream_description, behavior_hints)
        stream_tagline = _stream_tagline(video_info)

        list_item = xbmcgui.ListItem(
            label=stream_name, label2=stream_tagline, offscreen=True
        )
        if episode_thumb:
            list_item.setArt({"thumb": episode_thumb, "poster": episode_thumb})
        tags = list_item.getVideoInfoTag()
        tags.setTitle(stream_name)
        tags.setPlot(stream_description)
        if stream_tagline:
            tags.setTagLine(stream_tagline)

        if is_imdb:
            tags.setIMDBNumber(imdb_id)
        if season is not None:
            tags.setSeason(season_number)
            tags.setEpisode(episode_number)
            tags.setMediaType("episode")
        else:
            tags.setMediaType("video")

        size = video_info["size"]
        if size:
            list_item.setProperty("size", str(size))

        tags.addVideoStream(
            xbmc.VideoStreamDetail(
                width=int(video_info["width"]),
                height=int(video_info["height"]),
                language=video_info["language"],
                codec=video_info["codec"],
                hdrtype=video_info["hdr"],
            )
        )
        list_item.setProperty("IsPlayable", "true")
        if episode_bookmark and episode_bookmark.get("pos", 0) > 10:
            tags.setResumePoint(episode_bookmark["pos"], episode_bookmark["total"])

        if "url" in stream:
            resolved_stream_url = stream["url"]
        elif "infoHash" in stream:
            if elementum_available is None:
                elementum_available = is_elementum_installed_and_enabled()
            if not elementum_available:
                if not elementum_warning_sent:
                    _notify_error("Elementum is required for torrent playback.")
                    elementum_warning_sent = True
                continue

            magnet_link = convert_info_hash_to_magnet(
                stream["infoHash"],
                stream.get("sources", []),
                behavior_hints.get("filename", stream_name),
            )
            file_idx = stream.get("fileIdx")
            elementum_url = "plugin://plugin.video.elementum/play?uri=" + parse.quote_plus(magnet_link)
            if file_idx is not None:
                elementum_url += f"&index={file_idx}&oindex={file_idx}"
            resolved_stream_url = (
                elementum_url
            )
        else:
            continue

        playback_params = {"video_url": resolved_stream_url}
        if is_imdb:
            playback_params["imdb"] = imdb_id
        if season is not None:
            playback_params["season"] = season
            playback_params["episode"] = episode
        if sub_id:
            playback_params["sub_id"] = sub_id
        if series_logo:
            playback_params["logo"] = series_logo
        if parent_id:
            playback_params["series_id"]  = parent_id
            playback_params["episode_id"] = video_id
        if series_name:
            playback_params["series_name"] = series_name
        if episode_title:
            playback_params["episode_title"] = episode_title
        if season_poster:
            playback_params["season_poster"] = season_poster

        stream_items.append(
            (build_url("play_video", **playback_params), list_item, False)
        )

    _add_directory_items(stream_items, stream_count)
    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)


def mark_watched(params):
    scope = params.get("scope", "episode")
    series_id = params["series_id"]
    log(f"[watched] mark_watched called scope={scope!r} series_id={series_id!r}")

    if scope == "episode":
        episode_id = params["episode_id"]
        before = _watched.get_watched(series_id)
        _watched.toggle_episode(series_id, episode_id)
        after = _watched.get_watched(series_id)
        action = "marked" if episode_id in after else "unmarked"
        log(f"[watched] episode {action}: {episode_id!r} (total watched: {len(after)})")
        if action == "marked":
            _bookmarks.clear(episode_id)
    else:
        catalog_type = params.get("catalog_type", "series")
        season_filter = int(params["season"]) if scope == "season" else None
        meta = _fetch_provider_meta(catalog_type, series_id)
        if not meta:
            log(f"[watched] mark_watched ERROR: could not fetch meta for {series_id!r}")
            return
        all_videos = meta.get("videos", ())
        all_ep_ids = [v["id"] for v in all_videos if v.get("id")]
        if all_ep_ids:
            _watched.cache_total(series_id, len(all_ep_ids))
        episode_ids = [
            v["id"]
            for v in all_videos
            if v.get("id") and (season_filter is None or v.get("season") == season_filter)
        ]
        log(f"[watched] scope={scope!r} season_filter={season_filter} found {len(episode_ids)} episodes")
        if episode_ids:
            before = _watched.get_watched(series_id)
            all_watched_before = all(eid in before for eid in episode_ids)
            _watched.toggle_batch(series_id, episode_ids)
            after = _watched.get_watched(series_id)
            action = "unmarked all" if all_watched_before else "marked all"
            log(f"[watched] {action} — {len(episode_ids)} episodes, total watched now: {len(after)}")
        else:
            log(f"[watched] mark_watched WARNING: no episode IDs found for scope={scope!r}")

    xbmc.executebuiltin("Container.Refresh")
