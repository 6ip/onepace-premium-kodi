"""reuselanguageinvoker: nothing per-invocation may be frozen at import."""
import pathlib
import re
import harness

S = harness.setup({"debug_logging": "false"}, handle="7")
from lib import utils

print("=== the plugin handle follows sys.argv ===")
import sys, xbmcplugin
seen = []
xbmcplugin.endOfDirectory = lambda h, **k: seen.append(int(h))
for handle in ("7", "12", "31", "4"):
    sys.argv[1] = handle
    xbmcplugin.endOfDirectory(utils.ADDON_HANDLE)
    print(f"  sys.argv[1]={handle:<3} -> endOfDirectory got {seen[-1]}")
assert seen == [7, 12, 31, 4]

print()
print("=== garbage argv does not explode ===")
for bad in ([], ["one"], ["x", "nan"]):
    sys.argv = bad
    assert int(utils.ADDON_HANDLE) == -1
print("  missing / non-numeric -> -1, never a crash")
sys.argv = ["plugin://plugin.video.onepacepremium/", "7", "?"]

print()
print("=== the base URL no longer comes from argv[0] ===")
print(f"  {utils.ADDON_PATH}")
sys.argv[0] = "C:/some/script.py"          # what RunScript hands us
assert utils.build_url("list_root").startswith("plugin://plugin.video.onepacepremium/")
print(f"  build_url still correct: {utils.build_url('list_root')}")

print()
print("=== a settings change lands on the next invocation ===")
utils.log("warm the memo")
print(f"  memo after first call: {utils._DEBUG}")
S["debug_logging"] = "true"
print(f"  setting flipped, memo still: {utils._DEBUG}  <- would be stale")
utils.reset_for_invocation()
print(f"  after reset_for_invocation(): {utils._DEBUG}")
assert utils._DEBUG is True, "Debug Logging would need a Kodi restart"

print()
print("=== the on-disk stores are re-read each invocation ===")
from lib import watched, bookmarks
watched._CACHED, bookmarks._CACHED = {"stale": ["X"]}, {"stale": {}}
utils.reset_for_invocation()
print(f"  watched={watched._CACHED}  bookmarks={bookmarks._CACHED}")
assert watched._CACHED is None and bookmarks._CACHED is None

print()
print("=== the counter proves reuse at runtime ===")
utils._INVOCATIONS[0] = 0
for _ in range(3):
    utils.reset_for_invocation()
print(f"  three calls in one interpreter -> {utils._INVOCATIONS[0]}")
assert utils._INVOCATIONS[0] == 3

print()
print("=== nothing else is frozen at import ===")
frozen = []
for f in sorted((harness.ADDON / "lib").glob("*.py")):
    for line in f.read_text(encoding="utf-8").split("\n"):
        if re.match(r"^[A-Z_]+ *=.*sys\.argv", line):
            frozen.append(f"{f.name}: {line.strip()}")
print(f"  module-level sys.argv reads: {frozen or 'none'}")
assert not frozen

print()
print("=== no module keeps its own ADDON reference ===")
stale = []
for f in sorted((harness.ADDON / "lib").glob("*.py")):
    for line in f.read_text(encoding="utf-8").split("\n"):
        if re.search(r"^from \.utils import .*\bADDON\b(?!_)", line):
            stale.append(f"{f.name}: {line.strip()}")
print(f"  modules importing ADDON by name: {stale or 'none'}")
assert not stale, "reset_for_invocation would never reach these"

print()
print("=== the router resets before doing anything ===")
src = (harness.ADDON / "lib" / "router.py").read_text(encoding="utf-8")
body = src[src.index("def addon_router():"):]
first = next(l.strip() for l in body.split("\n")[1:] if l.strip())
print(f"  first statement: {first}")
assert first == "reset_for_invocation()"

print()
print("=== reuselanguageinvoker stays off ===")
# Pooled interpreters faulted in python3.8.dll on every crash between
# 2026-08-22 and 2026-08-24. Blocking one for a whole episode is the cost.
x = (harness.ADDON / "addon.xml").read_text(encoding="utf-8")
meta = x[x.index('<extension point="xbmc.addon.metadata">'):]
assert "<reuselanguageinvoker>false</reuselanguageinvoker>" in meta[:400], \
    "it must sit in xbmc.addon.metadata, and it must say false"
print("  declared false, inside xbmc.addon.metadata  OK")

print()
print("all assertions passed")
