"""The on-disk stores: read once, write through, stay persisted."""
import json
import os
import tempfile
import harness

harness.setup()
TMP = tempfile.mkdtemp(prefix="pp-stores-")

import xbmcvfs
reads = {"n": 0}
DISK = {}


class _File:
    def __init__(self, path, mode="r"):
        self.path, self.w = path, mode == "w"
        if not self.w:
            reads["n"] += 1
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return DISK.get(self.path, "")
    def write(self, data): DISK[self.path] = data; return True
    def close(self): pass


xbmcvfs.File = _File
xbmcvfs.exists = lambda p: p in DISK
xbmcvfs.translatePath = lambda p: TMP + "/"

from lib import bookmarks as bm, watched as w

print("=== many lookups, one disk read ===")
bm.invalidate()
DISK[TMP + "/bookmarks.json"] = json.dumps({f"EP{i}": {"pos": i, "total": 100} for i in range(50)})
reads["n"] = 0
for i in range(56):
    bm.get(f"EP{i % 50}")
print(f"  56 lookups -> {reads['n']} disk read(s)")
assert reads["n"] == 1, reads["n"]

print()
print("=== write, read back, persist ===")
bm.set_bookmark("EP2", 5.0, 100.0, "pp_onepacee")
print(f"  in memory: {bm.get('EP2')}")
assert bm.get("EP2")["pos"] == 5.0, "the write is not visible to the next read"
assert json.loads(DISK[TMP + "/bookmarks.json"])["EP2"]["pos"] == 5.0, "not persisted"
bm.clear("EP2")
print(f"  after clear: {bm.get('EP2')}")
assert bm.get("EP2") is None
assert "EP2" not in json.loads(DISK[TMP + "/bookmarks.json"])

print()
print("=== an outside edit is seen after invalidate() ===")
DISK[TMP + "/bookmarks.json"] = json.dumps({"NEW": {"pos": 9, "total": 99}})
print(f"  before invalidate: {bm.get('NEW')}")
bm.invalidate()
print(f"  after invalidate:  {bm.get('NEW')}")
assert bm.get("NEW") is not None, "a restore would look like it did nothing"

print()
print("=== watched marks and unmarks round-trip ===")
w.invalidate()
DISK[TMP + "/watched.json"] = json.dumps({})
w.set_episodes_watched("pp_onepacee", ["A", "B"], True)
print(f"  marked  -> {sorted(w.get_watched('pp_onepacee'))}")
assert w.get_watched("pp_onepacee") == {"A", "B"}
w.set_episodes_watched("pp_onepacee", ["A"], False)
print(f"  unmarked A -> {sorted(w.get_watched('pp_onepacee'))}")
assert w.get_watched("pp_onepacee") == {"B"}
assert "A" not in json.loads(DISK[TMP + "/watched.json"])["pp_onepacee"]

print()
print("=== toggling flips both ways ===")
w.toggle_episode("pp_onepacee", "C")
assert "C" in w.get_watched("pp_onepacee")
w.toggle_episode("pp_onepacee", "C")
assert "C" not in w.get_watched("pp_onepacee")
print("  on, then off  OK")

print()
print("=== the memory tier stays bounded ===")
from lib import cache

# A long browse keeps one stream list per episode opened, each good for an
# hour. With the interpreter reused they would otherwise pile up all session.
cache._mem.clear()
for i in range(cache._MAX_MEM * 3):
    cache.set(f"https://x/stream/{i}", {"n": i}, 3600)
print(f"  {cache._MAX_MEM * 3} written -> {len(cache._mem)} held, cap {cache._MAX_MEM}")
assert len(cache._mem) <= cache._MAX_MEM, len(cache._mem)

reads["n"] = 0
assert cache.get("https://x/stream/0") == {"n": 0}, "an evicted entry was lost"
assert reads["n"] == 1, "it should have come back from disk, not thin air"
print("  what memory drops still comes back from disk  OK")

# Dearest first: a catalogue lasts a day, a stream list an hour.
cache._mem.clear()
cache.set("https://x/catalogue", {"keep": True}, 86400)
for i in range(cache._MAX_MEM * 2):
    cache.set(f"https://x/stream/{i}", {"n": i}, 3600)
print(f"  catalogue kept: {'https://x/catalogue' in cache._mem}, "
      f"{len(cache._mem)} held")
assert "https://x/catalogue" in cache._mem, "the dearest entry went first"
assert len(cache._mem) <= cache._MAX_MEM

# Anything already expired goes before anything still live.
cache._mem.clear()
cache.set("https://x/dead", {"old": True}, -1)
for i in range(cache._MAX_MEM):
    cache.set(f"https://x/live/{i}", {"n": i}, 3600)
print(f"  expired entry still held: {'https://x/dead' in cache._mem}")
assert "https://x/dead" not in cache._mem, "an expired entry survived the trim"
assert len(cache._mem) <= cache._MAX_MEM
print("  expired first, then whatever expires soonest  OK")

print()
print("=== stale cache files go, once a Kodi start ===")
import kodistub
import time as _time

_folder = TMP + "/"
_old = _time.time() - cache._LONGEST_TTL - 60
_fresh = _time.time() - 60
_names = ["cache_old.json", "cache_fresh.json", "bookmarks.json", "notes.txt"]
xbmcvfs.Stat.mtimes = {_folder + n: _old for n in _names}
xbmcvfs.Stat.mtimes[_folder + "cache_fresh.json"] = _fresh

_removed = []
_real_listdir, _real_delete = xbmcvfs.listdir, xbmcvfs.delete
xbmcvfs.listdir = lambda p: ([], list(_names))
xbmcvfs.delete = lambda p: _removed.append(p) or True

kodistub._WINDOW_PROPS.clear()
_gone = cache.sweep()
print(f"  swept {_gone}: {[p.rsplit('/', 1)[-1] for p in _removed]}")
assert _gone == 1, _gone
assert _removed == [_folder + "cache_old.json"], _removed
print("  only our own files, and only the ones past every ttl  OK")

# Twice in one session would mean a listdir on every navigation.
_removed.clear()
assert cache.sweep() == 0 and not _removed, "it swept twice in one Kodi session"
print("  a second browse does not sweep again  OK")

# The property lives on the home window, so a Kodi restart clears it.
kodistub._WINDOW_PROPS.clear()
_removed.clear()
assert cache.sweep() == 1, "a fresh Kodi start should sweep again"
print("  a fresh Kodi start sweeps again  OK")

# A profile with no cache folder yet must not raise on first run.
xbmcvfs.listdir = lambda p: (_ for _ in ()).throw(OSError("no such folder"))
kodistub._WINDOW_PROPS.clear()
assert cache.sweep() == 0, "a missing folder should be quiet, not fatal"
print("  a profile with nothing in it yet is fine  OK")

xbmcvfs.listdir, xbmcvfs.delete = _real_listdir, _real_delete
xbmcvfs.Stat.mtimes = {}

# It has to run where every invocation passes, or it would never run at all.
_boot = (harness.ADDON / "lib" / "utils.py").read_text(encoding="utf-8")
_reset = _boot[_boot.index("def reset_for_invocation("):]
assert "sweep()" in _reset, "nothing ever calls the sweep"
print("  wired into the start of every plugin call  OK")

print()
print("all assertions passed")
