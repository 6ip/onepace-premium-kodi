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
card.properties = {}
card.setProperty = lambda k, v: card.properties.__setitem__(k, v)
TOTAL, SHOWN_AT = 1800.0, 1780.0
span = TOTAL - SHOWN_AT
card._window = span
card.out_of_range = False
for now, expect, label in [(SHOWN_AT, 100.0, "just shown"),
                           (TOTAL, 0.0, "at the end"),
                           (TOTAL + 30, 0.0, "past the end"),
                           (SHOWN_AT - 600, 100.0, "seeked back")]:
    # Seeking far back is what used to leave it up reading 12m 25s.
    card.out_of_range = False
    card._tick(_Player(now, TOTAL), TOTAL, span)
    if card.out_of_range:
        print(f"  {label:<14} -> put away, {TOTAL - now:.0f}s left is outside the range")
        continue
    print(f"  {label:<14} -> {card._bar.pct:.0f}%  ({card.properties['pp.countdown']})")
    assert card._bar.pct == expect, (label, card._bar.pct)
print("  always clamped to 0-100, however you seek  OK")

print()
print("=== the bar cannot take the remote away from the player ===")
CARD_XML = (SKINS / "next_episode.xml").read_text(encoding="utf-8")
root = ET.fromstring(CARD_XML)
focusable = [c.get("id") for c in root.iter("control")
             if c.get("type") in ("button", "scrollbar", "list", "radiobutton")]
print(f"  focusable controls: {focusable or 'none'}")
assert not focusable, "a focusable control means Kodi keeps the input here"
assert root.find("defaultcontrol") is None, "a default control pulls focus to the bar"
# What actually keeps the remote with the player: a modal dialog gets the
# input whether or not anything in it can be focused.
assert root.findtext("modality") == "modeless",     "without this Kodi routes the remote here instead of to the player"
print("  the window is modeless  OK")

# Kodi's own controls appear across the bottom, right where this sits.
osd = [a for a in root.iter("animation")
       if "videoosd" in (a.get("condition") or "")]
print("  when the player's controls appear: "
      + ", ".join(a.get("effect") for a in osd))
assert osd, "the bar would sit under the OSD"
assert all(a.get("effect") == "slide" for a in osd),     "fading a group fades each control in it, and the layers show through"
assert any("-" in a.get("end", "") for a in osd), "it should move up, not down"

# Every skin puts its controls at a different height, and Kodi will not say
# where they are, so how far to step up is the viewer's to set. A slide cannot
# read a distance from a property, hence one animation per step.
lifts = sorted(int(a.get("end").split("-")[1]) for a in osd)
print(f"  lift steps offered: {lifts}")
assert lifts == list(range(20, 241, 20)), lifts
# 0 leaves the bar under the controls, so it is not where the slider starts.
_settings = (harness.ADDON / "resources" / "settings.xml").read_text(encoding="utf-8")
import re as _re2
_lift = _re2.search(r'id="autoplay_card_lift"[^/]*default="(\d+)"[^/]*range="0,(\d+),(\d+)"',
                    _settings)
print(f"  default {_lift.group(1)}px, step {_lift.group(2)}, up to {_lift.group(3)}")
assert int(_lift.group(1)) >= 80, "the default leaves the bar under the controls"
assert int(_lift.group(1)) % int(_lift.group(2)) == 0, "the default is not on a step"
for a in osd:
    assert "pp.lift" in a.get("condition"), "every step must be gated on the setting"
assert 'id="autoplay_card_lift"' in (harness.ADDON / "resources" / "settings.xml").read_text(
    encoding="utf-8"), "nothing sets it"
src_card = (harness.ADDON / "lib" / "next_episode_card.py").read_text(encoding="utf-8")
assert 'setProperty("pp.lift"' in src_card, "the skin is never told which step"

# No single boolean means "the player's controls are on screen" — Estuary's
# own seek bar watches a list of them, so this watches the same shape.
condition = osd[0].get("condition")
watched = [w.strip() for w in condition.split("+")[0].strip(" []").split("|")]
print(f"  watching {len(watched)} player states, not just videoosd")
assert len(watched) >= 5, watched
for needed in ("Player.Seeking", "Player.ShowInfo", "fullscreeninfo"):
    assert any(needed in w for w in watched), f"{needed} is not watched"

# Nothing dims or fades: a group fade turns every layer in it translucent at
# once, and the seams between them show.
assert not [a for a in root.iter("animation") if a.get("effect") == "fade"
            and a.get("condition")],     "a conditional fade here shows the card's layers through one another"
print("  it moves and nothing else  OK")

SRC_CARD = (harness.ADDON / "lib" / "next_episode_card.py").read_text(encoding="utf-8")
assert "def onClick(" not in SRC_CARD, "nothing can be clicked, so nothing should listen"
assert "fullscreenvideo" in SRC_CARD,     "an action put back without a window lands on this dialog again"
act = SRC_CARD[SRC_CARD.index("def onAction("):]
# Kodi delivers something the instant the window opens, and with nothing here
# able to take focus it arrived as a key press and shut the bar at once.
assert "_SETTLE" in act and "_NOOP" in act,     "whatever Kodi sends on opening will close the bar immediately"
# Moving the mouse closed it, which is not an instruction about anything.
assert "_MOUSE_QUIET" in act, "a nudge of the desk cancels the next episode"
# Kodi routes everything to the window on top, so anything we cannot hand
# back has to make us step aside or the player never hears it.
assert "self.stepped_aside = True" in act, "an unrecognised key would be swallowed"
assert act.index("_FORWARD.get") < act.index("stepped_aside = True"),     "it would step aside before trying to hand the action back"
# Pausing to fetch a drink is not a decision about the next episode.
assert act.index("_CLOSE_ACTIONS") < act.index("self.dismissed = True"),     "something other than Back can cancel the next episode"
assert act.index("self.touched = True") < act.index("_CLOSE_ACTIONS"),     "being present should count however the bar ends"
# ActionTranslator.cpp names, which are not the action names: 77 is
# ACTION_PLAYER_FORWARD but the builtin only answers to "fastforward".
# Kodi does not honour <modality>modeless</modality> for a dialog Python
# opened, so every one of these arrives here and has to be passed on by hand.
from lib import next_episode_card as _card
# The builtins fired and nothing moved — the log said "handing stepback back"
# eleven times — so seeking and pausing are done on the player itself.
for label, action_id in (("left arrow", 1), ("right arrow", 2),
                         ("up", 3), ("down", 4)):
    assert action_id in _card._SEEK, f"{label} ({action_id}) does not seek"

    assert action_id not in _card._FORWARD, f"{label} should not go through a builtin"
