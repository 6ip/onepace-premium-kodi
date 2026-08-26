"""Export and restore: what travels, what stays private, what is left alone."""
import json
import harness

S = harness.setup({"secret_string": "rdkey=SECRET123", "base_url": "https://x.example",
                   "stremio_api_prefix": "/p/", "watched_threshold": "72",
                   "autoplay_next": "true", "hide_watched": "true",
                   "highlight_color": "ff00ff00", "highlight_color_display": "Green",
                   "sub_langs": "eng,ara", "last_seen_version": "9.9.9"})

import xbmcvfs
FILES = {
    str(harness.FIXTURES) + "/watched.json": json.dumps(
        {"pp_onepacee": ["RO_1", "RO_2"], "__totals__": {"pp_onepacee": 473}}),
    str(harness.FIXTURES) + "/bookmarks.json": json.dumps(
        {"AR_1": {"pos": 300.0, "total": 1847.6}}),
}
xbmcvfs.exists = lambda p: p in FILES


class _File:
    def __init__(self, path, mode="r"):
        self.path = path
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return FILES.get(self.path, "")
    def write(self, data): FILES[self.path] = data; return True
    def close(self): pass


xbmcvfs.File = _File
from lib import backup
D = harness.dialog()


def export(picks, folder="C:/out/"):
    D.multiselect_answers, D.browse_answers = [picks], [folder]
    harness.recorder()
    backup.export_settings()
    written = [k for k in FILES if k.startswith(folder)]
    return json.loads(FILES[written[-1]]) if written else None


print("=== the configuration key is off by default ===")
p = export([0, 1, 2])
leaked = [k for k in backup.SENSITIVE if k in p.get("settings", {})]
print(f"  sections: {sorted(k for k in p if k in ('settings', 'watched', 'bookmarks'))}")
print(f"  sensitive leaked: {leaked or 'none'}")
assert not leaked and "SECRET123" not in json.dumps(p)
print("  the key appears nowhere in the file  OK")

print()
print("=== picker labels travel with their values ===")
for value, label in [("highlight_color", "highlight_color_display"),
                     ("sub_langs", "sub_langs_display")]:
    both = value in p["settings"] and label in p["settings"]
    print(f"  {value:<18} + {label:<26} {both}")
    assert both, label
print(f"  only skipped: {sorted(backup.SKIP)}")
assert backup.SKIP == {"last_seen_version"}

print()
print("=== ticking the key includes it ===")
p2 = export(list(range(len(backup.PARTS))))          # every part, by name not index
assert all(k in p2["settings"] for k in backup.SENSITIVE)
print(f"  secret_string present: {'secret_string' in p2['settings']}")

backup_src = (harness.ADDON / "lib" / "backup.py").read_text(encoding="utf-8")

print()
print("=== a reinstall should not lose sight of the files on disk ===")
names = [key for key, _, _ in backup.PARTS]
print(f"  parts: {names}")
assert "downloads" in names, "the index would be gone and every download invisible"
label = next(text for key, text, _ in backup.PARTS if key == "downloads")
print(f"  shown as: {label!r}")
assert "episode" not in label.lower(), "that reads as though it backs up the videos"

# Every store must be read and written, or a part can be ticked and silently
# left out — which is what a hardcoded pair of names did here.
for src, what in ((backup_src[backup_src.index("def export_settings("):
                              backup_src.index("def import_settings(")], "export"),
                  (backup_src[backup_src.index("def import_settings("):], "restore")):
    assert "for name in _STORES:" in src, f"{what} walks a fixed list of stores"
    assert '("watched", "bookmarks")' not in src, f"{what} still hardcodes the pair"
print("  export and restore both walk every store  OK")

whole = export(list(range(len(backup.PARTS))))
print(f"  a full backup carries: {sorted(k for k in whole if k in backup._STORES)}")
assert "downloads" in whole, "ticking it did nothing"
assert backup._STORES.get("downloads") == "downloads.json", backup._STORES
on_by_default = {key for key, _, default in backup.PARTS if default}
assert "downloads" in on_by_default, "it is the common case, so it should be ticked"
assert "config" not in on_by_default, "the key should stay opt-in"
print("  downloads travel with the backup, ticked by default  OK")

print()
print("=== you can export one part alone ===")
p3 = export([1])
print(f"  watched only -> {sorted(k for k in p3 if k in ('settings', 'watched', 'bookmarks'))}")
assert "watched" in p3 and "settings" not in p3 and "bookmarks" not in p3

print()
print("=== restoring only what you tick ===")
FILES[str(harness.FIXTURES) + "/watched.json"] = json.dumps({})
FILES[str(harness.FIXTURES) + "/bookmarks.json"] = json.dumps({})
S.update({"secret_string": "rdkey=MINE", "watched_threshold": "85"})
FILES["C:/in/b.json"] = json.dumps(p)
import lib.episode_routes as er
synced = {}
er._bulk_kodi_update = lambda ids, watched: synced.update(ids=list(ids), watched=watched)
D.browse_answers, D.multiselect_answers, D.yesno_answers = ["C:/in/b.json"], [[0, 1]], [True]
backup.import_settings()
print(f"  settings   -> watched_threshold={S['watched_threshold']!r}")
_w = json.loads(FILES[str(harness.FIXTURES) + "/watched.json"])
print(f"  watched    -> {_w}")
_b = json.loads(FILES[str(harness.FIXTURES) + "/bookmarks.json"])
print(f"  bookmarks  -> {_b} (unticked)")
print(f"  local key kept: {S['secret_string']!r}")
assert S["watched_threshold"] == "72"
assert json.loads(FILES[str(harness.FIXTURES) + "/bookmarks.json"]) == {}
assert S["secret_string"] == "rdkey=MINE", "a key-less backup must not clear the local key"

print()
print("=== Kodi's own playcount is synced too ===")
print(f"  _bulk_kodi_update got {synced.get('ids')} watched={synced.get('watched')}")
assert synced.get("ids") == ["RO_1", "RO_2"] and synced.get("watched") is True

print()
print("=== a foreign file is refused ===")
FILES["C:/in/bad.json"] = json.dumps({"addon": "plugin.video.umbrella", "settings": {}})
D.browse_answers = ["C:/in/bad.json"]
rec = harness.recorder()
backup.import_settings()
print(f"  {rec.notifications[-1][0]}")
assert "not a One Pace Premium backup" in rec.notifications[-1][0]

print()
print("all assertions passed")
