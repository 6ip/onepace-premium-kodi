import json

import xbmc
import xbmcaddon
import xbmcvfs

_ADDON = xbmcaddon.Addon()
_TOTALS_KEY = "__totals__"


def _path():
    profile = xbmcvfs.translatePath(_ADDON.getAddonInfo("profile"))
    if not profile.endswith(("/", "\\")):
        profile += "/"
    return profile + "watched.json"


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
        xbmc.log(f"[One Pace Premium] watched save error: {e}", xbmc.LOGERROR)


def get_watched(series_id):
    """Return set of watched episode IDs for a series."""
    return set(_load().get(series_id, []))


def set_episodes_watched(series_id, episode_ids, watched=True):
    """Mark or unmark a list of episode IDs for a series."""
    data = _load()
    current = set(data.get(series_id, []))
    if watched:
        current.update(episode_ids)
    else:
        current.difference_update(episode_ids)
    if current:
        data[series_id] = sorted(current)
    elif series_id in data:
        del data[series_id]
    _save(data)


def toggle_episode(series_id, episode_id):
    """Toggle watched state for a single episode."""
    current = get_watched(series_id)
    set_episodes_watched(series_id, [episode_id], episode_id not in current)


def toggle_batch(series_id, episode_ids):
    """If all episodes are watched → unmark all. Otherwise mark all."""
    current = get_watched(series_id)
    all_watched = all(eid in current for eid in episode_ids)
    set_episodes_watched(series_id, episode_ids, not all_watched)


def cache_total(series_id, total):
    """Cache the total episode count for a series so list views can show X/total."""
    data = _load()
    totals = data.get(_TOTALS_KEY, {})
    if totals.get(series_id) == total:
        return
    totals[series_id] = total
    data[_TOTALS_KEY] = totals
    _save(data)


def get_all_series_ids():
    """Return all series IDs that have watched episodes."""
    data = _load()
    return [sid for sid in data if sid != _TOTALS_KEY and data[sid]]


def get_all_series_stats():
    """Return {series_id: (watched_count, total_or_None)} for all tracked series."""
    data = _load()
    totals = data.get(_TOTALS_KEY, {})
    result = {}
    for key, val in data.items():
        if key == _TOTALS_KEY:
            continue
        if isinstance(val, list):
            result[key] = (len(val), totals.get(key))
    return result
