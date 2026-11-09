"""Reuse Language Invoker: the manifest write, the warning, and the one offer."""
import harness
import kodistub

S = harness.setup({"invoker_reuse": "false", "invoker_asked": "false"})
from lib import invoker

MANIFEST = '<?xml version="1.0"?>\n<addon id="x">\n  <extension>\n' \
           '    <reuselanguageinvoker>false</reuselanguageinvoker>\n' \
           '  </extension>\n</addon>\n'

DISK = {"addon.xml": MANIFEST}
READ_ONLY = [False]


class _File:
    """A manifest in memory, which can be made read-only."""

    def __init__(self, path, mode="r"):
        self.mode, self.buffer = mode, ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        if self.mode == "w" and not READ_ONLY[0]:
            DISK["addon.xml"] = self.buffer
        return False

    def read(self):
        return DISK["addon.xml"]

    def write(self, blob):
        self.buffer += blob
        return True


invoker.xbmcvfs.File = _File
invoker._manifest = lambda: "addon.xml"

ANSWERS = []
ASKED = []


class _Asking:
    def yesno(self, heading, message, **k):
        ASKED.append(message)
        return ANSWERS.pop(0) if ANSWERS else False

    def ok(self, heading, message, **k):
        ASKED.append(message)
        return True


invoker.xbmcgui.Dialog = _Asking

print("=== what the manifest says, and setting it ===")
print(f"  read -> {invoker._read()!r}")
assert invoker._read() == "false", invoker._read()
assert invoker._write("true") is True, "the write did not land"
assert invoker._read() == "true", DISK["addon.xml"]
assert "<?xml" in DISK["addon.xml"], "the declaration was eaten"
assert DISK["addon.xml"].count("reuselanguageinvoker") == 2, DISK["addon.xml"]
print("  wrote true, and the rest of the file is untouched  OK")

READ_ONLY[0] = True
assert invoker._write("false") is False, "a folder we cannot write to looked fine"
assert invoker._read() == "true", "it reported failure but changed the file"
READ_ONLY[0] = False
invoker._write("false")
print("  a read-only folder is reported, not silently ignored  OK")

print()
print("=== the warning names the risk, and no version ===")
text = invoker._warning()
print(f"  {text.splitlines()[0]}")
assert "close unexpectedly" in text, text
for word in ("21", "22", "Kodi 2"):
    assert word not in text, f"the warning still talks about versions: {word}"
print("  one warning, the same on every Kodi  OK")

print()
print("=== turning it on needs the second answer ===")
DISK["addon.xml"] = MANIFEST
ANSWERS[:] = [True, False]          # yes, change it — then no, not worth it
invoker.choose()
print(f"  said yes then no  -> manifest {invoker._read()!r}")
assert invoker._read() == "false", "it turned on without the second answer"

ANSWERS[:] = [True, True, False]    # yes, yes, and not reloading now
invoker.choose()
print(f"  said yes twice    -> manifest {invoker._read()!r}")
assert invoker._read() == "true", DISK["addon.xml"]
assert S.get("invoker_display") == "On", S.get("invoker_display")

ASKED[:], ANSWERS[:] = [], [True, False]   # turning it off asks only once
invoker.choose()
print(f"  turning it off    -> manifest {invoker._read()!r}, {len(ASKED)} question(s)")
assert invoker._read() == "false", "turning it off should not need a warning"
assert not any("close unexpectedly" in m for m in ASKED),     "it warned about turning something off"
assert S.get("invoker_display") == "Off", S.get("invoker_display")
print("  on needs two answers, off needs one  OK")

print()
print("=== offered once, whoever you are ===")
S["invoker_asked"] = "false"
DISK["addon.xml"] = MANIFEST
ASKED[:], ANSWERS[:] = [], [False]          # "Not now"
invoker.maybe_offer()
print(f"  first browse      -> asked {len(ASKED)}, manifest {invoker._read()!r}")
assert len(ASKED) == 1, ASKED
assert invoker._read() == "false", "Not now still turned it on"
assert S.get("invoker_asked") == "true", "it will ask again next time"

ASKED[:], ANSWERS[:] = [], [True, True, False]
invoker.maybe_offer()
print(f"  second browse     -> asked {len(ASKED)}")
assert not ASKED, "it asked a second time"

# Written before the dialog, so a force quit cannot mean asking forever.
src = (harness.ADDON / "lib" / "invoker.py").read_text(encoding="utf-8")
offer = src[src.index("def maybe_offer("):]
assert offer.index('setSetting("invoker_asked", "true")') < offer.index("yesno("),     "a force quit mid-dialog would leave it asking on every browse"
print("  asked once, and the answer is remembered before it is given  OK")

print()
print("=== an update replaces the manifest, so the choice goes back ===")
S["invoker_reuse"] = "true"
DISK["addon.xml"] = MANIFEST        # an update reset it to false
assert invoker.reapply() is True, "the choice was not put back"
assert invoker._read() == "true", DISK["addon.xml"]
assert invoker.reapply() is False, "it rewrote a manifest that already agreed"

S["invoker_reuse"] = "false"
DISK["addon.xml"] = MANIFEST
assert invoker.reapply() is False, "it should never turn it on by itself"
assert invoker._read() == "false", "off is the safe direction, and it moved"
print("  put back when asked for, and never switched on by itself  OK")

root = (harness.ADDON / "lib" / "catalog_routes.py").read_text(encoding="utf-8")
assert "reapply()" in root and "maybe_offer()" in root, "neither hook is wired up"
assert root.index("set_defaults_for_new_install()") < root.index("reapply()"),     "the defaults for a fresh install are settled first"
assert root.index("end_directory(cache=True)") < root.index("maybe_offer()"),     "a dialog before the menu is drawn delays the first browse"
assert "if not maybe_show_on_update():" in root,     "the changelog and the offer would land on the same browse"
seen = (harness.ADDON / "lib" / "changelog.py").read_text(encoding="utf-8")
hook = seen[seen.index("def maybe_show_on_update("):]
assert "return True" in hook and "return False" in hook,     "the caller cannot tell whether What's New took the screen"
print("  offered after the menu is drawn, and a browse after What's New  OK")

print()
print("all assertions passed")
