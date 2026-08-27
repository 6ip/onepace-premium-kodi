"""The custom windows: their skins, wiring and key handling."""
import os
import re
import xml.etree.ElementTree as ET
import harness

harness.setup()
SKINS = harness.ADDON / "resources" / "skins" / "Default" / "1080i"

print("=== every skin file parses and only uses proven fonts ===")
for xml in sorted(SKINS.glob("*.xml")):
    root = ET.parse(xml).getroot()
    fonts = sorted({f.text for f in root.iter("font") if f.text})
    assert set(fonts) <= {"font12", "font13", "font14", "font16"}, (xml.name, fonts)
    print(f"  {xml.name:<20} {len(list(root.iter('control')))} controls, fonts {fonts}")

print()
print("=== every texture a skin references exists ===")
missing = []
for xml in sorted(SKINS.glob("*.xml")):
    for tag in ET.parse(xml).getroot().iter():
        if not tag.tag.startswith("texture"):
            continue
        val = (tag.text or "").strip()
        if not val or val == "-" or val.startswith("$INFO"):
            continue
        if not os.path.exists(os.path.normpath(str(SKINS / val))):
            missing.append(f"{xml.name}: {val}")
print(f"  missing: {missing or 'none'}")
assert not missing

print()
print("=== no control is unreachable by remote ===")
for xml in sorted(SKINS.glob("*.xml")):
    root = ET.parse(xml).getroot()
    focusable = {c.get("id") for c in root.iter("control")
                 if c.get("type") in ("button", "scrollbar") and c.get("id")}
    if not focusable:
        continue
    reachable = {e.text.strip() for c in root.iter("control")
                 for nav in ("onleft", "onright", "onup", "ondown")
                 for e in [c.find(nav)] if e is not None and e.text}
    default = root.find("defaultcontrol")
    if default is not None and default.text:
        reachable.add(default.text.strip())
    orphans = focusable - reachable
    print(f"  {xml.name:<20} focusable={sorted(focusable)} orphans={sorted(orphans) or 'none'}")
    assert not orphans, f"{xml.name} has controls nothing navigates to"

    # Buttons pointing at each other satisfies the check above while leaving
    # them stranded: focus starts on the default control and has to get out.
    if default is not None and default.text:
        start = default.text.strip()
        out = {e.text.strip() for c in root.iter("control") if c.get("id") == start
               for nav in ("onleft", "onright", "onup", "ondown")
               for e in [c.find(nav)] if e is not None and e.text} - {start}
        assert out & focusable, (f"{xml.name}: focus starts on {start} "
                                 f"and no arrow key reaches a button")

print()
print("=== a window opened from a menu row lets go of the listing first ===")
# A non-folder row leaves Kodi waiting on this invocation. doModal does not
# return until the window is closed, so Kodi times the listing out after two
# minutes and the whole media window wedges.
import inspect
from lib import downloads_manager, route_common
body = inspect.getsource(downloads_manager.show)
# The comment explaining this names doModal before the call it guards.
code = chr(10).join(l for l in body.split(chr(10)) if not l.lstrip().startswith("#"))
assert "end_directory" in body, "the listing is never released"
assert code.index("end_directory") < code.index("doModal"),     "the directory is only ended once the window closes, which is far too late"
print("  end_directory comes before doModal  OK")

for opener, source in (("open_addon_settings", inspect.getsource(route_common.open_addon_settings)),
                       ("downloads_manager.show", body)):
    assert "succeeded=False" in source, f"{opener} reports a listing it never drew"
    print(f"  {opener:<24} ends it, unsuccessfully  OK")

# always="true" drags focus back to a control that is hidden when the queue is
# empty, which Kodi logs as an error on every redraw.
manager = (SKINS / "downloads_manager.xml").read_text(encoding="utf-8")
default = re.search(r"<defaultcontrol([^>]*)>(\d+)<", manager)
print(f"  default control: {default.group(2)}, always={'always' in default.group(1)}")
assert "always" not in default.group(1), "focus is forced back onto a hidden list"
hidden = manager.split('<control type="list"')[1].split(">")[0]
assert default.group(2) != "2500", "the default control is the one that gets hidden"

print()
print("=== a group's children are what the group actually moves ===")
# Well-formed XML that nests wrongly still parses and still finds its
# textures, so nothing above this catches a control left outside its group.
for xml in sorted(SKINS.glob("*.xml")):
    for group in ET.parse(xml).getroot().iter("control"):
        if group.get("type") != "group":
            continue
        animation = group.find("animation")
        if animation is None or not animation.get("condition"):
            continue
        moved = group.findall("control")
        tops = {c.findtext("top") for c in moved if c.findtext("top")}
        print(f"  {xml.name}: a conditional group moves {len(moved)} control(s) at top {sorted(tops)}")
        # Everything drawn on one line has to travel together or it comes apart.
        assert len(moved) > 1 or len(tops) == 1,             f"{xml.name}: only part of the row moves with the group"
        assert len(tops) == 1,             f"{xml.name}: the group holds controls at different heights: {sorted(tops)}"

print()
print("=== window properties: what the skin reads, the python sets ===")
for skin, module in [("changelog.xml", "changelog"), ("donate.xml", "donate"),
                     ("downloads_manager.xml", "downloads_manager")]:
    reads = set(re.findall(r"Window\.Property\((pp\.[a-z]+)\)",
                           (SKINS / skin).read_text(encoding="utf-8")))
    sets = set(re.findall(r'setProperty\("(pp\.[a-z]+)"',
                          (harness.ADDON / "lib" / f"{module}.py").read_text(encoding="utf-8")))
    print(f"  {skin:<16} reads {sorted(reads)}")
    assert reads <= sets, f"{skin} reads properties nothing sets: {reads - sets}"

