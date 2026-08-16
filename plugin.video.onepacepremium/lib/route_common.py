from typing import Optional

import xbmc
import xbmcgui
import xbmcplugin

from .utils import ADDON_HANDLE, ADDON_ID, get_setting


def _add_directory_items(items: list, total_items: Optional[int] = None):
    if not items:
        return
    xbmcplugin.addDirectoryItems(
        ADDON_HANDLE,
        items,
        len(items) if total_items is None else total_items,
    )


def _notify_error(message: str):
    xbmcgui.Dialog().notification("One Pace Premium", message, xbmcgui.NOTIFICATION_ERROR)


def end_directory(cache: bool = False, succeeded: bool = True):
    """Finish a listing. cache=True lets Kodi reuse it instead of re-running us.

    Only lists that carry no watched or resume state opt in, plus the episode
    list, which is refreshed after every change to it.
    """
    if cache and get_setting("menu_cache") == "false":
        cache = False
    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=cache, succeeded=succeeded)


def open_addon_settings(_params):
    xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
    end_directory(succeeded=False)
