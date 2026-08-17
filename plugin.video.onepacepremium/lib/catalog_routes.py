from urllib import parse

import xbmcgui
import xbmcplugin

from . import watched as _watched
from .art import _set_art, _set_ids, _set_show_tags, _set_video_tags
from .provider_api import (_catalog_priority, _catalog_specs, _catalog_url,
                            _fetch_catalog, _fetch_provider_manifest,
                            _fetch_provider_meta, _prefetch_metas,
                            countable_episode_ids)
from .route_common import _add_directory_items, _notify_error, end_directory
from .utils import ADDON_DIR, ADDON_ID, ADDON_HANDLE, build_url, ensure_configured, fetch_data

CATALOG_PAGE_SIZE = 25
SUPPORTED_CATALOG_TYPES = {"movie", "series", "anime"}


def _process_catalog_items(videos: list, catalog_type: str):
    xbmcplugin.setContent(
        ADDON_HANDLE, "movies" if catalog_type == "movie" else "tvshows"
    )

    action = "list_seasons" if catalog_type == "series" else "get_streams"
    items = []

    for video in videos:
        video_id = video["id"]
        video_name = video["name"]
        list_item = xbmcgui.ListItem(label=video_name, offscreen=True)

        tags = list_item.getVideoInfoTag()
        _set_ids(tags, video_id)
        _set_video_tags(tags, video, video_name)
        _set_art(list_item, video)

        items.append(
            (
                build_url(action, catalog_type=catalog_type, video_id=video_id),
                list_item,
                True,
            )
        )

    _add_directory_items(items)


def list_root():
    if not ensure_configured():
        end_directory(succeeded=False)
        return

    import os as _os
    _skin_media = f"special://home/addons/{ADDON_ID}/resources/skins/Default/media"
    _fanart     = f"special://home/addons/{ADDON_ID}/resources/fanart.png"

    def _nav_item(label, icon):
        item = xbmcgui.ListItem(label=label, offscreen=True)
        item.setArt({"icon": icon, "thumb": icon, "poster": icon,
                     "fanart": _fanart, "banner": icon, "landscape": icon})
        item.getVideoInfoTag().setPlot("​")
        return item

    xbmcplugin.setContent(ADDON_HANDLE, "")
    items = [
        (build_url("list_my_lists"), _nav_item("My Lists", f"{_skin_media}/lists2.png"),  True),
        (build_url("list_browse"),   _nav_item("Browse",   f"{_skin_media}/hat.png"),     True),
        # Not a folder — it opens the settings window instead of navigating.
        (build_url("open_addon_settings"),
         _nav_item("Settings", f"{_skin_media}/settings.png"), False),
    ]
    _add_directory_items(items)
    end_directory(cache=True)


def list_browse(params):
    if not ensure_configured():
        end_directory(succeeded=False)
        return

    manifest = _fetch_provider_manifest()
    if not manifest:
        end_directory()
        return

    series_specs = _catalog_specs(manifest, "series")
    if not series_specs:
        _notify_error("No compatible catalogs found")
        end_directory()
        return

    spec = series_specs[0]
    catalog_type = "series"
    catalog_id = spec["id"]

    xbmcplugin.setContent(ADDON_HANDLE, "tvshows")
    xbmcplugin.setPluginCategory(ADDON_HANDLE, "Browse")

    response = _fetch_catalog(_catalog_url(catalog_type, catalog_id, "skip=0"))
    if not response:
        end_directory()
        return

    videos = response.get("metas", ())
    series_stats = _watched.get_all_series_stats()

    _prefetch_metas(
        catalog_type,
        [v["id"] for v in videos],
    )

    items = []

    for video in videos:
        video_id = video["id"]
        video_name = video["name"]
        list_item = xbmcgui.ListItem(label=video_name, offscreen=True)
        tags = list_item.getVideoInfoTag()
        _set_ids(tags, video_id)
        _set_video_tags(tags, video, video_name)
        _set_art(list_item, video)
        s_meta = _fetch_provider_meta(catalog_type, video_id)
        watched_count, total = series_stats.get(video_id, (0, None))
        if s_meta:
            _set_show_tags(tags, s_meta)
            # Count from the meta so hiding specials changes the total too.
            countable = set(countable_episode_ids(s_meta))
            total = len(countable) or None
            watched_count = len(_watched.get_watched(video_id) & countable)
            if total:
                _watched.cache_total(video_id, total)
        if total:
            props = {
                "UnWatchedEpisodes": str(max(0, total - watched_count)),
                "TotalEpisodes": str(total),
            }
            if watched_count > 0:
                props["WatchedEpisodes"] = str(watched_count)
            list_item.setProperties(props)
            if watched_count >= total:
                tags.setPlaycount(1)
        tags.setMediaType("tvshow")
        series_ctx_label = "[B]Mark Unwatched[/B]" if (total and watched_count >= total) else "[B]Mark Watched[/B]"
        list_item.addContextMenuItems([(
            series_ctx_label,
            f"RunPlugin({build_url('mark_watched', scope='series', series_id=video_id, catalog_type=catalog_type)})",
        )])
        items.append(
            (
                build_url("list_seasons", catalog_type=catalog_type, video_id=video_id),
                list_item,
                True,
            )
        )

    _add_directory_items(items)
    end_directory()


