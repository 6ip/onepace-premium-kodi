import hashlib
import json
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .utils import log

_ADDON = xbmcaddon.Addon()
_mem: dict = {}  # in-process memory tier (persists across navigations in same Kodi session)

# Room for the manifest, the catalogues and every series meta, with recent
# stream lists on top. One entry per episode opened would otherwise pile up for
# a whole Kodi session once the interpreter is reused.
_MAX_MEM = 64

# The longest ttl we hand out, so anything older has certainly expired.
_LONGEST_TTL = 86400
# A home-window property lasts exactly one Kodi session, which is how often
# this needs doing.
_HOME = 10000
_SWEPT = "pp.cache.swept"


def _remember(key: str, entry: dict) -> None:
    """Hold an entry in memory, keeping the tier bounded."""
    _mem[key] = entry
    if len(_mem) <= _MAX_MEM:
        return
    now = time.time()
    for stale in [k for k, v in _mem.items() if v["e"] <= now]:
        del _mem[stale]
    over = len(_mem) - _MAX_MEM
    if over <= 0:
        return
    # Stream lists last an hour and series metas six, so the cheapest to fetch
    # again go first. Nothing is lost either way: the disk copy stays.
    for spare, _ in sorted(_mem.items(), key=lambda kv: kv[1]["e"])[:over]:
        del _mem[spare]


def sweep() -> int:
    """Once a Kodi start, drop cache files nothing is going to read again.

    Entries expire when they are read, so a file for an episode never opened
    twice would sit in the profile for good. Age alone settles it here, which
    means no file has to be opened to know it is stale.
    """
    window = xbmcgui.Window(_HOME)
    if window.getProperty(_SWEPT):
        return 0
    window.setProperty(_SWEPT, "1")

    gone, cutoff = 0, time.time() - _LONGEST_TTL
    try:
        folder = _dir()
        for name in xbmcvfs.listdir(folder)[1]:
            if not (name.startswith("cache_") and name.endswith(".json")):
                continue
            try:
                if xbmcvfs.Stat(folder + name).st_mtime() >= cutoff:
                    continue
                if xbmcvfs.delete(folder + name):
                    gone += 1
            except Exception as exc:
                log(f"cache.sweep skipped {name}: {exc}", xbmc.LOGWARNING)
    except Exception as exc:
        log(f"cache.sweep error: {exc}", xbmc.LOGWARNING)
    if gone:
        log(f"[cache] swept {gone} file(s) nothing would have read again")
    return gone


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
                _remember(key, entry)
                return entry["d"]
            xbmcvfs.delete(p)
    except Exception:
        pass
    return None


def set(key: str, data, ttl: int) -> None:
    """Store data under key for ttl seconds."""
    entry = {"e": time.time() + ttl, "d": data}
    _remember(key, entry)
    try:
        xbmcvfs.mkdirs(_dir())
        with xbmcvfs.File(_path(key), "w") as f:
            f.write(json.dumps(entry, ensure_ascii=False))
    except Exception as e:
        log(f"cache.set error: {e}", xbmc.LOGWARNING)
