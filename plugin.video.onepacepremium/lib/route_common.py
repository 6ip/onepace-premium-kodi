from typing import Optional

import xbmc
import xbmcgui
import xbmcplugin

from .utils import ADDON_HANDLE, ADDON_ID, get_setting


def _handle():
    """The handle as a real int, which is the only thing Kodi 22 accepts.

    It stays lazy in utils so a reused interpreter cannot serve a stale one,
    and is resolved here, at the one place it crosses into Kodi.
    """
    return int(ADDON_HANDLE)


def set_content(content: str):
    xbmcplugin.setContent(_handle(), content)


def set_category(label: str):
    xbmcplugin.setPluginCategory(_handle(), label)


def set_resolved(item, succeeded: bool = True):
    xbmcplugin.setResolvedUrl(_handle(), succeeded, item)


def _add_directory_items(items: list, total_items: Optional[int] = None):
    if not items:
        return
    xbmcplugin.addDirectoryItems(
        _handle(),
        items,
        len(items) if total_items is None else total_items,
    )


def _notify_error(message: str):
    # The trailing False is the sound flag, which defaults to on.
    xbmcgui.Dialog().notification("One Pace Premium", message,
                                  xbmcgui.NOTIFICATION_ERROR, 5000, True)


def _notify_info(message: str):
    xbmcgui.Dialog().notification("One Pace Premium", message,
                                  xbmcgui.NOTIFICATION_INFO, 4000, False)


def end_directory(cache: bool = False, succeeded: bool = True):
    """Finish a listing. cache=True lets Kodi reuse it instead of re-running us.

    Only lists that carry no watched or resume state opt in, plus the episode
    list, which is refreshed after every change to it.
    """
    if cache and get_setting("menu_cache") == "false":
        cache = False
    xbmcplugin.endOfDirectory(_handle(), cacheToDisc=cache, succeeded=succeeded)


def play_trailer(params):
    """Trailer button target — needs the YouTube add-on to actually play."""
    ytid = params.get("ytid", "")
    if ytid and xbmc.getCondVisibility("System.HasAddon(plugin.video.youtube)"):
        item = xbmcgui.ListItem(
            path=f"plugin://plugin.video.youtube/play/?video_id={ytid}"
        )
        set_resolved(item)
        return
    _notify_error("YouTube add-on is required to play trailers")
    set_resolved(xbmcgui.ListItem(), False)


def open_addon_settings(_params):
    xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
    end_directory(succeeded=False)
