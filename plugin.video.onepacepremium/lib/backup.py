"""Export and restore add-on data."""
import json
import os
import time
import xml.etree.ElementTree as ET

import xbmcaddon
import xbmcgui
import xbmcvfs

from .utils import ADDON_ID, log

# Your configuration key unlocks the debrid account behind it.
SENSITIVE = {"secret_string", "base_url", "stremio_api_prefix"}

# Rebuilt on the target machine, so carrying them over means nothing.
SKIP = {"last_seen_version", "preferred_service_display",
        "highlight_color_display", "sub_langs_display"}

# key, label, on by default
PARTS = [
    ("settings",  "Settings",           True),
    ("watched",   "Watched history",    True),
    ("bookmarks", "Resume points",      True),
    ("config",    "Configuration key",  False),
]
_STORES = {"watched": "watched.json", "bookmarks": "bookmarks.json"}
_FORMAT = 2


def _addon():
    return xbmcaddon.Addon(ADDON_ID)


def _profile(addon):
    p = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    return p if p.endswith(("/", "\\")) else p + "/"


def _schema(addon):
    path = os.path.join(
        xbmcvfs.translatePath(addon.getAddonInfo("path")), "resources", "settings.xml")
    return [s.get("id") for s in ET.parse(path).getroot().iter("setting")
            if s.get("id") and s.get("type") != "action" and s.get("id") not in SKIP]


def _read_store(addon, name):
    try:
        path = _profile(addon) + _STORES[name]
        if not xbmcvfs.exists(path):
            return {}
        with xbmcvfs.File(path) as handle:
            return json.loads(handle.read() or "{}")
    except Exception as exc:
        log(f"[backup] could not read {name}: {exc}")
        return {}


def _write_store(addon, name, data):
    try:
        with xbmcvfs.File(_profile(addon) + _STORES[name], "w") as handle:
            handle.write(json.dumps(data, indent=2))
        return True
    except Exception as exc:
        log(f"[backup] could not write {name}: {exc}")
        return False


def _count(part, section):
    if part == "settings":
        return len(section)
    return sum(len(v) if isinstance(v, list) else 1
               for k, v in section.items() if not k.startswith("__"))


def _pick(heading, options, preselect):
    chosen = xbmcgui.Dialog().multiselect(heading, options, preselect=preselect)
    return chosen or []


def export_settings(_params=None):
    addon = _addon()
    dialog = xbmcgui.Dialog()

    labels = [f"{lab}  [COLOR FF888899](keep private)[/COLOR]" if key == "config" else lab
              for key, lab, _ in PARTS]
    picked = _pick("What should the backup include?", labels,
                   [i for i, (_, _, on) in enumerate(PARTS) if on])
    if not picked:
        return
    want = {PARTS[i][0] for i in picked}

    payload = {"addon": ADDON_ID, "version": addon.getAddonInfo("version"),
               "format": _FORMAT, "created": time.strftime("%Y-%m-%d %H:%M:%S")}

    if "settings" in want or "config" in want:
        values = {}
        for sid in _schema(addon):
            sensitive = sid in SENSITIVE
            if sensitive and "config" not in want:
                continue
            if not sensitive and "settings" not in want:
                continue
            values[sid] = addon.getSetting(sid)
        payload["settings"] = values
    for name in ("watched", "bookmarks"):
        if name in want:
            payload[name] = _read_store(addon, name)

    folder = dialog.browseSingle(3, "Where should the backup go?", "files")
    if not folder:
        return

    name = f"onepacepremium-backup-{time.strftime('%Y%m%d-%H%M%S')}.json"
    target = folder + name if folder.endswith(("/", "\\")) else f"{folder}/{name}"
    try:
        with xbmcvfs.File(target, "w") as handle:
            handle.write(json.dumps(payload, indent=2))
    except Exception as exc:
        log(f"[backup] export failed: {exc}")
        dialog.ok("Export", f"Could not write the backup.\n\n{exc}")
        return

    plain = {k: v for k, v in payload.get("settings", {}).items()
             if k not in SENSITIVE}
    rows = []
    for key, lab, _ in PARTS:
        if key == "config" or key not in want:
            continue
        section = plain if key == "settings" else payload.get(key)
        if section is not None:
            rows.append("  %s — %d" % (lab, _count(key, section)))
    summary = "\n".join(rows)
    if "config" in want:
        summary += "\n  Configuration key included"
    log(f"[backup] exported {sorted(want)} to {target}")
    dialog.ok("Export", f"Saved as [B]{name}[/B]\n\n{summary}")


def import_settings(_params=None):
    addon = _addon()
    dialog = xbmcgui.Dialog()

    path = dialog.browseSingle(1, "Pick a backup file", "files", ".json")
    if not path:
        return
    try:
        with xbmcvfs.File(path) as handle:
            data = json.loads(handle.read() or "{}")
    except Exception as exc:
        log(f"[backup] could not read {path}: {exc}")
        dialog.ok("Restore", f"Could not read that file.\n\n{exc}")
        return

    if data.get("addon") != ADDON_ID:
        dialog.ok("Restore", "That is not a One Pace Premium backup.")
        return

    stored = data.get("settings") or {}
    has_config = any(k in SENSITIVE for k in stored)
    available, labels, preselect = [], [], []
    for key, lab, _ in PARTS:
        if key == "config":
            if not has_config:
                continue
            present, count = True, len([k for k in stored if k in SENSITIVE])
        elif key == "settings":
            present = bool([k for k in stored if k not in SENSITIVE])
            count = len([k for k in stored if k not in SENSITIVE])
        else:
            present = isinstance(data.get(key), dict) and bool(data[key])
            count = _count(key, data.get(key, {}))
        if present:
            preselect.append(len(available))
            available.append(key)
            labels.append(f"{lab}  [COLOR FF888899]({count})[/COLOR]")

    if not available:
        dialog.ok("Restore", "That backup has nothing in it.")
        return

    picked = _pick(f"Restore from {data.get('created', 'this backup')}", labels, preselect)
    if not picked:
        return
    want = {available[i] for i in picked}

    if "config" in want and not dialog.yesno(
            "Restore", "This will replace your current configuration key.\n\nContinue?",
            nolabel="Cancel", yeslabel="Replace"):
        return

    done, known = [], set(_schema(addon))
    if want & {"settings", "config"}:
        n = 0
        for sid, value in stored.items():
            if sid not in known:
                continue
            part = "config" if sid in SENSITIVE else "settings"
            if part not in want:
                continue
            try:
                addon.setSetting(sid, str(value))
                n += 1
            except Exception as exc:
                log(f"[backup] could not set {sid}: {exc}")
        done.append(f"{n} settings")
    for name in ("watched", "bookmarks"):
        if name in want and _write_store(addon, name, data[name]):
            noun = "watched" if name == "watched" else "resume points"
            done.append(f"{_count(name, data[name])} {noun}")

    # Our lists draw the tick from watched.json, but Kodi keeps its own
    # playCount. Without this the two disagree until each episode is replayed.
    if "watched" in want:
        episode_ids = [e for key, eps in data["watched"].items()
                       if not key.startswith("__") and isinstance(eps, list)
                       for e in eps]
        if episode_ids:
            try:
                from .episode_routes import _bulk_kodi_update
                _bulk_kodi_update(episode_ids, True)
            except Exception as exc:
                log(f"[backup] could not sync Kodi state: {exc}")

    log(f"[backup] restored {sorted(want)} from {path}")
    dialog.ok("Restore", "Restored:\n\n  " + "\n  ".join(done)
              + "\n\nGo back to the add-on to see the change.")
