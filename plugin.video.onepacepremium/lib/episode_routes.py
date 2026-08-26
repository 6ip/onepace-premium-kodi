from urllib import parse

import xbmc
import xbmcgui
import xbmcplugin

from . import bookmarks as _bookmarks
from . import cache as _cache
from . import watched as _watched
from .art import (_cast_list, _episode_number, _season_thumbnails,
                   _set_episode_art, _set_episode_rating, _set_ids,
                   _set_season_art, _set_show_tags, _set_video_tags,
                   _stream_tagline)
from .parser import parse_stream_info
from .provider_api import (_compose_url, _fetch_provider_meta, countable_episode_ids,
                            episode_params, episode_play_url,
                            _parse_air_date, _parse_release_year,
                            _parse_runtime_seconds)
from .route_common import (_add_directory_items, _notify_error, _notify_info,
                            end_directory)
from .utils import (ADDON_HANDLE, ALERT_ICON, build_url,
                     convert_info_hash_to_magnet, ensure_configured,
                     fetch_data, get_base_url, get_config_prefix,
                     get_secret_string,
                     get_setting, is_elementum_installed_and_enabled, log,
                     refresh_container)

# Notice cards that sit in an episode slot but aren't episodes.
_NOTICE_ID_PREFIX = "pp_COMPLETE"

# bingeGroup is "onepace|<service>|<version>". The service preference stores the
# code directly; the version is an enum.
_VERSION_BY_INDEX = {"1": "standard", "2": "extended"}

# bingeGroup service codes and their names, in the order the settings picker
# lists them. tools.py holds the same pairs but runs its dispatch on import,
# so it cannot be the one place they live.
_SERVICES = (("rd", "Real-Debrid"), ("pm", "Premiumize"), ("dl", "DebridLink"),
             ("tb", "TorBox"), ("ad", "AllDebrid"), ("p2p", "Torrent"))
_SERVICE_NAMES = dict(_SERVICES)
_SERVICE_ORDER = {code: i for i, (code, _) in enumerate(_SERVICES)}


def _binge_part(group, index):
    parts = str(group or "").split("|")
    return parts[index] if len(parts) > index else ""


def _preferred_streams(groups, prefer=None):
    """Narrow the streams to the preferred service and version.

    A preference that matches nothing is ignored rather than leaving no
    choices, and a missing version falls back to the standard cut. prefer
    overrides the settings, which is how one answer covers a whole season.
    """
    indexes = list(range(len(groups)))

    if prefer:
        service, version = prefer
    else:
        service = get_setting("preferred_service")
        version = _VERSION_BY_INDEX.get(get_setting("preferred_version"))
    if service:
        matched = [i for i in indexes if _binge_part(groups[i], 1) == service]
        if matched:
            indexes = matched

    if version:
        matched = [i for i in indexes if _binge_part(groups[i], 2) == version]
        if not matched and version != "standard":
            matched = [i for i in indexes if _binge_part(groups[i], 2) == "standard"]
        if matched:
            indexes = matched

    return indexes


def _episode_label(title, season, episode, episode_id):
    """"1x01. Title", except where a number would be noise."""
    if (get_setting("episode_title_format") == "1" or season == 0
            or str(episode_id).startswith(_NOTICE_ID_PREFIX)):
        return title
    return f"{season}x{int(episode):02d}. {title}"


