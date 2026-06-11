from typing import Optional

import xbmc
import xbmcgui
import xbmcplugin

from .utils import ADDON_HANDLE, ADDON_ID


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


def open_addon_settings(_params):
    xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False, succeeded=False)
