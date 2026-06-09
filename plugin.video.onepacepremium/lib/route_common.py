from typing import Optional

import xbmcgui
import xbmcplugin

from .utils import ADDON_HANDLE


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