def list_seasons(params):
    if not ensure_configured():
        end_directory(succeeded=False)
        return

    catalog_type = params["catalog_type"]
    video_id = params["video_id"]

    meta = _fetch_provider_meta(catalog_type, video_id)
    if not meta:
        end_directory(succeeded=False)
        return

    videos = meta.get("videos", ())
    if not videos:
        _notify_error("No seasons available")
        end_directory(succeeded=False)
        return

    xbmcplugin.setContent(ADDON_HANDLE, "seasons")

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
    if get_setting("show_specials") == "false":
        seasons = [season for season in seasons if season != 0]
    elif 0 in seasons:
        seasons = [season for season in seasons if season != 0] + [0]

    show_title = meta.get("name") or ""
    series_watched = _watched.get_watched(video_id)

    with_unwatched = set()
    if get_setting("hide_watched") == "true":
        # Same rule the episode list uses, so a season on show is never empty.
        for v in videos:
            number = _episode_number(v)
            if number is None:
                continue
            season = v.get("season")
            eid = v.get("id") or f"{video_id}:{season}:{number}"
            if eid not in series_watched:
                with_unwatched.add(season)
        # Checked before flattening, so a one-season show names itself here.
        if not any(s in with_unwatched for s in seasons):
            _notify_info(f"{show_title or 'Series'} fully watched")
            end_directory()
            return

    if len(seasons) == 1 and get_setting("flatten_single_season") == "true":
        return list_episodes({**params, "season": str(seasons[0])})

    all_ep_ids = countable_episode_ids(meta)
    if all_ep_ids:
        _watched.cache_total(video_id, len(all_ep_ids))

    # Pre-build per-season episode ID lists for count display
    season_ep_ids = {}
    for v in videos:
        s = v.get("season")
        eid = v.get("id")
        if s is not None and eid:
            season_ep_ids.setdefault(s, []).append(eid)

    if with_unwatched:
        seasons = [s for s in seasons if s in with_unwatched]

    if show_title:
        xbmcplugin.setPluginCategory(ADDON_HANDLE, show_title)

    series_actors = _cast_list(meta)
    from .downloads import enabled as _downloads_enabled
    downloads_on = _downloads_enabled()
    items = []
    for season in seasons:
        label = "Specials" if season == 0 else f"Season {season}"
        list_item = xbmcgui.ListItem(label=label, offscreen=True)
        tags = list_item.getVideoInfoTag()
        _set_video_tags(tags, meta, label)
        tags.setTvShowTitle(show_title)
        tags.setSeason(season)
        _set_show_tags(tags, meta, premiered=False, trailer=False,
                       actors=series_actors)
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
        season_ctx_label = "[B]Mark Unwatched[/B]" if season_fully_watched else "[B]Mark Watched[/B]"
        season_menu = [(
            season_ctx_label,
            f"RunPlugin({build_url('mark_watched', scope='season', series_id=video_id, catalog_type=catalog_type, season=season)})",
        )]
        if downloads_on:
            season_menu.append((
                "[B]Download Season[/B]",
                f"RunPlugin({build_url('download_season', catalog_type=catalog_type, video_id=video_id, season=season)})",
            ))
        list_item.addContextMenuItems(season_menu)

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
    end_directory()