assert _card._SEEK[1] < 0 < _card._SEEK[2], "left and right seek the wrong ways"
assert abs(_card._SEEK[3]) > abs(_card._SEEK[1]), "up and down should jump further"
drive = _card.__dict__["NextEpisodeCard"].__dict__["_drive"].__doc__
assert "player" in drive.lower()
for label, action_id in (("left click", 100), ("select", 7), ("info", 11)):
    assert action_id in _card._FORWARD, f"{label} ({action_id}) never reaches the player"
# Every action is accounted for: handed back, left to Kodi, seeked, or it
# closes the bar. Anything else makes the bar step aside, which is the escape
# hatch rather than a plan.
covered = set(_card._FORWARD) | _card._GLOBAL | set(_card._SEEK) | set(_card._CLOSE_ACTIONS)
assert len(covered) == (len(_card._FORWARD) + len(_card._GLOBAL)
                        + len(_card._SEEK) + len(_card._CLOSE_ACTIONS)),     "an action id is in two groups at once"
assert 101 in _card._CLOSE_ACTIONS, "right click is Back everywhere else in Kodi"
assert set(_card._MOUSE_QUIET) == {106, 107, 108, 109},     "a gesture ending should not be read as an instruction"
# A touchscreen sends its own actions, so without these a tap fell through to
# "not ours" and the bar closed instead of opening the player's controls. The
# ids are the same on Kodi 21 and 22, checked against ActionIDs.h.
assert _card._FORWARD.get(401) == "osd", "a tap does not reach the player"
assert _card._FORWARD.get(410) == "osd", "a ten-finger tap is still a tap"
for label, action_id in (("long press", 411), ("gesture notify", 500),
                         ("pan", 504), ("gesture abort", 505)):
    assert action_id in _card._TOUCH_QUIET, f"{label} would close the bar"
    assert action_id not in _card._FORWARD, f"{label} is not an instruction"
# A swipe is a deliberate press, so it steps aside like any unknown action.
assert 511 not in _card._TOUCH_QUIET and 511 not in _card._FORWARD,     "a swipe should reach the player the same way any other key does"
assert not set(_card._MOUSE_QUIET) & set(_card._TOUCH_QUIET)
# Kodi's keyboard map handles these in its <global> section, so they reach the
# player anyway. Acting on them too meant it paused, we saw it paused, and we
# resumed it — a pause that lasted milliseconds.
for label, action_id in (("pause", 12), ("stop", 13), ("play", 79),
                         ("volume up", 88), ("wheel up", 104)):
    assert action_id in _card._GLOBAL, f"{label} would be handled twice"
    assert action_id not in _card._FORWARD and action_id not in _card._SEEK
assert "Player.PlayPause" not in SRC_CARD, "still driving what Kodi already drives"
# But they must not close the bar either.
drive = SRC_CARD[SRC_CARD.index("def _drive("):]
assert drive.index("_GLOBAL") < drive.index("return False"),     "a global key would fall through and make the bar step aside"

print(f"  {len(_card._FORWARD)} actions handed back, "
      f"{len(_card._CLOSE_ACTIONS)} cancel, the rest step aside  OK")

from lib.next_episode_card import countdown_label
shown = {s: countdown_label(s) for s in (0, 30, 59, 60, 61, 119, 120, 300)}
print("  countdown: " + ", ".join(f"{k}->{v}" for k, v in shown.items()))
assert shown[59] == "59s" and shown[60] == "1m" and shown[119] == "1m 59s"
assert "0m" not in "".join(shown.values()), "under a minute should not say 0m"
print("  the mouse and whatever arrives on opening are ignored  OK")

print()
print("=== still watching asks, and silence means stop ===")
SW = (harness.ADDON / "lib" / "still_watching.py").read_text(encoding="utf-8")
assert "self.keep_going = False" in SW, "a sleeping viewer would carry on by default"
sw_xml = ET.fromstring((SKINS / "still_watching.xml").read_text(encoding="utf-8"))
buttons = sorted(c.get("id") for c in sw_xml.iter("control") if c.get("type") == "button")
print(f"  buttons: {buttons}, default focus {sw_xml.findtext('defaultcontrol')}")
assert buttons == ["10", "11"], buttons
# Nothing is playing by now, so this one is a question and does have buttons.
assert sw_xml.findtext("defaultcontrol") == "11", "focus should start on Keep Watching"

from lib.still_watching import _how_many
for n in (1, 2, 5):
    print(f"  {n} -> {_how_many(n)}")
assert "1 episodes" not in _how_many(1), "reads as broken English on the first one"
assert _how_many(3).startswith("3 episodes")


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
