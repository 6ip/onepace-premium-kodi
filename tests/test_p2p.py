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


_FOUND = '{"jsonrpc":"2.0","id":1,"result":{"addon":{"enabled":false}}}'


def ask(answers, acked=False, elementum=True, silent=False, installed=False):
    rec = harness.recorder()
    SEEN.clear()
    D.yesno_answers = list(answers)
    S["p2p_ack"] = "true" if acked else "false"
    ELEMENTUM[0] = elementum
    # Installed-but-disabled is the one Kodi still answers for.
    if installed:
        rec.jsonrpc_reply = _FOUND
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
# Switched off is one button away from fixed; never installed is a trip to the
# repository. InstallAddon is deliberately not used for the second: it gives up
# silently on anything no installed repository already carries.
for label, installed, want in [("switched off", True, "EnableAddon"),
                               ("never installed", False, "addonbrowser")]:
    got, rec, left = ask([1], elementum=False, installed=installed)
    fired = [b for b in rec.builtins if want in b]
    print(f"  {label:<15} -> {got}, fired: {fired}")
    assert got is False and fired, f"{label} needs {want}"
    assert "Elementum" in SEEN[0][1], "say what is missing, not just 'peer-to-peer'"

# Our own repository, which carries the ElementumOrg one: Kodi installs
# nothing it cannot already see, so that is where the trip has to start.
assert utils.ONEPACE_REPO_ID in fired[0], fired
assert "InstallAddon" not in fired[0], "that is the call that fails quietly"
print(f"  and it opens {utils.ONEPACE_REPO_ID} in the add-on browser")

got, rec, left = ask([2], elementum=False)
print(f"  'Configure' instead -> {got}, settings opened: "
      f"{bool([b for b in rec.builtins if 'OpenSettings' in b])}")
assert got is False and [b for b in rec.builtins if "OpenSettings" in b]

print()
print("=== a disabled add-on is not a missing one ===")
for label, enabled, reply, want in [
        ("installed and on", True,  None,   "ready"),
        ("installed, off",   False, _FOUND, "disabled"),
        ("never installed",  False, None,   "missing")]:
    rec = harness.recorder()
    ELEMENTUM[0] = enabled
    if reply:
        rec.jsonrpc_reply = reply
    got = utils.elementum_state()
    print(f"  {label:<17} -> {got!r}")
    assert got == want, (label, got)

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
print("=== a magnet is not a file to fetch, and the reason is worth saying ===")
# "No stream here can be downloaded" is true but sounds like a glitch to the
# one viewer who can never download anything.
er.ensure_p2p_ready = lambda silent=False: True
er.is_elementum_installed_and_enabled = lambda: True
er._cache.get = lambda url: {"streams": [
    {"name": "One Pace", "description": "1080p", "infoHash": "abc123",
     "sources": [], "behaviorHints": {}}]}



def refused(**settings):
    """What Download says when it can find nothing to fetch."""
    for key, value in settings.items():
        S[key] = value
    rec = harness.recorder()
    got = er._choose_stream({"catalog_type": "series", "video_id": "pp_onepacee"},
                            downloadable_only=True)
    assert got is None, got
    return rec.notifications[0][0] if rec.notifications else None


# Asked of the streams, not of the key: a peer-to-peer configuration has a key
# and still has no debrid service, so it used to hear the wrong one of these.
for label, secret in [("no key at all", ""), ("a p2p-only key", "p2p")]:
    said = refused(secret_string=secret)
    print(f"  {label:<15} -> {said!r}")
    assert said == "Downloads need a debrid service", (label, said)

# A debrid stream does exist here; the version preference is what ruled it out,
# so the episode is the problem rather than the service.
er._cache.get = lambda url: {"streams": [
    {"name": "Real-Debrid", "description": "1080p", "url": "https://dl/x.mkv",
     "behaviorHints": {"bingeGroup": "onepace|rd|standard"}},
    {"name": "P2P", "description": "1080p", "infoHash": "abc123", "sources": [],
     "behaviorHints": {"bingeGroup": "onepace|p2p|extended"}}]}
said = refused(secret_string="rdkey=X", preferred_version="2")
print(f"  {'extended, p2p only':<15} -> {said!r}")
assert said == "No stream here can be downloaded", said

S["secret_string"], S["preferred_version"] = "", "0"

print()
print("all assertions passed")
