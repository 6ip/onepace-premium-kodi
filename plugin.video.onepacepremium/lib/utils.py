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
# What a viewer sees. The id above is what Kodi matches on, and never changes.
ADDON_NAME = "One Pace Addon"
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
    xbmc.log(f"[{ADDON_NAME}] {message}", level)


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
            ADDON_NAME,
            f"Request failed ({status_code}) on {target}"
            if status_code
            else f"Request failed on {target}",
            xbmcgui.NOTIFICATION_ERROR, 5000, True,
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


def set_setting(key, value):
    ADDON.setSetting(key, value)


def background():
    """The picture behind our own menus: the viewer's choice, or the one we ship.

    Checked every time, because a chosen file can be moved or deleted later and
    a missing fanart leaves the menu on a black screen.
    """
    chosen = get_setting("background_image")
    if chosen and xbmcvfs.exists(chosen):
        return chosen
    return f"special://home/addons/{ADDON_ID}/resources/fanart.png"


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

    Ours first: the nudge starts a library scan, and Kodi drops a refresh that
    arrives while one is running.
    """
    if not is_widget():
        xbmc.executebuiltin("Container.Refresh")
    ping_widgets()


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
    # The add-on serves its own catalog — always use the base URL
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
        ADDON_NAME,
        "Not configured. Open add-on settings.",
        xbmcgui.NOTIFICATION_INFO, 5000, True,
    )
    xbmc.executebuiltin(
        f"RunScript(special://home/addons/{ADDON_ID}/lib/custom_settings_window.py)"
    )
    return False


def _offer_elementum():
    """Torrents are played by Elementum, so its absence is the first thing to say."""
    choice = xbmcgui.Dialog().yesnocustom(
        ADDON_NAME,
        "No debrid service is set up, so episodes play over peer-to-peer, "
        "which needs the Elementum add-on.\n\n"
        "Look for Elementum now, or set up a debrid service instead?",
        "Configure", "Not now", "Find Elementum",
    )
    if choice == 1:
        xbmc.executebuiltin(
            f"RunScript(special://home/addons/{ADDON_ID}/lib/setup_elementum.py)"
        )
    elif choice == 2:
        xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
    return False


def _ask_p2p():
    """The one-time question. True only when they choose to go ahead."""
    choice = xbmcgui.Dialog().yesnocustom(
        ADDON_NAME,
        "This add-on is not configured, so this episode plays over "
        "peer-to-peer.\n\n"
        "It takes longer to start than a debrid service, and your own "
        "connection joins in sharing the file.",
        "Configure", "Not now", "Play anyway",
    )
    if choice == 1:
        set_setting("p2p_ack", "true")
        return True
    if choice == 2:
        xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
    # "Not now", Back and Esc all leave it for later. Nothing is stored, so
    # the next episode asks again.
    return False


def ensure_p2p_ready(silent=False):
    """Whether a viewer with no debrid key may go ahead over peer-to-peer.

    The server answers an unconfigured request with torrents, so this asks for
    consent rather than reporting a missing setup. silent is for the paths that
    must not raise a dialog: a season queue, the preference survey, and the
    hand-off into the next episode.
    """
    if silent:
        return get_setting("p2p_ack") == "true"
    if not is_elementum_installed_and_enabled():
        return _offer_elementum()
    if get_setting("p2p_ack") == "true":
        return True
    return _ask_p2p()
