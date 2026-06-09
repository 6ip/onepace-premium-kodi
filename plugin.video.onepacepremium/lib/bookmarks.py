import json

import xbmc
import xbmcaddon
import xbmcvfs

_ADDON = xbmcaddon.Addon()


def _path():
    profile = xbmcvfs.translatePath(_ADDON.getAddonInfo("profile"))
    if not profile.endswith(("/", "\\")):
        profile += "/"
    return profile + "bookmarks.json"


def _load():
    try:
        p = _path()
        if xbmcvfs.exists(p):
            with xbmcvfs.File(p, "r") as f:
                return json.loads(f.read() or "{}")
    except Exception:
        pass
    return {}


def _save(data):
    try:
        xbmcvfs.mkdirs(xbmcvfs.translatePath(_ADDON.getAddonInfo("profile")))
        with xbmcvfs.File(_path(), "w") as f:
            f.write(json.dumps(data))
    except Exception as e:
        xbmc.log(f"[One Pace Premium] bookmarks save error: {e}", xbmc.LOGERROR)


def get(episode_id):
    """Return {"pos": float, "total": float} or None if no bookmark exists."""
    entry = _load().get(episode_id)
    if not entry or not isinstance(entry, dict):
        return None
    return entry


def set_bookmark(episode_id, position, total):
    """Save resume position for an episode."""
    data = _load()
    data[episode_id] = {"pos": round(float(position), 1), "total": round(float(total), 1)}
    _save(data)


def clear(episode_id):
    """Remove resume position for an episode (no-op if none exists)."""
    data = _load()
    if episode_id in data:
        del data[episode_id]
        _save(data)
