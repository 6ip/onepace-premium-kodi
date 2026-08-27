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
print("=== the rows the Downloads section draws ===")
fresh()
from lib import downloads
assert downloads._active_rows() == [], "an idle add-on should draw no busy rows"
alpha = q.acquire("6x01 Arlong Park 01")
q.beat(alpha, 42)
q._write(q._WAITING, [{"id": "later", "label": "6x02 Arlong Park 02"}])
rows = downloads._active_rows()
labels = [r[1].label for r in rows]
print("  " + chr(10) + "  ".join(labels))
assert "(42%)" in labels[0] and "(waiting)" in labels[1]
assert all(is_folder for _, _, is_folder in rows),     "a non-folder row would have Kodi try to play the download in progress"
assert all("action=list_downloads" in url for url, _, _ in rows),     "selecting a row should redraw the list, not fire an action"
menu = rows[0][1].context
assert any("Cancel All" in text for text, _ in menu), "two in flight, no way to stop both"
print("  folder rows, cancel on the menu  OK")

print()
print("=== a season row says what cancelling it would stop ===")
fresh()
job = q.acquire("Season 6", total=12)
q.on_item(job, "6x03 Arlong Park 03", 3)
q.beat(job, 55)
row = downloads._active_rows()[0]
print(f"  {row[1].label}")
print(f"  menu: {[t for t, _ in row[1].context]}")
assert "Season 6" in row[1].label and "3 of 12" in row[1].label,     "the row only named the episode, so a cancel looked like it stopped one"
assert "6x03 Arlong Park 03" in row[1].getVideoInfoTag().calls["setPlot"],     "the plot never named the episode actually in flight"
assert any("Cancel Season 6" in text for text, _ in row[1].context),     'the menu said only "Cancel" for a job covering twelve episodes'

# One episode on its own must not be dressed up as a season.
fresh()
q.acquire("1x01 Romance Dawn")
solo = downloads._active_rows()[0]
assert [t for t, _ in solo[1].context] == ["[B]Cancel[/B]"], solo[1].context
print("  a single episode still reads plainly  OK")

print()
print("all assertions passed")
