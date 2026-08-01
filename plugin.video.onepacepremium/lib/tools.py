import base64
import glob
import json
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


def _parse_config(config):
    """Parse config string. Returns list of human-readable service labels (empty = unrecognized)."""
    _LEGACY_PREFIXES = [
        ("rdkey=",      "Real-Debrid"),
        ("torbox=",     "TorBox"),
        ("alldebrid=",  "AllDebrid"),
        ("premiumize=", "Premiumize"),
        ("dlkey=",      "DebridLink"),
    ]
    _LEGACY_KEYS = {
        "rdkey":      "Real-Debrid",
        "torbox":     "TorBox",
        "alldebrid":  "AllDebrid",
        "premiumize": "Premiumize",
        "dlkey":      "DebridLink",
    }
    _SERVICE_LABELS = {
        "realdebrid":  "Real-Debrid",
        "torbox":      "TorBox",
        "alldebrid":   "AllDebrid",
        "premiumize":  "Premiumize",
        "debridlink":  "DebridLink",
        "p2p":         "Torrent",
    }
    # P2P carries no apiKey; every debrid service must have one.
    _KEYLESS = {"p2p"}

    if not config:
        return []

    # 1. Legacy plaintext: rdkey=..., torbox=..., etc.
    for prefix, label in _LEGACY_PREFIXES:
        if config.startswith(prefix):
            return [label] if config[len(prefix):] else []

    try:
        decoded = json.loads(base64.b64decode(config).decode("utf-8"))

        # 2. Multi-service: {"debridServices": [{"service": ..., "apiKey": ...}]}
        if isinstance(decoded.get("debridServices"), list):
            seen = set()
            labels = []
            for entry in decoded["debridServices"]:
                if not isinstance(entry, dict):
                    continue  # skip a malformed entry, keep the valid ones
                svc = entry.get("service", "")
                key = entry.get("apiKey", "")
                label = _SERVICE_LABELS.get(svc)
                if not label or label in seen:
                    continue
                if svc not in _KEYLESS and not key:
                    continue
                seen.add(label)
                labels.append(label)
            return labels

        # 3. Legacy base64 JSON: {"rdkey": "..."}
        labels = []
        seen = set()
        for field, label in _LEGACY_KEYS.items():
            if decoded.get(field) and label not in seen:
                seen.add(label)
                labels.append(label)
        return labels

    except Exception:
        pass

    return []


def show_status():
    config = xbmcaddon.Addon(ADDON_ID).getSetting("secret_string")
    services = _parse_config(config)

    if not config:
        xbmcgui.Dialog().ok(
            "Account Status",
            "Not configured.\n\nUse [B]Configure / Reconfigure[/B] to set up your account."
        )
        return

    if not services:
        xbmcgui.Dialog().ok(
            "Account Status",
            "Configuration not recognized.\n\nUse [B]Configure / Reconfigure[/B] to set up again."
        )
        return

    connected = " + ".join(f"[B]{s}[/B]" for s in services)
    xbmcgui.Dialog().ok(
        "Account Status",
        f"Connected: {connected}"
    )


def configure_account():
    import xbmc
    addon = xbmcaddon.Addon(ADDON_ID)
    config = addon.getSetting("secret_string")
    services = _parse_config(config)

    if not config:
        message = "No account configured yet.\n\nSet up your debrid service to start watching."
        yes_label = "Configure"
    elif not services:
        message = "Configuration not recognized.\n\nReconfigure your account?"
        yes_label = "Reconfigure"
    else:
        connected = " + ".join(f"[B]{s}[/B]" for s in services)
        message = f"Connected: {connected}\n\nDo you want to reconfigure?"
        yes_label = "Reconfigure"

    if xbmcgui.Dialog().yesno(
        "Configure / Reconfigure",
        message,
        nolabel="Cancel",
        yeslabel=yes_label,
    ):
        # Close settings first so auth runs full-screen without settings in background
        xbmc.executebuiltin("Dialog.Close(all,true)")
        xbmc.executebuiltin(
            f"RunScript(special://home/addons/{ADDON_ID}/lib/custom_settings_window.py)"
        )


if __name__ == "__main__":
    action = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    if action == "clear_cache":
        clear_cache()
    elif action == "clear_bookmarks":
        clear_bookmarks()
    elif action == "clear_watched":
        clear_watched()
    elif action == "show_status":
        show_status()
    elif action == "configure_account":
        configure_account()
