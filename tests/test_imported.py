"""Files we only adopted: ours to list, never ours to delete, move or forget.

An imported episode is the viewer's own copy, in their own folder, often on a
drive that comes and goes. Every habit the download side has — replace the old
cut, gather everything into one folder, forget a row whose file is gone — is
wrong for one of these, and wrong in a way that costs somebody a library.
"""
import harness

store = harness.setup({"downloads_enabled": "true"})

import kodistub
from lib import downloads

SRC = (harness.ADDON / "lib" / "downloads.py").read_text(encoding="utf-8")

# Where a download of ours would land, given the folder patched in below.
OURS = "C:/kodi/downloads/One Pace/Season 01/1x01 - Episode.mkv"
THEIRS = "E:/Anime/[One Pace][1] Romance Dawn 01 [1080p][E5F09F49].mkv"

_ROW = {"episode_id": "RO_1", "series_name": "One Pace", "variant": "",
        "season": "1", "episode": "1", "subtitles": []}

downloads.folder = lambda: "C:/kodi/downloads/"
downloads._prune = lambda path: None
downloads.xbmcvfs.rmdir = lambda path: True

present, deleted, written = set(), [], []
downloads.xbmcvfs.exists = lambda path: path in present
downloads.xbmcvfs.delete = lambda path: (deleted.append(path), True)[1]
downloads.read_index = lambda: written[-1]
downloads._write_index = lambda data: written.append(data)

# The question a dialog asked, so a prompt cannot promise the wrong thing.
asked = []
_yesno = kodistub.Dialog.yesno
kodistub.Dialog.yesno = lambda self, heading, message, **k: (
    asked.append((message, k)), _yesno(self, heading, message, **k))[1]

D = harness.dialog()


def fresh(ours=True, theirs=True, on_disk=(OURS, THEIRS)):
    files = {}
    if ours:
        files[OURS] = dict(_ROW)
    if theirs:
        files[THEIRS] = dict(_ROW, imported=True)
    written.clear()
    written.append({"files": files, "series": {"One Pace": {}}})
    present.clear()
    present.update(on_disk)
    deleted.clear()
    asked.clear()
    return harness.recorder()


print("=== deleting forgets a borrowed file, and keeps it ===")
fresh()
D.yesno_answers = [True]
downloads.delete({"path": THEIRS})
print(f"  rows left: {list(written[-1]['files'])!r}")
print(f"  files deleted: {deleted}")
assert THEIRS not in written[-1]["files"], "the row should go"
assert deleted == [], "an imported file is not ours to delete"
assert OURS in written[-1]["files"], "the other row is untouched"

print()
print("=== one of ours is still deleted outright ===")
fresh()
D.yesno_answers = [True]
downloads.delete({"path": OURS})
print(f"  files deleted: {deleted}")
assert deleted == [OURS], "a file we fetched should go from disk"

print()
print("=== the prompt never promises what it will not do ===")
fresh()
D.yesno_answers = [True]
downloads.delete({"path": THEIRS})
message, kwargs = asked[0]
print(f"  all imported -> {message.splitlines()[-1]!r}, button {kwargs.get('yeslabel')!r}")
assert "stay where they are" in message and kwargs.get("yeslabel") == "Remove"
assert "from disk" not in message, "it deletes nothing, so it must not say disk"

fresh()
D.yesno_answers = [True]
downloads.delete({"series": "One Pace"})
message, kwargs = asked[0]
print(f"  mixed        -> {message.splitlines()[-1]!r}")
assert "1 will be deleted" in message and "1 imported" in message
assert deleted == [OURS], deleted

print()
print("=== gathering everything into one folder leaves theirs alone ===")
rec = fresh()
downloads.download_queue.busy = lambda: False
downloads.move_downloads()
print(f"  said: {rec.notifications[0][0]!r}")
assert "already in the download folder" in rec.notifications[0][0], rec.notifications
assert deleted == [] and THEIRS in written[-1]["files"]

print()
print("=== an unplugged drive is not a deleted file ===")
# Only our own file is on disk; theirs is on a drive that is not here.
fresh(on_disk=(OURS,))
downloads._sweep(written[-1])
print(f"  rows left: {sorted(written[-1]['files'])!r}")
assert THEIRS in written[-1]["files"], "an unplugged drive would cost them everything"

fresh(on_disk=(THEIRS,))
downloads._sweep(written[-1])
print(f"  ours gone from disk -> rows left: {sorted(written[-1]['files'])!r}")
assert OURS not in written[-1]["files"], "a file of ours that vanished is stale"

print()
print("=== nothing imported is offered a re-download ===")
fresh(ours=False)
rec = harness.recorder()
downloads.check_updates()
print(f"  only imported rows -> {rec.notifications[0][0]!r}")
assert "Nothing downloaded yet" in rec.notifications[0][0]

print()
print("=== a finished download never deletes the copy it was compared against ===")
swap = SRC[SRC.index("    # Now the new file is in place"):]
swap = swap[:swap.index("_remember(")]
print(f"  guarded by: {[l.strip() for l in swap.splitlines() if 'imported' in l]}")
assert "not imported(already)" in swap, "replacing would delete their own file"

print()
print("=== the flag is off unless the importer sets it ===")
fresh()
downloads.save_subtitles = lambda destination, params: ()
downloads._remember(OURS, dict(_ROW, series_name="One Pace"))
assert written[-1]["files"][OURS]["imported"] is False
downloads._remember(THEIRS, dict(_ROW, series_name="One Pace", imported=True))
assert written[-1]["files"][THEIRS]["imported"] is True
print("  a download writes False, an import writes True  OK")

print()
print("all assertions passed")