def list_catalog_type(params):
    if not ensure_configured():
        end_directory(succeeded=False)
        return

    catalog_type = params["catalog_type"]
    if catalog_type not in SUPPORTED_CATALOG_TYPES:
        _notify_error("Unsupported catalog type")
        end_directory(succeeded=False)
        return

    manifest = _fetch_provider_manifest()
    if not manifest:
        end_directory(succeeded=False)
        return

    specs = _catalog_specs(manifest, catalog_type)
    if not specs:
        _notify_error("No catalogs available")
        end_directory(succeeded=False)
        return

    specs.sort(key=lambda spec: (_catalog_priority(spec["name"]), spec["name"].lower()))
    search_catalog_id = next((spec["id"] for spec in specs if spec["has_search"]), None)

    items = []
    if search_catalog_id is not None:
        items.append(
            (
                build_url(
                    "search_catalog",
                    catalog_type=catalog_type,
                    catalog_id=search_catalog_id,
                ),
                xbmcgui.ListItem(label="Search"),
                True,
            )
        )

    seen_labels = set()
    for spec in specs:
        label = spec["name"]
        if label in seen_labels:
            label = f"{label} ({spec['id']})"
        seen_labels.add(label)

        items.append(
            (
                build_url(
                    "list_catalog",
                    catalog_type=catalog_type,
                    catalog_id=spec["id"],
                ),
                xbmcgui.ListItem(label=label),
                True,
            )
        )

    _add_directory_items(items)
    end_directory(cache=True)


def list_catalog(params):
    if not ensure_configured():
        end_directory(succeeded=False)
        return

    catalog_type = params["catalog_type"]
    catalog_id = params["catalog_id"]
    skip = int(params.get("skip", "0"))

    response = _fetch_catalog(_catalog_url(catalog_type, catalog_id, f"skip={skip}"))
    if not response:
        end_directory(succeeded=False)
        return

    videos = response.get("metas", ())
    if not videos:
        _notify_error("No videos available")
        end_directory(succeeded=False)
        return

    _process_catalog_items(videos, catalog_type)

    if len(videos) >= CATALOG_PAGE_SIZE:
        _add_directory_items(
            [
                (
                    build_url(
                        "list_catalog",
                        catalog_type=catalog_type,
                        catalog_id=catalog_id,
                        skip=skip + len(videos),
                    ),
                    xbmcgui.ListItem(label="Next Page"),
                    True,
                )
            ]
        )

    end_directory()


def search_catalog(params):
    if not ensure_configured():
        end_directory(succeeded=False)
        return

    query = xbmcgui.Dialog().input("Search", type=xbmcgui.INPUT_ALPHANUM)
    if not query:
        end_directory(succeeded=False)
        return

    catalog_type = params["catalog_type"]
    catalog_id = params["catalog_id"]
    response = fetch_data(
        _catalog_url(catalog_type, catalog_id, f"search={parse.quote(query, safe='')}")
    )
    if not response:
        end_directory(succeeded=False)
        return

    videos = response.get("metas", ())
    if not videos:
        _notify_error("No results found")
        end_directory(succeeded=False)
        return

    _process_catalog_items(videos, catalog_type)
    end_directory()
