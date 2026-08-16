import json

import xbmc
import xbmcaddon
import xbmcvfs

_ADDON = xbmcaddon.Addon()

# Read once per plugin call — episode lists ask for this in a loop.
_CACHED = None


def _path():
    profile = xbmcvfs.translatePath(_ADDON.getAddonInfo("profile"))
    if not profile.endswith(("/", "\\")):
        profile += "/"
    return profile + "bookmarks.json"


def _load(fresh=False):
    global _CACHED
    if _CACHED is not None and not fresh:
        return _CACHED
    data = {}
    try:
        p = _path()
        if xbmcvfs.exists(p):
            with xbmcvfs.File(p, "r") as f:
                data = json.loads(f.read() or "{}")
    except Exception:
        pass
    _CACHED = data
    return _CACHED


def _save(data):
    global _CACHED
    try:
        xbmcvfs.mkdirs(xbmcvfs.translatePath(_ADDON.getAddonInfo("profile")))
        with xbmcvfs.File(_path(), "w") as f:
            f.write(json.dumps(data))
    except Exception as e:
        xbmc.log(f"[One Pace Premium] bookmarks save error: {e}", xbmc.LOGERROR)
    _CACHED = data


def get_all():
    """Return all bookmarks as {episode_id: {"pos": float, "total": float}}."""
    return {k: v for k, v in _load().items() if isinstance(v, dict)}


def get(episode_id):
    """Return {"pos": float, "total": float} or None if no bookmark exists."""
    entry = _load().get(episode_id)
    if not entry or not isinstance(entry, dict):
        return None
    return entry


def set_bookmark(episode_id, position, total, series_id=""):
    """Save resume position for an episode."""
    data = _load(fresh=True)
    entry = {"pos": round(float(position), 1), "total": round(float(total), 1)}
    if series_id:
        entry["series_id"] = series_id
    data[episode_id] = entry
    _save(data)


def clear(episode_id):
    """Remove resume position for an episode (no-op if none exists)."""
    data = _load(fresh=True)
    if episode_id in data:
        del data[episode_id]
        _save(data)