def list_episodes(params):
    if not ensure_configured():
        end_directory(succeeded=False)
        return

    catalog_type = params["catalog_type"]
    video_id = params["video_id"]
    selected_season = int(params["season"])

    meta = _fetch_provider_meta(catalog_type, video_id)
    if not meta:
        end_directory(succeeded=False)
        return

    videos = meta.get("videos", ())
    if not videos:
        _notify_error("No episodes available")
        end_directory(succeeded=False)
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

    if show_title:
        xbmcplugin.setPluginCategory(ADDON_HANDLE, show_title)
    series_actors = _cast_list(meta)
    hide_watched = get_setting("hide_watched") == "true"
    from .downloads import downloaded_ids, enabled, mark as _download_mark
    on_disk, downloads_on = downloaded_ids(), enabled()
    items = []
    n_watched = n_resume = n_hidden = 0
    for video in season_videos:
        episode_number = _episode_number(video)
        if episode_number is None:
            continue

        # Compute episode ID early — needed for watched check and context menu.
        stream_video_id = video.get("id") or f"{video_id}:{selected_season}:{episode_number}"

        if hide_watched and stream_video_id in series_watched:
            n_hidden += 1
            continue

        title = video.get("name") or video.get("title") or f"Episode {episode_number}"
        label = _download_mark(
            _episode_label(title, selected_season, episode_number, stream_video_id),
            stream_video_id, on_disk)
        list_item = xbmcgui.ListItem(label=label, offscreen=True)
        tags = list_item.getVideoInfoTag()
        _set_ids(tags, video_id)
        tags.setTitle(title)
        tags.setTvShowTitle(show_title)
        tags.setSeason(selected_season)
        tags.setEpisode(int(episode_number))

        tags.setMediaType("episode")
        # Watched wins: no resume bar. The bookmark is still looked up so a
        # stale one left by an older version can be cleared from the menu.
        bm = _bookmarks.get(stream_video_id)
        if stream_video_id in series_watched:
            tags.setPlaycount(1)
            n_watched += 1
        else:
            if bm:
                pos, total = bm.get("pos", 0), bm.get("total", 0)
                if total > 0:
                    pct = min(99, max(1, int(pos / total * 100)))
                    list_item.setProperty("WatchedProgress", str(pct))
                    list_item.setProperty("PercentPlayed", str(pct))
                    tags.setResumePoint(pos, total)
                    n_resume += 1

        plot = video.get("overview") or meta_description
        if plot:
            tags.setPlot(plot)

        release_year = _parse_release_year(video.get("released") or meta_release_info)
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

        runtime = _parse_runtime_seconds(video)
        if runtime:
            tags.setDuration(runtime)

        if meta_genres:
            tags.setGenres(meta_genres)

        _set_episode_rating(tags, video)

        _set_show_tags(tags, meta, premiered=False, trailer=False, actors=series_actors)

        list_item.setProperty("IsPlayable", "true")
        if stream_video_id in on_disk:
            list_item.setProperty("Downloaded", "true")
        _set_episode_art(list_item, video, meta, season_poster)
        ep_ctx_label = "[B]Mark Unwatched[/B]" if stream_video_id in series_watched else "[B]Mark Watched[/B]"
        ctx_items = [(
            ep_ctx_label,
            f"RunPlugin({build_url('mark_watched', scope='episode', series_id=video_id, episode_id=stream_video_id)})",
        )]
        if bm:
            ctx_items.append((
                "[B]Clear Progress[/B]",
                f"RunPlugin({build_url('clear_progress', episode_id=stream_video_id)})",
            ))
        if downloads_on:
            ctx_items.append((
            "[B]Download[/B]",
                f"RunPlugin({build_url('download_episode', **episode_params(video, meta, video_id, catalog_type, season_poster, stream_video_id))})",
            ))
        list_item.addContextMenuItems(ctx_items, replaceItems=True)
        items.append(
            (
                episode_play_url(video, meta, video_id, catalog_type,
                                 season_poster, stream_video_id),
                list_item,
                False,
            )
        )

    if not items:
        # Hiding every episode is the setting working, not a failure.
        if n_hidden:
            where = "Specials" if selected_season == 0 else f"Season {selected_season}"
            _notify_info(f"{where} fully watched")
            end_directory()
        else:
            _notify_error("No episodes available")
            end_directory(succeeded=False)
        return

    log(f"[list] {video_id} s{selected_season}: {len(items)} rows, "
        f"{n_watched}/{len(series_watched)} watched, {n_resume} resumable")
    _add_directory_items(items)
    end_directory(cache=True)


