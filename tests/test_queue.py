"""One transfer at a time: taking the slot, waiting behind it, cancelling."""
import time
import harness

harness.setup()

import kodistub
import xbmc
from lib import download_queue as q

SRC = (harness.ADDON / "lib" / "download_queue.py").read_text(encoding="utf-8")


def fresh():
    kodistub._WINDOW_PROPS.clear()
    q._last_beat = (0, -1)


print("=== the first caller takes the slot without waiting ===")
fresh()
started = time.time()
one = q.acquire("1x01 Romance Dawn")
assert one, "nobody else was downloading"
assert time.time() - started < 1, "an idle queue made it wait"
assert q.holder()["label"] == "1x01 Romance Dawn"
assert q.waiting() == [], "it should not still be in the line it joined"
print(f"  took the slot in {(time.time() - started) * 1000:.0f} ms  OK")

print()
print("=== a second caller waits, and can be cancelled out of the line ===")
# waitForAbort returns at once in the stub, so the wait spins rather than
# sleeps; cancelling is what has to break it.
calls = {"n": 0}
real_wait = xbmc.Monitor.waitForAbort


def _wait(self, secs=0):
    calls["n"] += 1
    if calls["n"] == 3:
        q.request_cancel([q.waiting()[0]["id"]])
    assert calls["n"] < 50, "the wait never noticed the cancel"
    return False


xbmc.Monitor.waitForAbort = _wait
two = q.acquire("1x02 The Great Swordsman")
xbmc.Monitor.waitForAbort = real_wait
assert two is None, "it should not have taken a slot someone else holds"
assert q.holder()["label"] == "1x01 Romance Dawn", "the first one was displaced"
assert q.waiting() == [], "a cancelled waiter stayed in the line"
print(f"  waited {calls['n']} turns, then cancelled cleanly  OK")

print()
print("=== releasing hands the slot to whoever is next ===")
q.release(one)
assert q.holder() == {}, "the slot is still held"
assert not q.busy()
three = q.acquire("1x03 Morgan versus Luffy")
assert three and q.holder()["label"] == "1x03 Morgan versus Luffy"
print("  the next caller went straight through  OK")

print()
print("=== a cancel sticks until the job ends ===")
q.request_cancel([three])
assert q.cancelled(three), "the running transfer was not told"
assert q.cancelled(three), "asking twice cleared it, so a season cannot see it"
q.release(three)
assert not q.cancelled(three), "the flag outlived the job it belonged to"
print("  read twice, cleared on release  OK")

print()
print("=== a holder that stopped beating is stepped over ===")
fresh()
kodistub._WINDOW_PROPS.setdefault(10000, {})[q._HOLDER] = harness.json.dumps(
    {"id": "deadbeef", "label": "killed mid-download", "pct": 40,
     "beat": time.time() - q._STALE - 5})
assert q.holder() == {}, "a dead holder still blocks the slot"
four = q.acquire("1x04 Luffy's Past")
assert four, "nobody could ever download again after one crash"
print(f"  took over from a holder {q._STALE + 5}s silent  OK")

print()
print("=== the beat is throttled, not written per chunk ===")
fresh()
five = q.acquire("1x05")
writes = {"n": 0}
real = q._write


def _count(key, value):
    writes["n"] += 1
    real(key, value)


q._write = _count
for pct in [7] * 200:
    q.beat(five, pct)
q._write = real
assert writes["n"] == 1, f"the same percentage was written {writes['n']} times"
print("  200 chunks at 7% wrote once  OK")

print()
print("=== nothing here is kept on disk ===")
assert "xbmcvfs" not in SRC, "a file lock would outlive the session that made it"
assert "Window(_HOME)" in SRC
print("  the queue lives in window properties, so Kodi restarting clears it  OK")

print()
print("=== what the manager window is given to draw ===")
fresh()
alpha = q.acquire("6x01 Arlong Park 01")
q.beat(alpha, 42)
q._write(q._WAITING, [{"id": "later", "label": "6x02 Arlong Park 02"}])
rows = q.snapshot()
for row in rows:
    print(f"  {row['name']:<22} {row['pct']:>3}%  {row['status']}")
assert [r["status"] for r in rows] == ["Downloading", "Waiting"]
assert rows[0]["pct"] == 42 and rows[1]["pct"] == 0
assert [r["ticket"] for r in rows] == [alpha, "later"], "no ticket, nothing to cancel"

print()
print("=== a season row says what cancelling it would stop ===")
fresh()
job = q.acquire("Season 6", total=12)
q.on_item(job, "6x03 Arlong Park 03", 3)
q.beat(job, 55)
row = q.snapshot()[0]
print(f"  {row['name']}  |  {row['detail']}  |  {row['pct']}%")
assert row["name"] == "Season 6", "the episode overwrote the season it belongs to"
assert "3 of 12" in row["detail"] and "6x03 Arlong Park 03" in row["detail"]

# One episode on its own must not be dressed up as a run of many.
fresh()
q.acquire("1x01 Romance Dawn")
solo = q.snapshot()[0]
assert solo["detail"] == "", solo
print("  a single episode carries no run detail  OK")

print()
print("=== the window stops redrawing once the queue empties ===")
MSRC = (harness.ADDON / "lib" / "downloads_manager.py").read_text(encoding="utf-8")
watch = MSRC[MSRC.index("def _watch("):]
assert "while not self.closed and download_queue.busy():" in watch,     "an idle window would redraw forever, holding an interpreter open"
assert "waitForAbort" in watch, "a raw sleep ignores Kodi shutting down"
print("  the redraw loop ends when nothing is in flight  OK")

print()
print("all assertions passed")
