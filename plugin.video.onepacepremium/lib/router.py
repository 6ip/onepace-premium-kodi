import sys
from urllib import parse

import xbmc

from .catalog_routes import (list_browse, list_catalog, list_catalog_type,
                              list_root, search_catalog)
from .my_lists import list_in_progress, list_my_lists, list_next_episodes
from .episode_routes import (check_resume, clear_progress, get_streams,
                              list_episodes, list_seasons, mark_watched)
from .playback import play_video
from .route_common import open_addon_settings
from .utils import ADDON_ID, log


def open_settings(_params):
    xbmc.executebuiltin(
        f"RunScript(special://home/addons/{ADDON_ID}/lib/custom_settings_window.py)"
    )


_ACTIONS = {
    "open_settings": open_settings,
    "open_addon_settings": open_addon_settings,
    "list_catalog_type": list_catalog_type,
    "list_catalog": list_catalog,
    "search_catalog": search_catalog,
    "list_seasons": list_seasons,
    "list_episodes": list_episodes,
    "check_resume": check_resume,
    "get_streams": get_streams,
    "play_video": play_video,
    "mark_watched": mark_watched,
    "clear_progress": clear_progress,
    "list_browse": list_browse,
    "list_my_lists": list_my_lists,
    "list_in_progress": list_in_progress,
    "list_next_episodes": list_next_episodes,
}


def addon_router():
    param_string = sys.argv[2][1:]

    if param_string:
        params = dict(parse.parse_qsl(param_string))
        action = params.get("action")
        action_handler = _ACTIONS.get(action)
        if action_handler:
            action_handler(params)
            return

    log("Opening root menu", xbmc.LOGINFO)
    list_root()