print()
print("=== the textbox is bound to its scrollbar, or nothing scrolls ===")
root = ET.parse(SKINS / "changelog.xml").getroot()
page = [c.find("pagecontrol").text for c in root.iter("control")
        if c.get("id") == "2000" and c.find("pagecontrol") is not None]
print(f"  textbox 2000 -> pagecontrol {page}")
assert page == ["2060"]

print()
print("=== Enter closes the changelog, except on a button ===")
import kodistub
from lib.changelog import ChangelogDialog


class Probe(ChangelogDialog):
    def __init__(self, focus):
        self._focus, self.donate, self.closed = focus, False, False
    def getFocusId(self): return self._focus
    def close(self): self.closed = True


Action = type("Action", (), {"__init__": lambda s, i: setattr(s, "_i", i),
                             "getId": lambda s: s._i})
SCROLLBAR, DONATE, CLOSE = 2060, 11, 10
for label, focus, action, want in [
        ("Enter on the scrollbar", SCROLLBAR, 7, True),
        ("Back on the scrollbar", SCROLLBAR, 92, True),
        ("Down on the scrollbar", SCROLLBAR, 4, False),
        ("Enter on Donate", DONATE, 7, False),
        ("Enter on Close", CLOSE, 7, False),
        ("Back on Donate", DONATE, 92, True)]:
    d = Probe(focus)
    d.onAction(Action(action))
    print(f"  {label:<24} -> {'closed' if d.closed else 'open'}")
    assert d.closed is want, label

d = Probe(DONATE)
d.onAction(Action(7))
assert not d.closed, "Enter would swallow the click and skip the donate window"
d.onClick(DONATE)
assert d.donate and d.closed
print("  the Donate click still gets through  OK")

print("=== the next-episode card's progress bar ===")
from lib.next_episode_card import NextEpisodeCard


class _Bar:
    def __init__(self): self.pct = None
    def setPercent(self, v): self.pct = v


class _Player:
    def __init__(self, now, total): self.now, self.total = now, total
    def getTime(self): return self.now
    def getTotalTime(self): return self.total
    def isPlaying(self): return True


card = NextEpisodeCard.__new__(NextEpisodeCard)
card._bar = _Bar()
TOTAL, SHOWN_AT = 1800.0, 1780.0
span = TOTAL - SHOWN_AT
for now, expect, label in [(SHOWN_AT, 100.0, "just shown"),
                           (TOTAL, 0.0, "at the end"),
                           (TOTAL + 30, 0.0, "past the end"),
                           (SHOWN_AT - 600, 100.0, "seeked back")]:
    card._update_progress(_Player(now, TOTAL), TOTAL, span)
    print(f"  {label:<14} -> {card._bar.pct:.0f}%")
    assert card._bar.pct == expect, (label, card._bar.pct)
print("  always clamped to 0-100, however you seek  OK")


print()
print("=== the changelog itself ===")
txt = (harness.ADDON / "changelog.txt").read_text(encoding="utf-8")
vers = re.findall(r"\[B\]v([0-9.]+)\[/B\]", txt)
key = lambda v: tuple(int(x) for x in v.split("."))
print(f"  versions: {vers}")
assert vers == sorted(vers, key=key, reverse=True), "not newest-first"
assert txt.count("[B]") == txt.count("[/B]"), "unbalanced bold"
assert txt.count("[COLOR") == txt.count("[/COLOR]"), "unbalanced colour"
addon_v = re.search(r'id="plugin\.video\.onepacepremium" version="([^"]+)"',
                    (harness.ADDON / "addon.xml").read_text(encoding="utf-8")).group(1)
print(f"  addon.xml={addon_v}, changelog top={vers[0]}")
assert addon_v == vers[0], "bump the changelog when you bump the version"
bullets = [l.strip()[3:].strip() for l in txt.splitlines() if l.strip().startswith("•")]
longest = max(bullets, key=len)
print(f"  {len(bullets)} bullets, longest {len(longest)} chars")
assert len(longest) <= 95, f"getting wordy: {longest}"

print()
print()
print("=== the What's New window cycles both ways ===")
import re as _re
_xml = (harness.ADDON / "resources" / "skins" / "Default" / "1080i"
        / "changelog.xml").read_text(encoding="utf-8")
_nav = {}
for _m in _re.finditer(r'<control type="(?:scrollbar|button)" id="(\d+)">(.*?)</control>',
                       _xml, _re.S):
    _left = _re.search(r"<onleft>(\d+)</onleft>", _m.group(2))
    _right = _re.search(r"<onright>(\d+)</onright>", _m.group(2))
    _nav[_m.group(1)] = (_left.group(1) if _left else "", _right.group(1) if _right else "")
for _who, (_l, _r) in sorted(_nav.items()):
    print(f"  {_who:>5}: left -> {_l:<5} right -> {_r}")
assert all(l and r for l, r in _nav.values()), "an arrow that goes nowhere reads as broken"
assert all(l != who and r != who for who, (l, r) in _nav.items()),     "a control pointing at itself swallows the keypress"
_seen, _at = [], "2060"
for _ in range(len(_nav)):
    _seen.append(_at)
    _at = _nav[_at][1]
assert _at == "2060" and len(set(_seen)) == len(_nav), f"right does not cycle: {_seen}"
_seen, _at = [], "2060"
for _ in range(len(_nav)):
    _seen.append(_at)
    _at = _nav[_at][0]
assert _at == "2060" and len(set(_seen)) == len(_nav), f"left does not cycle: {_seen}"
print("  every control is reachable from either arrow  OK")

print()
print("all assertions passed")
