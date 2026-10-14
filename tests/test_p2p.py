"""Playing with no debrid key: the consent question, and who must not be asked."""
import harness

S = harness.setup({"base_url": "https://onepace.e6ip.com", "secret_string": "",
                   "p2p_ack": "false"})

import kodistub
from lib import episode_routes as er, utils

D = harness.dialog()

# Elementum is what plays a torrent, so every case says whether it is there
# before saying what the viewer answered.
ELEMENTUM = [True]
utils.is_elementum_installed_and_enabled = lambda: ELEMENTUM[0]

# The three buttons arrive as customlabel, nolabel, yeslabel and come back as
# 2, 0, 1 — a pairing worth holding still.
SEEN = []
_orig = kodistub.Dialog.yesnocustom
kodistub.Dialog.yesnocustom = lambda self, *a, **k: (
    SEEN.append(a), _orig(self, *a, **k))[1]


def ask(answers, acked=False, elementum=True, silent=False):
    rec = harness.recorder()
    SEEN.clear()
    D.yesno_answers = list(answers)
    S["p2p_ack"] = "true" if acked else "false"
    ELEMENTUM[0] = elementum
    got = utils.ensure_p2p_ready(silent=silent)
    return got, rec, D.yesno_answers


print("=== the question is only ever asked once ===")
got, rec, left = ask([1], acked=True)
print(f"  already agreed      -> {got}, dialogs shown: {len(SEEN)}")
assert got is True and left == [1], "an agreed viewer must not be asked again"

got, rec, left = ask([1])
print(f"  'Play anyway'       -> {got}, p2p_ack={S['p2p_ack']!r}")
assert got is True and S["p2p_ack"] == "true", "consent must be remembered"

print()
print("=== the buttons sit in the slots their return values mean ===")
heading, message, custom, no, yes = SEEN[0]
print(f"  custom={custom!r} no={no!r} yes={yes!r}")
assert (custom, no, yes) == ("Configure", "Not now", "Play anyway")
assert "peer-to-peer" in message, "the question has to name what it is asking"

print()
print("=== Configure sends them to settings and plays nothing ===")
got, rec, left = ask([2])
opened = [b for b in rec.builtins if "OpenSettings" in b]
print(f"  -> {got}, builtins: {opened}")
assert got is False and opened and S["p2p_ack"] != "true"

print()
print("=== every way of saying no leaves it for later ===")
# The dialog does not reopen in place: a button saying "Not now" that does not
# let you not now reads as a click that failed to register. Nothing is stored,
# so clicking the next episode asks again.
for label, answers in [("Not now", [0, 1]), ("Back or Esc", [-1, 1])]:
    got, rec, left = ask(answers)
    print(f"  {label:<12} -> {got} after {len(SEEN)} dialog, "
          f"p2p_ack={S['p2p_ack']!r}")
    assert got is False and len(SEEN) == 1, "it must not come round again"
    assert left == [1] and S["p2p_ack"] != "true"

print()
print("=== without Elementum there is nothing to play a torrent ===")
got, rec, left = ask([1], elementum=False)
ran = [b for b in rec.builtins if "setup_elementum" in b]
print(f"  'Find Elementum'    -> {got}, builtins: {len(ran)}")
assert got is False and ran, "it should offer to go and look for Elementum"
assert "Elementum" in SEEN[0][1], "say what is missing, not just 'peer-to-peer'"

got, rec, left = ask([2], elementum=False)
print(f"  'Configure' instead -> {got}, settings opened: "
      f"{bool([b for b in rec.builtins if 'OpenSettings' in b])}")
assert got is False and [b for b in rec.builtins if "OpenSettings" in b]

print()
print("=== the silent paths never raise a dialog ===")
for acked, elementum, expect in [(True, True, True), (False, True, False),
                                 (True, False, True)]:
    got, rec, left = ask([1], acked=acked, elementum=elementum, silent=True)
    print(f"  agreed={str(acked):<5} elementum={str(elementum):<5} -> {got}, "
          f"dialogs: {len(SEEN)}")
    assert got is expect and left == [1] and not SEEN

print()
print("=== only a row someone pressed play on gets asked ===")
SILENT = []
er.ensure_p2p_ready = lambda silent=False: SILENT.append(silent) or False
base = {"catalog_type": "series", "video_id": "pp_onepacee"}
cases = [
    ("a row someone pressed play on", {},               {},                False),
    ("a season queue",                {"quiet": True},  {},                True),
    ("the preference survey",         {"survey": True}, {},                True),
    ("the next-episode hand-off",     {},               {"autoplay": "1"}, True),
]
for label, kwargs, extra, want in cases:
    SILENT.clear()
    assert er._choose_stream({**base, **extra}, **kwargs) is None
    print(f"  {label:<30} silent={SILENT[0]}")
    assert SILENT == [want], (label, SILENT)

print()
print("all assertions passed")
