import sys
from importlib import import_module
from urllib import parse

import xbmc

from .utils import log, reset_for_invocation


# Each action names the module and function it lives in. Nothing is imported
# until one is asked for, so a listing never pays for playback or backup.
_ACTIONS = {
    "show_changelog":      "changelog:show_changelog",
    "show_donate":         "donate:show_donate",
    "export_settings":     "backup:export_settings",
    "import_settings":     "backup:import_settings",
    "open_addon_settings": "route_common:open_addon_settings",
    "play_trailer":        "route_common:play_trailer",
    "list_catalog_type":   "catalog_routes:list_catalog_type",
    "list_catalog":        "catalog_routes:list_catalog",
    "search_catalog":      "catalog_routes:search_catalog",
    "list_browse":         "catalog_routes:list_browse",
    "list_seasons":        "episode_routes:list_seasons",
    "list_episodes":       "episode_routes:list_episodes",
    "check_resume":        "episode_routes:check_resume",
    "get_streams":         "episode_routes:get_streams",
    "mark_watched":        "episode_routes:mark_watched",
    "clear_progress":      "episode_routes:clear_progress",
    "play_video":          "playback:play_video",
    "list_my_lists":       "my_lists:list_my_lists",
    "list_in_progress":    "my_lists:list_in_progress",
    "list_next_episodes":  "my_lists:list_next_episodes",
    "list_downloads":      "downloads:list_downloads",
    "download_episode":    "episode_routes:download_episode",
    "delete_download":     "downloads:delete",
    "move_downloads":      "downloads:move_downloads",
}


def _resolve(target):
    """Import the one module this action lives in."""
    module, _, name = target.partition(":")
    return getattr(import_module(f".{module}", __package__), name)


def addon_router():
    reset_for_invocation()
    param_string = sys.argv[2][1:]

    if param_string:
        params = dict(parse.parse_qsl(param_string))
        action = params.get("action")
        target = _ACTIONS.get(action)
        if target:
            _resolve(target)(params)
            return

    log("Opening root menu", xbmc.LOGINFO)
    _resolve("catalog_routes:list_root")()