def _choose_stream(params, downloadable_only=False, quiet=False, prefer=None,
                   survey=False):
    """Fetch this episode's streams and return the one to act on, or None.

    Playing and downloading want the same list and the same picker, so the
    only difference is whether a magnet is allowed through. quiet is for a
    season queue: it takes the preferred stream and says nothing, since one
    dialog per episode is thirty dialogs.
    """
    if not ensure_configured():
        return None

    if not get_secret_string():
        xbmcgui.Dialog().ok(
            "One Pace Premium",
            "Add-on is not configured.\nPlease set up your configuration first."
        )
        xbmc.executebuiltin("Addon.OpenSettings(plugin.video.onepacepremium)")
        return None

    catalog_type = params["catalog_type"]
    video_id     = params["video_id"]
    episode_thumb = params.get("thumb", "")
    series_logo   = params.get("logo", "")
    parent_id     = params.get("parent_id", "")
    series_name   = params.get("series_name", "")
    episode_title = params.get("episode_title", "")
    season_poster = params.get("season_poster", "")
    episode_plot  = params.get("episode_plot", "")

    stream_url = _compose_url(
        get_base_url(),
        f"{get_config_prefix()}stream/{catalog_type}/{video_id}.json?kodi=1",
    )
    response = _cache.get(stream_url)
    if response is None:
        response = fetch_data(stream_url)
        if not response:
            return None
        _cache.set(stream_url, response, 3600)

    streams = response.get("streams", ())
    if not streams:
        _notify_error("No streams available")
        return None

    # Detect server-side configuration error (externalUrl with no playable url/infoHash)
    config_error = next(
        (s for s in streams if s.get("externalUrl") and "url" not in s and "infoHash" not in s),
        None
    )
    if config_error:
        xbmcgui.Dialog().ok(
            "One Pace Premium",
            "Your configuration key is invalid or not recognized by the server.\n\n"
            "Add-on settings will now open — please enter a valid configuration key."
        )
        xbmc.executebuiltin("Addon.OpenSettings(plugin.video.onepacepremium)")
        return None

    id_parts = video_id.split(":", 2)
    if len(id_parts) == 3:
        imdb_id, season, episode = id_parts
    else:
        imdb_id = video_id
        season  = params.get("season")
        episode = params.get("episode")
    is_imdb = imdb_id.startswith("tt")
    sub_id = video_id if not is_imdb and ":" not in video_id else ""
    if sub_id.startswith("pp_"):
        sub_id = sub_id[3:]

    elementum_available   = None
    elementum_warning_sent = False
    valid_streams  = []
    dialog_labels  = []
    binge_groups   = []

    for stream in streams:
        stream_name    = stream.get("name", "")
        stream_desc    = stream.get("description") or stream.get("title", "")
        behavior_hints = stream.get("behaviorHints", {})
        video_info     = parse_stream_info(stream_name, stream_desc, behavior_hints)
        stream_tagline = _stream_tagline(video_info)

        if "url" in stream:
            resolved_url = stream["url"]
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
            # Resume is ours (bookmarks + Kodi's dialog); stop Elementum asking too.
            elementum_url += "&doresume=false"
            resolved_url = elementum_url
        else:
            continue

        playback_params = {"video_url": resolved_url}
        if is_imdb:
            playback_params["imdb"] = imdb_id
        if season is not None:
            playback_params["season"]   = season
            playback_params["episode"]  = episode
        if sub_id:
            playback_params["sub_id"]   = sub_id
        if series_logo:
            playback_params["logo"]     = series_logo
        if parent_id:
            playback_params["series_id"]  = parent_id
            playback_params["episode_id"] = video_id
        if series_name:
            playback_params["series_name"]  = series_name
        if episode_title:
            playback_params["episode_title"] = episode_title
        if season_poster:
            playback_params["season_poster"] = season_poster
        if episode_thumb:
            playback_params["thumb"] = episode_thumb

        if params.get("autoplay"):
            playback_params["autoplay"] = "1"

        playback_params["stream_name"] = stream_name
        playback_params["stream_desc"] = stream_desc
        if episode_plot:
            playback_params["episode_plot"] = episode_plot
        # What the provider knows about this exact file, for downloading.
        playback_params["filename"] = video_info["filename"]
        playback_params["video_size"] = video_info["size"]
        playback_params["duration"] = video_info["duration"]
        playback_params["variant"] = _binge_part(behavior_hints.get("bingeGroup"), 2)
        playback_params["service"] = _binge_part(behavior_hints.get("bingeGroup"), 1)

        label = stream_name
        if stream_tagline:
            label += f"  [{stream_tagline}]"

        valid_streams.append(playback_params)
        dialog_labels.append(label)
        binge_groups.append(behavior_hints.get("bingeGroup", ""))

    if not valid_streams:
        _notify_error("No streams available")
        return None

    if survey:
        # What this episode could offer, before any preference narrows it.
        from .downloads import is_downloadable
        return sorted({(valid_streams[i].get("service", ""),
                        valid_streams[i].get("variant", "") or "standard")
                       for i in range(len(valid_streams))
                       if is_downloadable(valid_streams[i]["video_url"])})

    choices = _preferred_streams(binge_groups, prefer)

    # The copy on disk goes first, so it is never hidden behind a preference
    # that narrowed the list down to one remote stream.
    if not downloadable_only:
        from .downloads import local_options
        held = local_options(params.get("video_id", ""))
        for offset, (label, fields) in enumerate(held):
            valid_streams.insert(offset, fields)
            dialog_labels.insert(offset, label)
        if held:
            shift = len(held)
            choices = list(range(shift)) + [i + shift for i in choices]

    if downloadable_only:
        from .downloads import is_downloadable
        choices = [i for i in choices
                   if is_downloadable(valid_streams[i]["video_url"])]
        if not choices:
            if not quiet:
                _notify_error("No stream here can be downloaded")
            return None
    log(f"[streams] {len(valid_streams)} available, {len(choices)} after preferences")
    if len(choices) == 1 or quiet:
        selected = choices[0]
    else:
        pick = xbmcgui.Dialog().select(
            "Download" if downloadable_only else "Select Stream",
            [dialog_labels[i] for i in choices],
        )
        if pick < 0:
            return None
        selected = choices[pick]

    chosen = valid_streams[selected]
    # How many there were to choose between. A season only remembers an answer
    # that was actually given, so one episode with a single cut cannot decide
    # for a later one that has two.
    chosen["stream_choices"] = len(choices)
    return chosen


