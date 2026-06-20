import glob
import os
import sqlite3
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


def _clear_kodi_bookmarks():
    """Clear Kodi's own bookmark (resume) entries for our plugin from MyVideos.db."""
    try:
        db_dir = xbmcvfs.translatePath("special://profile/Database/")
        db_files = sorted(glob.glob(os.path.join(db_dir, "MyVideos*.db")), reverse=True)
        if not db_files:
            return 0
        con = sqlite3.connect(db_files[0])
        cur = con.cursor()
        cur.execute(
            "SELECT idFile FROM files WHERE strFilename LIKE ?",
            ("%plugin.video.onepacepremium%",)
        )
        file_ids = [str(r[0]) for r in cur.fetchall()]
        count = 0
        if file_ids:
            ph = ",".join(file_ids)
            cur.execute(f"DELETE FROM bookmark WHERE idFile IN ({ph})")
            count = cur.rowcount
            con.commit()
        cur.close()
        con.close()
        return count
    except Exception:
        return 0


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
    _clear_kodi_bookmarks()
    dialog.notification(
        "One Pace Premium",
        "All resume bookmarks cleared.",
        xbmcgui.NOTIFICATION_INFO,
    )


def _reset_kodi_playcounts():
    """Reset Kodi's playCount to 0 for all our plugin's file entries in MyVideos.db."""
    try:
        db_dir = xbmcvfs.translatePath("special://profile/Database/")
        db_files = sorted(glob.glob(os.path.join(db_dir, "MyVideos*.db")), reverse=True)
        if not db_files:
            return
        con = sqlite3.connect(db_files[0])
        cur = con.cursor()
        cur.execute(
            "UPDATE files SET playCount=0 WHERE strFilename LIKE ?",
            ("%plugin.video.onepacepremium%",)
        )
        con.commit()
        cur.close()
        con.close()
    except Exception:
        pass


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
    _reset_kodi_playcounts()
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
