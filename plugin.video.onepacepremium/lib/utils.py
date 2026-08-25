import os
import sys
from typing import List
from urllib import parse

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


class _Handle:
    """Kodi hands us a new plugin handle every call, but with
    reuselanguageinvoker the module is only imported once. Resolving on each
    use keeps sys.argv[1] from going stale.
    """

    @staticmethod
    def _resolve():
        try:
            return int(sys.argv[1])
        except (IndexError, ValueError):
            return -1

    def __index__(self):
        return self._resolve()

    def __int__(self):
        return self._resolve()

    def __repr__(self):
        return str(self._resolve())


ADDON_HANDLE = _Handle()
ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_PATH = f"plugin://{ADDON_ID}/"
ADDON_DIR = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
ALERT_ICON = os.path.join(ADDON_DIR, "resources", "skins", "Default", "media", "alert.png")

REQUEST_TIMEOUT = 20
_SESSION = None


def session():
    """requests costs ~400ms to import, so load it only when we really fetch."""
    global _SESSION
    if _SESSION is None:
        import requests
        _SESSION = requests.Session()
    return _SESSION


_DEBUG = None


def log(message: str, level=xbmc.LOGINFO):
    """Info lines are debug-only; warnings and errors always go through."""
    global _DEBUG
    if level == xbmc.LOGINFO:
        if _DEBUG is None:
            _DEBUG = ADDON.getSetting("debug_logging") == "true"
        if not _DEBUG:
            return
    xbmc.log(f"[One Pace Premium] {message}", level)


def build_url(action: str, **params):
    query = parse.urlencode(params)
    return (
        f"{ADDON_PATH}?action={action}&{query}"
        if query
        else f"{ADDON_PATH}?action={action}"
    )


def fetch_data(url: str):
    import requests
    try:
        response = session().get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        resp = exc.response
        status_code = resp.status_code if resp is not None else None
        target = parse.urlparse(url).netloc or url
        log(f"Request failed for {url}: {exc}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            "One Pace Premium",
            f"Request failed ({status_code}) on {target}"
            if status_code
            else f"Request failed on {target}",
            xbmcgui.NOTIFICATION_ERROR,
        )
        return None


def convert_info_hash_to_magnet(
    info_hash: str,
    trackers: List[str],
    display_name: str = "",
):
    magnet_parts = [f"magnet:?xt=urn:btih:{info_hash.strip()}"]
    if display_name:
        magnet_parts.append(f"dn={parse.quote(display_name, safe='')}")

    seen = set()
    for source in trackers:
        if source.startswith("tracker:"):
            stype, svalue = "tr", source[8:]
        elif source.startswith("dht:"):
            stype, svalue = "dht", source[4:]
        else:
            stype, svalue = "tr", source

        svalue = svalue.strip()
        if not svalue:
            continue

        key = (stype, svalue)
        if key in seen:
            continue
        seen.add(key)

        magnet_parts.append(f"{stype}={parse.quote(svalue, safe='')}")

    return "&".join(magnet_parts)


def get_setting(key):
    return ADDON.getSetting(key)


def is_widget():
    """True when a skin is drawing our list on its own screen."""
    return ADDON_ID not in xbmc.getInfoLabel("Container.PluginName")


def ping_widgets():
    """Nudge every shelf on the home screen.

    The scan finds nothing under that path and stops, but widgets reload on
    the way past. It is the only thing that reaches a container we do not own.
    """
    xbmc.executebuiltin("UpdateLibrary(video,special://skin/foo)")


def refresh_container():
    """Redraw whatever is showing what we just changed.

    Shelves need the nudge either way, since a change made in our own list
    still leaves them stale. Container.Refresh only reaches a container we
    own, so it is worth adding when we are standing in one.
    """
    ping_widgets()
    if not is_widget():
        xbmc.executebuiltin("Container.Refresh")


# Climbs past 1 only when Kodi reuses the interpreter.
_INVOCATIONS = [0]


def reset_for_invocation():
    """Drop anything that must not outlive one plugin call."""
    global ADDON, _DEBUG
    ADDON = xbmcaddon.Addon()
    _DEBUG = None
    _INVOCATIONS[0] += 1
    log(f"[boot] invocation {_INVOCATIONS[0]} in pid {os.getpid()}")
    from . import bookmarks as _b, watched as _w
    _b.invalidate()
    _w.invalidate()


def get_base_url():
    return ADDON.getSetting("base_url").rstrip("/")


def get_secret_string():
    return ADDON.getSetting("secret_string")


def get_stremio_api_prefix():
    prefix = ADDON.getSetting("stremio_api_prefix").strip().strip("/")
    return f"{prefix}/" if prefix else ""


def get_config_prefix():
    api_prefix = get_stremio_api_prefix()
    secret = get_secret_string()
    return f"{api_prefix}{secret}/" if secret else api_prefix


def get_catalog_provider_url():
    # One Pace Premium serves its own catalog — always use the base URL
    return get_base_url()


def is_elementum_installed_and_enabled():
    try:
        xbmcaddon.Addon("plugin.video.elementum")
        return True
    except Exception:
        return False


def ensure_configured():
    if get_base_url():
        return True

    xbmcgui.Dialog().notification(
        "One Pace Premium",
        "Not configured. Open add-on settings.",
        xbmcgui.NOTIFICATION_INFO,
    )
    xbmc.executebuiltin(
        f"RunScript(special://home/addons/{ADDON_ID}/lib/custom_settings_window.py)"
    )
    return False