def check_resume(params):
    from .downloads import local_playback

    # Already on disk, so there is nothing to ask a provider for.
    chosen = local_playback(params.get("video_id", ""))
    if chosen:
        log(f"[downloads] playing the copy on disk for {params.get('video_id')!r}")
    else:
        chosen = _choose_stream(params)
    if chosen is None:
        xbmcplugin.setResolvedUrl(ADDON_HANDLE, False, xbmcgui.ListItem())
        return
    from .playback import play_video as _play_video
    _play_video(chosen)


def _season_episodes(meta, video_id, catalog_type, season):
    """Every episode of one season, in order, with what a download needs."""
    from .provider_api import episode_params

    season_poster = next(
        (s["poster"] for s in meta.get("seasons", ())
         if s.get("season") == season and s.get("poster")), "")
    picks = []
    for video in sorted((v for v in meta.get("videos", ())
                         if v.get("season") == season),
                        key=lambda v: _episode_number(v) or 0):
        number = _episode_number(video)
        if number is None:
            continue
        episode_id = video.get("id") or f"{video_id}:{season}:{number}"
        if str(episode_id).startswith(_NOTICE_ID_PREFIX):
            continue
        picks.append((episode_id, _episode_label(
            video.get("name") or video.get("title") or f"Episode {number}",
            season, number, episode_id),
            episode_params(video, meta, video_id, catalog_type,
                           season_poster, episode_id)))
    return picks


def _season_preference(picks, label):
    """Ask once what the whole run should prefer, or None if cancelled.

    Looking at every chosen episode first, not just the first one: a season
    where only episode two has an extended cut must still offer it, and one
    where every episode has the same single stream must not ask at all.
    """
    services, versions = set(), set()
    busy = xbmcgui.DialogProgressBG()
    busy.create(f"Checking {label}")
    try:
        for index, episode in enumerate(picks, 1):
            busy.update(int(index * 100 / len(picks)), f"Checking {label}",
                        episode.get("episode_title", ""))
            for service, version in _choose_stream(
                    episode, downloadable_only=True, survey=True) or ():
                services.add(service)
                versions.add(version)
    finally:
        busy.close()

    want_service, want_version = "", ""
    if len(services) > 1 and not get_setting("preferred_service"):
        # Same order as Preferred Service, so the two lists read alike.
        ordered = sorted(services, key=lambda c: (_SERVICE_ORDER.get(c, 99), c))
        pick = xbmcgui.Dialog().select(
            f"{label} — which service?",
            [_SERVICE_NAMES.get(c, c.upper()) for c in ordered])
        if pick < 0:
            return None
        want_service = ordered[pick]

    if len(versions) > 1 and not _VERSION_BY_INDEX.get(get_setting("preferred_version")):
        from .downloads import variant_label
        ordered = sorted(versions, key=lambda v: (v != "standard", v))
        pick = xbmcgui.Dialog().select(
            f"{label} — which cut?", [variant_label(v) for v in ordered])
        if pick < 0:
            return None
        want_version = ordered[pick]

    log(f"[downloads] {label} will prefer {(want_service, want_version)}")
    return want_service, want_version


