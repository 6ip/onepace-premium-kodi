"""Waiting for someone to finish setup, without hammering the server for it."""
import sys
import harness

harness.setup()
sys.path.insert(0, str(harness.ADDON / "lib"))

from setup_dialog import poll_interval

DIALOG = (harness.ADDON / "lib" / "setup_dialog.py").read_text(encoding="utf-8")
SETTINGS = (harness.ADDON / "lib" / "custom_settings_window.py").read_text(encoding="utf-8")
TTL = 300


def requests_for(finished_at, interval):
    """How many times we ask, if setup completes this many seconds in."""
    waited, asked = 0.0, 0
    while waited < finished_at:
        waited += interval(waited)
        asked += 1
    return asked


print("=== a code nobody claims ===")
flat = requests_for(TTL, lambda _: 3)
now = requests_for(TTL, poll_interval)
print(f"  flat 3s : {flat} requests")
print(f"  backoff : {now} requests")
assert now < flat / 2, f"{now} is not much better than {flat}"

print()
print("=== the people who actually finish are answered sooner ===")
for done in (5, 15, 30):
    assert poll_interval(done - 1) <= 3, f"slower than before at {done}s in"
    print(f"  finished at {done:>2}s -> next check in {poll_interval(done - 1)}s, was 3s")
# And the tail, where nobody is watching, is where the saving comes from.
assert poll_interval(200) == 10, poll_interval(200)
print(f"  still waiting at 200s -> next check in {poll_interval(200)}s")

print()
print("=== the schedule only ever slows down ===")
seen = [poll_interval(t) for t in range(0, TTL, 5)]
assert seen == sorted(seen), seen
print(f"  {sorted(set(seen))} seconds, in that order  OK")

print()
print("=== a code the server has dropped is not polled again ===")
# 404 means the key is gone from Redis. Waiting for it cannot ever pay off,
# and it used to be swallowed and retried until the client's own clock ran out.
loop = DIALOG[DIALOG.index("def _poll_loop("):]
assert "resp.status_code == 404" in loop and "break" in loop,     "the dialog keeps asking for a code that no longer exists"
assert "status_code == 404" in SETTINGS and "# Gone from the server" in SETTINGS,     "the wait that outlives the dialog keeps asking for a code that is gone"
print("  both loops stop on 404  OK")

print()
print("=== the deadline belongs to the server, not to whenever we started ===")
# It used to be time.time() + expires_in, recomputed when the second loop
# began — so closing the dialog after a minute bought five more minutes of
# polling, the last minute of it against an expired code.
assert 'deadline = pending.get("expires_at")' in SETTINGS,     "the second wait invents its own deadline again"
assert '"started_at": time.time()' in SETTINGS,     "without this the second wait restarts the schedule from the beginning"
for src, who in ((DIALOG, "the dialog"), (SETTINGS, "the wait after it")):
    assert 'expires_in"' in src and "min(deadline" in src,         f"{who} ignores the expiry the server reports"
print("  taken from the pending record, and corrected by the server  OK")

print()
print("all assertions passed")
