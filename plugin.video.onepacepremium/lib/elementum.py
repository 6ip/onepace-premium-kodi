"""Resolve Elementum streams directly from its daemon.

Playing a plugin:// URL makes Kodi resolve a second time, and Elementum's own
ListItem replaces ours. Asking the daemon for the stream URL ourselves keeps a
single setResolvedUrl, so our metadata survives — same as the debrid path.
Elementum still does all torrent work and shows its own progress dialog.
"""
from urllib import parse

import requests
import xbmcaddon

from .utils import ADDON, log

ELEMENTUM_ID = "plugin.video.elementum"
PLUGIN_PREFIX = f"plugin://{ELEMENTUM_ID}/play"

_CONNECT_TIMEOUT = 10
_BUFFER_TIMEOUT = 900  # the daemon holds the request open while buffering


def direct_enabled():
    # Defaults off: getSetting returns "" before the settings dialog is opened.
    return ADDON.getSetting("elementum_direct") == "true"


def _connection():
    """Elementum's own daemon settings, or None if it isn't installed."""
    try:
        addon = xbmcaddon.Addon(ELEMENTUM_ID)
    except Exception:
        return None
    host = addon.getSetting("remote_host") or "127.0.0.1"
    port = addon.getSetting("remote_port") or "65220"
    login = addon.getSetting("remote_login") or ""
    password = addon.getSetting("remote_password") or ""
    return (
        f"http://{host}:{port}",
        (login, password) if login else None,
    )


def resolve_stream(uri, file_idx=None):
    """Ask the daemon for a direct stream URL. None means fall back."""
    conn = _connection()
    if not conn:
        log("[direct] Elementum not installed, falling back")
        return None
    base, auth = conn

    url = f"{base}/play?uri={parse.quote(uri, safe='')}"
    if file_idx is not None:
        url += f"&index={file_idx}&oindex={file_idx}"
    # Resume is ours (bookmarks + Kodi's dialog); stop Elementum asking too.
    url += "&doresume=false"

    session = requests.Session()
    session.trust_env = False  # daemon is local; ignore system proxy settings
    try:
        # Follow the redirect and open /files/ exactly as Elementum's own
        # urlopen does, so the daemon sets up its reader the same way.
        # stream=True keeps us from pulling the video body down.
        response = session.get(
            url,
            allow_redirects=True,
            stream=True,
            auth=auth,
            timeout=(_CONNECT_TIMEOUT, _BUFFER_TIMEOUT),
        )
    except Exception as exc:
        log(f"[direct] daemon request failed ({exc}), falling back")
        session.close()
        return None

    try:
        final_url = response.url
        status = response.status_code
    finally:
        response.close()
        session.close()

    # A redirect chain ending on /files/ is success. Landing back on /play with
    # no redirect means the daemon stopped early — cancelled, or no file chosen.
    if status == 200 and "/files/" in final_url:
        log(f"[direct] resolved to {final_url[:100]}")
        return final_url

    log(f"[direct] no stream (HTTP {status}, url={final_url[:80]}), falling back")
    return None


def resolve_plugin_url(plugin_url):
    """Turn a plugin://…/play?uri=… URL into a direct stream URL, or None."""
    try:
        query = parse.parse_qs(parse.urlsplit(plugin_url).query)
    except Exception as exc:
        log(f"[direct] could not parse {plugin_url[:60]!r}: {exc}")
        return None

    uri = (query.get("uri") or [""])[0]
    if not uri:
        log("[direct] no uri in plugin URL, falling back")
        return None

    index = (query.get("index") or [None])[0]
    return resolve_stream(uri, index)