def download_season(params):
    """Fetch a whole season, one episode at a time.

    Sequential on purpose: a debrid account rate-limits parallel connections,
    and one writer means the download index cannot be raced.
    """
    from .downloads import (BLOCKED, OK, download, enabled, mark as _download_mark,
                            downloaded_ids)

    if not enabled() or not ensure_configured():
        return

    catalog_type = params.get("catalog_type", "series")
    video_id = params["video_id"]
    season = int(params["season"])
    meta = _fetch_provider_meta(catalog_type, video_id)
    if not meta:
        _notify_error("Could not read the season")
        return
    show_title = meta.get("name") or ""

    episodes = _season_episodes(meta, video_id, catalog_type, season)
    if not episodes:
        _notify_error("Nothing to download in this season")
        return

    # Anything already on disk starts unticked, so the usual press re-fetches
    # nothing and the choice is still there when a copy is wanted again.
    on_disk = downloaded_ids()
    labels = [_download_mark(label, episode_id, on_disk, plain=True)
              for episode_id, label, _ in episodes]
    preselect = [i for i, (episode_id, _, _) in enumerate(episodes)
                 if episode_id not in on_disk]
    label = "Specials" if season == 0 else f"Season {season}"
    chosen = xbmcgui.Dialog().multiselect(f"Download {label}", labels,
                                          preselect=preselect)
    if not chosen:
        return

    prefer = _season_preference([episodes[pick][2] for pick in chosen], label)
    if prefer is None:
        return

    done = skipped = failed = 0
    progress = xbmcgui.DialogProgressBG()
    progress.create(f"Downloading {label}")
    kodi_monitor = xbmc.Monitor()
    for index, pick in enumerate(chosen, 1):
        if kodi_monitor.abortRequested():
            break
        episode_id, title, episode = episodes[pick]
        progress.update(int((index - 1) * 100 / len(chosen)),
                        f"{label} — {index} of {len(chosen)}", title)
        stream = _choose_stream(episode, downloadable_only=True, quiet=True,
                                prefer=prefer)
        if stream is None:
            skipped += 1
            log(f"[downloads] no stream to download for {episode_id!r}")
            continue
        outcome = download(dict(stream, in_season=True), meta)
        if outcome == BLOCKED:
            # One message beats the same one for every episode left.
            progress.close()
            log(f"[downloads] season download stopped at {episode_id!r}")
            return
        if outcome == OK:
            done += 1
        else:
            # A blip on one episode is no reason to abandon the rest.
            failed += 1
    progress.close()

    parts = [f"downloaded {done}"]
    if skipped:
        parts.append(f"{skipped} with no stream")
    if failed:
        parts.append(f"{failed} failed")
    _notify_info(f"{label}: " + ", ".join(parts))
    log(f"[downloads] {label}: {done} done, {skipped} skipped, {failed} failed")

    from .downloads import write_report
    report = [f"Downloaded {done} of {len(chosen)} picked"]
    if skipped:
        report.append(f"{skipped} had no stream we could fetch")
    if failed:
        report.append(f"{failed} could not be downloaded")
    if prefer and any(prefer):
        report.append(f"Cut used: {prefer[1] or 'standard'}")
    write_report(f"{show_title or 'Downloads'} — {label}", report)


