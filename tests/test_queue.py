"""One transfer at a time: taking the slot, waiting behind it, cancelling."""
import re
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


def queue_up(*entries):
    """Put waiters in the line the way a real one arrives: beating."""
    for entry in entries:
        q._wait_beat(entry["id"])
    q._write(q._WAITING, list(entries))


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
print("=== cancel all: a waiter must never start, then stop ===")
fresh()
first = q.acquire("1x01")
# Two behind it, both cancelled while the first is still going.
queue_up({"id": "second", "label": "1x02"}, {"id": "third", "label": "1x03"})
q.request_cancel([first, "second", "third"])
q.release(first)

# "second" now reaches the front of an empty queue. Claiming before checking
# the cancel is what made it download the whole file and only then stop.
started = []
for who in ("second", "third"):
    line = [w for w in q.waiting() if w["id"] != who]
    queue_up(*([{"id": who, "label": who}] + line))
    if q.cancelled(who):
        q._leave(who)
    else:
        started.append(who)
print(f"  started anyway: {started or 'none'}")
assert not started, "a cancelled transfer still took the slot"
assert q.waiting() == [], q.waiting()

src = (harness.ADDON / "lib" / "download_queue.py").read_text(encoding="utf-8")
body = src[src.index("def acquire("):src.index("def beat(")]
assert body.index("if cancelled(ticket):") < body.index("_write(_HOLDER"),     "the slot is claimed before the cancel is noticed, so the download begins"
print("  the cancel is read before the slot is taken  OK")

print()
print("=== a row nobody is waiting on cannot survive Cancel All ===")
fresh()
running = q.acquire("1x01")
queue_up({"id": "b", "label": "1x02"}, {"id": "c", "label": "1x03"})

# Two waiters leaving at once: each writes a list it read a moment earlier, so
# the second write puts the first one back. That row belongs to no invocation,
# and pressing Cancel on it does nothing — which is what was seen.
before = q.waiting()
q._leave("b")
q._write(q._WAITING, [w for w in before if w.get("id") != "c"])   # the stale write
listed = q._read(q._WAITING, [])
print(f"  the stale write put back: {[w['label'] for w in listed]}")
assert [w["id"] for w in listed] == ["b"], "the race did not happen"
print(f"  but the queue reads:      {[w['label'] for w in q.waiting()]}")
assert q.waiting() == [], "the resurrected row is listed, and nothing can cancel it"

# And a waiter that dies without ever leaving stops being counted too.
fresh()
running = q.acquire("1x01")
queue_up({"id": "d", "label": "1x04"})
assert len(q.waiting()) == 1
q.xbmcgui.Window(q._HOME).setProperty(
    q._wait_key("d"), str(time.time() - q._WAIT_STALE - 1))
print(f"  after it stops beating:   {[w['label'] for w in q.waiting()]}")
assert q.waiting() == [], "a waiter that died still holds up the queue"
assert not q.busy() or q.holder(), "busy should now be the holder alone"
q.release(running)
print("  a row with nobody behind it drops out on its own  OK")

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
queue_up({"id": "later", "label": "6x02 Arlong Park 02"})
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

# One episode on its own must not be dressed up as a run of many, but its
# filename does not say which series it is, so the band would sit half empty.
fresh()
q.acquire("1x01 Romance Dawn", detail="One Pace")
solo = q.snapshot()[0]
print(f"  {solo['name']}  |  {solo['detail']}")
assert solo["detail"] == "One Pace", solo
assert "of" not in solo["detail"], "a single download read like a run of many"

# And if there is no series to name, the skin closes the gap itself.
assert 'condition="String.IsEmpty(Window(Home).Property(pp.dl.detail))"' in (
    harness.ADDON / "resources" / "skins" / "Default" / "1080i"
    / "downloads_manager.xml").read_text(encoding="utf-8"),     "an empty detail line would leave the name stranded at the top"
print("  a single episode names its series instead  OK")

print()
print("=== the manager leaves the moving parts to the skin ===")
MSRC = (harness.ADDON / "lib" / "downloads_manager.py").read_text(encoding="utf-8")
# A control may only be touched from the thread Kodi calls us on. Doing it
# from a timer thread corrupts the render, so there is no timer at all.
assert "threading" not in MSRC, "a background thread must not touch a control"
assert "setPercent(" not in MSRC, "driving the bar from python needs a thread"
assert "def _sync(" in MSRC, "nothing would ever catch the list up"

skin = (harness.ADDON / "resources" / "skins" / "Default" / "1080i"
        / "downloads_manager.xml").read_text(encoding="utf-8")
assert 'type="progress"' not in skin, "a progress control can only be driven from python"
steps = skin.count("Integer.IsGreaterOrEqual(Window(Home).Property(pp.dl.percent)")
assert steps >= 10, f"only {steps} steps, the bar would jump"
print(f"  the bar is {steps} skin-drawn steps, no control touched from python  OK")

# What the skin reads, the queue has to write.
reads = set(re.findall(r"Window\(Home\)\.Property\((pp\.dl\.[a-z]+)\)", skin))
writes = set(re.findall(r'setProperty\("(pp\.dl\.[a-z]+)"', SRC))
print(f"  skin reads {sorted(reads)}")
assert reads <= writes, f"the skin reads what nothing sets: {sorted(reads - writes)}"

print()
print("all assertions passed")
