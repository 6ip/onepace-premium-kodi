"""Settings, router and imports: the plumbing that breaks quietly."""
import ast
import re
import xml.etree.ElementTree as ET
import harness

harness.setup()
LIB = harness.ADDON / "lib"
SETTINGS = ET.parse(harness.ADDON / "resources" / "settings.xml").getroot()
ROUTER = (LIB / "router.py").read_text(encoding="utf-8")

print("=== every settings action resolves to a real route ===")
known = set(re.findall(r"\"(\w+)\":\s*(?:None|\")", ROUTER))
bad = []
for s in SETTINGS.iter("setting"):
    m = re.search(r"action=(\w+)", s.get("action") or "")
    if m and m.group(1) not in known:
        bad.append((s.get("id"), m.group(1)))
used = sorted({m.group(1) for s in SETTINGS.iter("setting")
               for m in [re.search(r"action=(\w+)", s.get("action") or "")] if m})
print(f"  plugin actions used: {used}")
assert not bad, bad

# The dispatch imports by name, so a typo cannot fail until someone clicks.
targets = re.findall(r"\"(\w+):(\w+)\"", ROUTER)
gone = [f'{m}:{fn}' for m, fn in targets
        if f'def {fn}(' not in (LIB / f'{m}.py').read_text(encoding='utf-8')]
assert not gone, gone
print(f"  {len(targets)} lazy targets, all present")

# Resolving by name defers the TypeError to click time, so check arity here.
import inspect
from lib import router as _r
wrong = []
for _action, _t in _r._ACTIONS.items():
    if not _t:
        continue
    _p = inspect.signature(_r._resolve(_t)).parameters.values()
    _n = len([x for x in _p if x.kind in (x.POSITIONAL_ONLY, x.POSITIONAL_OR_KEYWORD)])
    if _n != 1:
        wrong.append(f'{_action} -> {_t} takes {_n}, dispatched with 1')
# The root menu is dispatched by hand, so compare that call to its signature.
_call = re.search(r'_resolve\("catalog_routes:list_root"\)\(([^)]*)\)', ROUTER)
_passed = 1 if _call and _call.group(1).strip() else 0
_root = inspect.signature(_r._resolve('catalog_routes:list_root')).parameters.values()
_takes = len([x for x in _root if x.kind in (x.POSITIONAL_ONLY, x.POSITIONAL_OR_KEYWORD)])
if _passed != _takes:
    wrong.append(f'list_root takes {_takes} args, router passes {_passed}')
assert not wrong, wrong
print("  every target accepts what the router passes it")

seasons = (LIB / "episode_routes.py").read_text(encoding="utf-8")
block = seasons[seasons.index("season_ctx_label ="):seasons.index("addContextMenuItems(season_menu)")]
assert "download_season" in block, "Download Season is not on the season row"
assert "if downloads_on:" in block, "it would show with downloads turned off"
print("  Download Season sits on the season row, behind the setting")

print()
tabs = [c.get("label") for c in SETTINGS.iter("category")]
print(f"  tabs: {tabs}")
assert tabs.index("Downloads") < tabs.index("Tools"), "Downloads should sit before Tools"
groups = {c.get("label"): [x.get("label") for x in c if x.get("type") == "lsep"]
          for c in SETTINGS.iter("category")}
print(f"  Downloads: {groups['Downloads']}")
print(f"  Tools:     {groups['Tools']}")
assert groups["Downloads"] == ["Where They Go", "In the Lists", "Updates", "History"]
assert groups["Tools"] == ["Backup", "Cache", "Advanced"]

# Every row in the Downloads tab greys out with the feature turned off.
rows = list(next(c for c in SETTINGS.iter("category") if c.get("label") == "Downloads"))
for at, row in enumerate(rows):
    rule = row.get("enable", "")
    if rule.startswith("eq(-"):
        target = rows[at - int(rule[4:rule.index(",")])].get("id")
        assert target == "downloads_enabled", (row.get("id"), rule, target)
assert rows[0].get("id") == "downloads_enabled", "the switch has to come first"
assert rows[0].get("default") == "false", "an update should not change a menu unasked"
print(f"  {sum(1 for r in rows if r.get('enable', '').startswith('eq(-'))} rows follow the switch")
assert "[COLOR" not in ET.tostring(SETTINGS, encoding="unicode"), "labels carry markup"
default = next(x for x in SETTINGS.iter("setting") if x.get("id") == "highlight_color")
print(f"  show name colour default: {default.get('default')}")
assert default.get("default") == "ff00d4ff", "the pink default is still there"

print()
print("=== every RunScript target exists ===")
missing = []
for s in SETTINGS.iter("setting"):
    for script in re.findall(r"addons/plugin\.video\.onepacepremium/([\w/]+\.py)", s.get("action") or ""):
        if not (harness.ADDON / script).exists():
            missing.append(script)
print(f"  missing scripts: {sorted(set(missing)) or 'none'}")
assert not missing

print()
print("=== no duplicate or dangling setting ids ===")
ids = [s.get("id") for s in SETTINGS.iter("setting") if s.get("id")]
dupes = [i for i in set(ids) if ids.count(i) > 1]
print(f"  {len(ids)} settings, duplicates: {dupes or 'none'}")
assert not dupes

print()
print("=== every non-action setting is read somewhere ===")
source = "\n".join(p.read_text(encoding="utf-8") for p in LIB.glob("*.py"))
unread = [s.get("id") for s in SETTINGS.iter("setting")
          if s.get("id") and s.get("type") not in ("action",)
          and f'"{s.get("id")}"' not in source]
print(f"  never read: {unread or 'none'}")
assert not unread, "a setting nothing reads is a lie to the user"

print()
print("=== relative enable/visible offsets still point where they should ===")
for cat in SETTINGS.findall("category"):
    items = cat.findall("setting")
    for i, s in enumerate(items):
        for attr in ("enable", "visible"):
            for off in re.findall(r"eq\(-(\d+),", s.get(attr) or ""):
                target = items[i - int(off)]
                assert target.get("id"), f"{s.get('id')} {attr} points at a separator"
                print(f"  {s.get('id'):<24} {attr} -> {target.get('id')}")

print()
print("=== no unused imports ===")
unused = []
for p in sorted(LIB.glob("*.py")):
    src = p.read_text(encoding="utf-8")
    names = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            names |= {(a.asname or a.name).split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            names |= {a.asname or a.name for a in n.names}
    unused += [f"{p.name}: {x}" for x in names if src.count(x) < 2]
print(f"  {unused or 'none'}")
assert not unused

print()
print("=== heavy imports stay lazy ===")
for module, heavy in [("utils.py", "requests"), ("playback.py", "requests"),
                      ("provider_api.py", "concurrent")]:
    src = (LIB / module).read_text(encoding="utf-8")
    top = [n for n in ast.parse(src).body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {a.name.split(".")[0] for n in top if isinstance(n, ast.Import) for a in n.names}
    names |= {(n.module or "").split(".")[0] for n in top if isinstance(n, ast.ImportFrom)}
    print(f"  {module:<18} {heavy} at module level: {heavy in names}")
    assert heavy not in names, f"{module} would pay for {heavy} on every invocation"

print()
print("=== nothing left half-finished ===")
marks = [f"{p.name}:{i}" for p in LIB.glob("*.py")
         for i, l in enumerate(p.read_text(encoding="utf-8").split("\n"), 1)
         if re.search(r"\bTODO\b|\bFIXME\b|^\s*print\(", l)]
print(f"  TODO / FIXME / stray print: {marks or 'none'}")
assert not marks

print()
print("all assertions passed")