def download_episode(params):
    """Context-menu target on an episode row: pick a stream, then keep it."""
    chosen = _choose_stream(params, downloadable_only=True)
    if chosen is None:
        return
    from .downloads import download
    # Fetched while we are certainly online, so the list still looks right
    # when the connection is gone.
    meta = _fetch_provider_meta(params.get("catalog_type", "series"),
                                params.get("parent_id", "")) or {}
    download(chosen, meta)


def get_streams(params):
    if not ensure_configured():
        end_directory(succeeded=False)
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
        end_directory()
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
            end_directory(succeeded=False)
            return
        _cache.set(stream_url, response, 3600)

    streams = response.get("streams", ())
    if not streams:
        _notify_error("No streams available")
        end_directory(succeeded=False)
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
            # Resume is ours (bookmarks + Kodi's dialog); stop Elementum asking too.
            elementum_url += "&doresume=false"
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
        if episode_thumb:
            playback_params["thumb"] = episode_thumb

        stream_items.append(
            (build_url("play_video", **playback_params), list_item, False)
        )

    _add_directory_items(stream_items, stream_count)
    end_directory()


def _get_kodi_episode_file_ids(cur, episode_id):
    cur.execute(
        "SELECT idFile FROM files WHERE strFilename LIKE ? AND "
        "(strFilename LIKE ? OR strFilename LIKE ? OR "
        " strFilename LIKE ? OR strFilename LIKE ?)",
        (
            "%plugin.video.onepacepremium%",
            f"%video_id={episode_id}&%",
            f"%video_id={episode_id}",
            f"%episode_id={episode_id}&%",
            f"%episode_id={episode_id}",
        )
    )
    return [str(r[0]) for r in cur.fetchall()]


def _kodi_db_connect():
    import glob as _glob
    import os as _os
    import sqlite3
    import xbmcvfs
    db_dir = xbmcvfs.translatePath("special://profile/Database/")
    db_files = sorted(_glob.glob(_os.path.join(db_dir, "MyVideos*.db")), reverse=True)
    if not db_files:
        return None
    return sqlite3.connect(db_files[0])


def _bulk_kodi_update(episode_ids, marking_watched):
    """Bulk-update Kodi's DB for a set of episodes in one transaction.
    If marking_watched: set playCount=1, clear bookmark + streamdetails.
    If marking_unwatched: set playCount=0.
    """
    if not episode_ids:
        return
    try:
        con = _kodi_db_connect()
        if not con:
            return
        cur = con.cursor()
        cur.execute(
            "SELECT idFile, strFilename FROM files WHERE strFilename LIKE ?",
            ("%plugin.video.onepacepremium%",)
        )
        all_files = cur.fetchall()

        ep_id_set = set(episode_ids)
        file_ids = []
        for fid, fname in all_files:
            for ep_id in ep_id_set:
                if (f"video_id={ep_id}&" in fname or fname.endswith(f"video_id={ep_id}") or
                        f"episode_id={ep_id}&" in fname or fname.endswith(f"episode_id={ep_id}")):
                    file_ids.append(str(fid))
                    break

        if file_ids:
            ph = ",".join(file_ids)
            playcount = 1 if marking_watched else 0
            cur.execute(f"UPDATE files SET playCount=? WHERE idFile IN ({ph})", (playcount,))
            cur.execute(f"DELETE FROM bookmark WHERE idFile IN ({ph})")
            cur.execute(f"DELETE FROM streamdetails WHERE idFile IN ({ph})")
            con.commit()
            log(f"[watched] bulk Kodi update playCount={playcount} for {len(file_ids)} file(s)")

        cur.close()
        con.close()
    except Exception as e:
        log(f"[watched] bulk Kodi update error: {e}")


