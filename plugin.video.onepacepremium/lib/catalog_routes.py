from urllib import parse

import xbmcgui
import xbmcplugin

from . import watched as _watched
from .art import _set_art, _set_ids, _set_video_tags
from .provider_api import (_catalog_priority, _catalog_specs, _catalog_url,
                            _fetch_catalog, _fetch_provider_manifest,
                            _fetch_provider_meta)
from .route_common import _add_directory_items, _notify_error
from .utils import ADDON_HANDLE, build_url, ensure_configured, fetch_data

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
        return

    manifest = _fetch_provider_manifest()
    if not manifest:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)
        return

    series_specs = _catalog_specs(manifest, "series")  # also matches anime via _catalog_specs

    if not series_specs:
        _notify_error("No compatible catalogs found")
        xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)
        return

    # Go directly to catalog content — skip the type/catalog-name intermediate menus
    spec = series_specs[0]
    catalog_type = "series"
    catalog_id = spec["id"]

    xbmcplugin.setContent(ADDON_HANDLE, "tvshows")

    response = _fetch_catalog(_catalog_url(catalog_type, catalog_id, "skip=0"))
    if not response:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)
        return

    videos = response.get("metas", ())
    series_stats = _watched.get_all_series_stats()
    items = []

    for video in videos:
        video_id = video["id"]
        video_name = video["name"]
        list_item = xbmcgui.ListItem(label=video_name, offscreen=True)
        tags = list_item.getVideoInfoTag()
        _set_ids(tags, video_id)
        _set_video_tags(tags, video, video_name)
        _set_art(list_item, video)
        watched_count, total = series_stats.get(video_id, (0, None))
        if total is None:
            s_meta = _fetch_provider_meta(catalog_type, video_id)
            if s_meta:
                all_ep_ids = [v["id"] for v in s_meta.get("videos", ()) if v.get("id")]
                if all_ep_ids:
                    total = len(all_ep_ids)
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
        series_ctx_label = "Mark as Unwatched" if (total and watched_count >= total) else "Mark as Watched"
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
    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)


def list_catalog_type(params):
    if not ensure_configured():
        return

    catalog_type = params["catalog_type"]
    if catalog_type not in SUPPORTED_CATALOG_TYPES:
        _notify_error("Unsupported catalog type")
        return

    manifest = _fetch_provider_manifest()
    if not manifest:
        return

    specs = _catalog_specs(manifest, catalog_type)
    if not specs:
        _notify_error("No catalogs available")
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
    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)


def list_catalog(params):
    if not ensure_configured():
        return

    catalog_type = params["catalog_type"]
    catalog_id = params["catalog_id"]
    skip = int(params.get("skip", "0"))

    response = _fetch_catalog(_catalog_url(catalog_type, catalog_id, f"skip={skip}"))
    if not response:
        return

    videos = response.get("metas", ())
    if not videos:
        _notify_error("No videos available")
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

    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)


def search_catalog(params):
    if not ensure_configured():
        return

    query = xbmcgui.Dialog().input("Search", type=xbmcgui.INPUT_ALPHANUM)
    if not query:
        return

    catalog_type = params["catalog_type"]
    catalog_id = params["catalog_id"]
    response = fetch_data(
        _catalog_url(catalog_type, catalog_id, f"search={parse.quote(query, safe='')}")
    )
    if not response:
        return

    videos = response.get("metas", ())
    if not videos:
        _notify_error("No results found")
        return

    _process_catalog_items(videos, catalog_type)
    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)
