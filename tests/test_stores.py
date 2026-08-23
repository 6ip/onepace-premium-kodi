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
print("all assertions passed")
