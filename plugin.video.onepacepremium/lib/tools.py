import sys

import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = "plugin.video.onepacepremium"


def _profile():
    p = xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo("profile"))
    return p if p.endswith(("/", "\\")) else p + "/"


def clear_cache():
    dialog = xbmcgui.Dialog()
    if not dialog.yesno(
        "Clear API Cache",
        "This will delete all cached metadata. The addon will fetch fresh data on the next browse.\n\nAre you sure?",
        nolabel="No",
        yeslabel="Yes",
    ):
        return

    profile = _profile()
    _, files = xbmcvfs.listdir(profile)
    count = sum(
        1 for f in files
        if f.startswith("cache_") and f.endswith(".json") and xbmcvfs.delete(profile + f)
    )
    dialog.notification(
        "One Pace Premium",
        f"API cache cleared ({count} file{'s' if count != 1 else ''} removed).",
        xbmcgui.NOTIFICATION_INFO,
    )


def clear_bookmarks():
    dialog = xbmcgui.Dialog()
    if not dialog.yesno(
        "Clear Bookmarks",
        "This will remove all saved resume positions. Your watched history will not be affected.\n\nAre you sure?",
        nolabel="No",
        yeslabel="Yes",
    ):
        return

    path = _profile() + "bookmarks.json"
    if xbmcvfs.exists(path):
        xbmcvfs.delete(path)
    dialog.notification(
        "One Pace Premium",
        "All resume bookmarks cleared.",
        xbmcgui.NOTIFICATION_INFO,
    )


def clear_watched():
    dialog = xbmcgui.Dialog()
    if not dialog.yesno(
        "Clear Watched History",
        "This will permanently erase all watched episode data. This cannot be undone.\n\nAre you sure?",
        nolabel="No",
        yeslabel="Yes, clear all",
    ):
        return

    path = _profile() + "watched.json"
    if xbmcvfs.exists(path):
        xbmcvfs.delete(path)
    dialog.notification(
        "One Pace Premium",
        "Watched history cleared.",
        xbmcgui.NOTIFICATION_INFO,
    )


if __name__ == "__main__":
    action = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    if action == "clear_cache":
        clear_cache()
    elif action == "clear_bookmarks":
        clear_bookmarks()
    elif action == "clear_watched":
        clear_watched()
