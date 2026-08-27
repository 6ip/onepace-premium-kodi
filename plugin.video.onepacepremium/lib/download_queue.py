"""One transfer at a time, so a second Download waits instead of racing.

The queue lives in Kodi's window properties rather than a file. Every plugin
invocation can read them, they cost nothing to touch from inside a chunk loop,
and they go when Kodi does — so a session that was killed mid-download cannot
leave a lock behind that blocks the next one.
"""
import json
import time
import uuid

import xbmc
import xbmcgui

from .utils import log

_HOME = 10000
_HOLDER = "onepace.dl.holder"
_WAITING = "onepace.dl.waiting"
_CANCEL = "onepace.dl.cancel"

# A holder that stopped beating this long ago died without tidying up.
_STALE = 90
# Two invocations can both read an empty slot, so the winner is whoever the
# property still names a moment later.
_SETTLE = 0.15

_last_beat = (0, -1)


def _read(key, empty):
    raw = xbmcgui.Window(_HOME).getProperty(key)
    if not raw:
        return empty
    try:
        return json.loads(raw)
    except ValueError:
        return empty


def _write(key, value):
    xbmcgui.Window(_HOME).setProperty(key, json.dumps(value))


def holder():
    """The transfer running right now, or {} when the slot is free."""
    current = _read(_HOLDER, {})
    if current and time.time() - current.get("beat", 0) > _STALE:
        return {}
    return current


def waiting():
    return _read(_WAITING, [])


def busy():
    return bool(holder()) or bool(waiting())


def _leave(ticket):
    _write(_WAITING, [w for w in waiting() if w.get("id") != ticket])
    _write(_CANCEL, [x for x in _read(_CANCEL, []) if x != ticket])


def acquire(label, progress=None, total=1):
    """Take the download slot, waiting behind anyone already in line.

    A season passes its episode count as total, so the row can say what
    cancelling would actually stop.

    Returns a ticket to pass back to beat() and release(), or None if the
    wait was cancelled.
    """
    ticket = uuid.uuid4().hex[:8]
    monitor = xbmc.Monitor()
    while True:
        line = waiting()
        if ticket not in [w.get("id") for w in line]:
            line.append({"id": ticket, "label": label})
            _write(_WAITING, line)
            continue
        if not holder() and line[0].get("id") == ticket:
            _write(_HOLDER, {"id": ticket, "label": label, "pct": 0,
                             "total": total, "index": 1, "now": "",
                             "beat": time.time()})
            monitor.waitForAbort(_SETTLE)
            if _read(_HOLDER, {}).get("id") == ticket:
                _leave(ticket)
                return ticket
            continue
        if cancelled(ticket):
            _leave(ticket)
            log(f"[queue] {label!r} was cancelled before it started")
            return None
        ahead = [w.get("id") for w in line].index(ticket) + 1
        if progress:
            progress.update(0, "Waiting to download",
                            f"{ahead} ahead — {label}")
        if monitor.waitForAbort(1):
            _leave(ticket)
            return None


def snapshot():
    """Rows for the manager: the transfer running, then whatever waits."""
    rows = []
    running = holder()
    if running:
        total = running.get("total") or 1
        detail = running.get("now") or ""
        if total > 1:
            # A season: say which episode, and how far through the run it is.
            place = f"{running.get('index') or 1} of {total}"
            detail = f"{place}  ·  {detail}" if detail else place
        rows.append({"ticket": running.get("id", ""),
                     "name": running.get("label", ""), "detail": detail,
                     "pct": running.get("pct") or 0, "status": "Downloading"})
    for entry in waiting():
        rows.append({"ticket": entry.get("id", ""), "name": entry.get("label", ""),
                     "detail": "", "pct": 0, "status": "Waiting"})
    return rows


def beat(ticket, pct):
    """Keep the slot alive and say how far along it is."""
    global _last_beat
    now = time.time()
    if pct == _last_beat[1] and now - _last_beat[0] < 5:
        return
    current = _read(_HOLDER, {})
    if current.get("id") != ticket:
        return
    current.update(pct=pct, beat=now)
    _write(_HOLDER, current)
    _last_beat = (now, pct)


def on_item(ticket, name, index):
    """A season keeps one slot across many episodes; this says which one.

    The season's own label stays put, so the row still shows what a cancel
    would stop rather than only the episode in flight.
    """
    current = _read(_HOLDER, {})
    if current.get("id") == ticket:
        current.update(now=name, index=index, pct=0, beat=time.time())
        _write(_HOLDER, current)


def release(ticket):
    if _read(_HOLDER, {}).get("id") == ticket:
        xbmcgui.Window(_HOME).clearProperty(_HOLDER)
    _leave(ticket)


def cancelled(ticket):
    """Whether this transfer was asked to stop.

    It stays true until the job ends, so a season can tell a cancel apart
    from an episode that was merely skipped.
    """
    return ticket in _read(_CANCEL, [])


def request_cancel(tickets):
    _write(_CANCEL, sorted(set(_read(_CANCEL, [])) | set(tickets)))
    log(f"[queue] asked {len(tickets)} transfer(s) to stop")