def _update_kodi_episode_playcount(episode_id, playcount):
    """Sync Kodi's own watched state for this episode (files.playCount in MyVideos.db)."""
    try:
        con = _kodi_db_connect()
        if not con:
            return
        cur = con.cursor()
        file_ids = _get_kodi_episode_file_ids(cur, episode_id)
        if file_ids:
            ph = ",".join(file_ids)
            cur.execute(f"UPDATE files SET playCount=? WHERE idFile IN ({ph})", (playcount,))
            con.commit()
            log(f"[watched] set Kodi playCount={playcount} for {episode_id!r} ({cur.rowcount} rows)")
        cur.close()
        con.close()
    except Exception as e:
        log(f"[watched] Kodi playCount update error: {e}")


def _clear_kodi_episode_state(episode_id, tables=("bookmark", "streamdetails")):
    """Drop Kodi's per-episode rows for one episode.

    Kodi only writes streamdetails when the row is empty, so clearing it before
    playback is what keeps the codec badges current. The files row holds
    playCount, so only the full reset in clear_progress includes it.
    """
    try:
        con = _kodi_db_connect()
        if not con:
            return
        cur = con.cursor()
        file_ids = _get_kodi_episode_file_ids(cur, episode_id)
        if file_ids:
            ph = ",".join(file_ids)
            for table in tables:
                cur.execute(f"DELETE FROM {table} WHERE idFile IN ({ph})")
            con.commit()
            log(f"[watched] cleared {'+'.join(tables)} for {episode_id!r}")
        cur.close()
        con.close()
    except Exception as e:
        log(f"[watched] Kodi state clear error: {e}")


def episode_has_stream(catalog_type, video_id):
    """True if this episode has at least one stream we could actually play."""
    stream_url = _compose_url(
        get_base_url(),
        f"{get_config_prefix()}stream/{catalog_type}/{video_id}.json?kodi=1",
    )
    response = _cache.get(stream_url)
    if response is None:
        response = fetch_data(stream_url)
        if response:
            _cache.set(stream_url, response, 3600)
    if not response:
        return False
    return any(
        "url" in stream or "infoHash" in stream
        for stream in response.get("streams", ())
    )


def kodi_episode_has_bookmark(episode_id):
    """True if Kodi still holds a resume point for this episode."""
    try:
        con = _kodi_db_connect()
        if not con:
            return False
        cur = con.cursor()
        file_ids = _get_kodi_episode_file_ids(cur, episode_id)
        found = False
        if file_ids:
            ph = ",".join(file_ids)
            cur.execute(f"SELECT 1 FROM bookmark WHERE idFile IN ({ph}) LIMIT 1")
            found = cur.fetchone() is not None
        cur.close()
        con.close()
        return found
    except Exception:
        return False


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
        # Both ways: marking means "seen it", unmarking means "start over".
        # Leaving the resume point behind makes an unmarked episode come back
        # part-watched, which is neither.
        _bookmarks.clear(episode_id)
        _clear_kodi_episode_state(episode_id)
        _update_kodi_episode_playcount(episode_id, 1 if action == "marked" else 0)
    else:
        catalog_type = params.get("catalog_type", "series")
        season_filter = int(params["season"]) if scope == "season" else None
        meta = _fetch_provider_meta(catalog_type, series_id)
        if not meta:
            log(f"[watched] mark_watched ERROR: could not fetch meta for {series_id!r}")
            return
        all_videos = meta.get("videos", ())
        all_ep_ids = countable_episode_ids(meta)
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
            marking_watched = not all_watched_before
            action = "unmarked all" if all_watched_before else "marked all"
            log(f"[watched] {action} — {len(episode_ids)} episodes, total watched now: {len(after)}")
            for eid in episode_ids:
                _bookmarks.clear(eid)
            _bulk_kodi_update(episode_ids, marking_watched)
        else:
            log(f"[watched] mark_watched WARNING: no episode IDs found for scope={scope!r}")

    refresh_container()


def clear_progress(params):
    episode_id = params["episode_id"]
    _bookmarks.clear(episode_id)
    _clear_kodi_episode_state(episode_id, ("bookmark", "streamdetails", "files"))
    log(f"[progress] reset Kodi state for {episode_id!r}")
    refresh_container()
