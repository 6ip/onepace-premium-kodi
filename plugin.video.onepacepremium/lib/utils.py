import json
import os
import sys
from typing import List
from urllib import parse

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


class _Handle:
    """A new handle arrives every call, so it is resolved on every use."""

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

ELEMENTUM_ID = "plugin.video.elementum"
# Our feed carries the ElementumOrg repository, so send them there.
ONEPACE_REPO_ID = "repository.onepacepremium"


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
    """The picture behind our own menus: the viewer's choice, or the one we ship."""
    chosen = get_setting("background_image")
    if chosen and xbmcvfs.exists(chosen):
        return chosen
    return f"special://home/addons/{ADDON_ID}/resources/fanart.png"


def is_widget():
    """True when a skin is drawing our list on its own screen."""
    return ADDON_ID not in xbmc.getInfoLabel("Container.PluginName")


def ping_widgets():
    """Nudge every shelf on the home screen."""
    xbmc.executebuiltin("UpdateLibrary(video,special://skin/foo)")


def refresh_container():
    """Redraw whatever is showing what we just changed."""
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
        xbmcaddon.Addon(ELEMENTUM_ID)
        return True
    except Exception:
        return False


def elementum_state():
    """One of "ready", "disabled" or "missing"."""
    if is_elementum_installed_and_enabled():
        return "ready"
    try:
        reply = json.loads(xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "Addons.GetAddonDetails",
            "params": {"addonid": ELEMENTUM_ID, "properties": ["enabled"]},
        })))
    except ValueError:
        return "missing"
    return "disabled" if "result" in reply else "missing"


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


def _offer_elementum(state):
    """Torrents are played by Elementum, so its absence is the first thing to say."""
    preamble = ("No debrid service is set up, so episodes play over "
                "peer-to-peer, which needs the Elementum add-on.\n\n")
    if state == "disabled":
        message, label = preamble + "Elementum is installed but switched off.", "Enable Elementum"
    else:
        # Said as two steps because it is two steps.
        message, label = preamble + (
            "Our repository will open. Install ElementumOrg Repository there, "
            "then Elementum from inside it."), "Open Repository"

    choice = xbmcgui.Dialog().yesnocustom(
        ADDON_NAME, message, "Configure", "Not now", label)
    if choice == 1:
        xbmc.executebuiltin(
            f"EnableAddon({ELEMENTUM_ID})" if state == "disabled"
            else f"ActivateWindow(addonbrowser,addons://{ONEPACE_REPO_ID}/,return)"
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
    # "Not now", Back and Esc all leave it for later.
    return False


def ensure_p2p_ready(silent=False):
    """Whether a viewer with no debrid key may go ahead over peer-to-peer."""
    if silent:
        return get_setting("p2p_ack") == "true"
    state = elementum_state()
    if state != "ready":
        return _offer_elementum(state)
    if get_setting("p2p_ack") == "true":
        return True
    return _ask_p2p()
