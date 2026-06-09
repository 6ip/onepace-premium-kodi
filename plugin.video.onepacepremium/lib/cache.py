import hashlib
import json
import time

import xbmc
import xbmcaddon
import xbmcvfs

_ADDON = xbmcaddon.Addon()
_mem: dict = {}  # in-process memory tier (persists across navigations in same Kodi session)


def _dir() -> str:
    p = xbmcvfs.translatePath(_ADDON.getAddonInfo("profile"))
    return p if p.endswith(("/", "\\")) else p + "/"


def _path(key: str) -> str:
    h = hashlib.md5(key.encode("utf-8")).hexdigest()
    return _dir() + f"cache_{h}.json"


def get(key: str):
    """Return cached value or None if missing / expired."""
    now = time.time()

    entry = _mem.get(key)
    if entry:
        if now < entry["e"]:
            return entry["d"]
        del _mem[key]

    p = _path(key)
    try:
        if xbmcvfs.exists(p):
            with xbmcvfs.File(p, "r") as f:
                entry = json.loads(f.read())
            if now < entry["e"]:
                _mem[key] = entry
                return entry["d"]
            xbmcvfs.delete(p)
    except Exception:
        pass
    return None


def set(key: str, data, ttl: int) -> None:
    """Store data under key for ttl seconds."""
    entry = {"e": time.time() + ttl, "d": data}
    _mem[key] = entry
    try:
        xbmcvfs.mkdirs(_dir())
        with xbmcvfs.File(_path(key), "w") as f:
            f.write(json.dumps(entry, ensure_ascii=False))
    except Exception as e:
        xbmc.log(f"[One Pace Premium] cache.set error: {e}", xbmc.LOGWARNING)
