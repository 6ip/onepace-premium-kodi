"""Marking, unmarking, replaying — and keeping our store and Kodi's in step."""
import re
import harness

harness.setup()
SRC = (harness.ADDON / "lib" / "episode_routes.py").read_text(encoding="utf-8")
PLAY = (harness.ADDON / "lib" / "playback.py").read_text(encoding="utf-8")

print("=== marking and unmarking are symmetric ===")
blk = SRC[SRC.index('    if scope == "episode":'):SRC.index("    else:\n        catalog_type")]
print("  single episode:")
for l in blk.rstrip().split("\n")[-3:]:
    print("    " + l.strip())
assert 'if action == "marked":' not in blk, "clearing the resume point is still one-way"
assert "_bookmarks.clear(episode_id)" in blk

bulk = SRC[SRC.index("            marking_watched = not all_watched_before"):]
bulk = bulk[:bulk.index("_bulk_kodi_update")]
assert "if marking_watched:" not in bulk, "still one-way at season scope"
print("  season / series: same both ways")

print()
print("=== the two context actions mean different things ===")
cp = SRC[SRC.index("def clear_progress"):]
cp = cp[:cp.index("refresh_container()")]
print("  Mark Unwatched -> no tick, no resume")
print("  Clear Progress -> keeps the tick, drops the resume")
assert "_watched." not in cp, "Clear Progress must not touch watched state"

print()
print("=== replaying a watched episode leaves it watched, with no resume ===")
end = PLAY[PLAY.index("        if marked or was_watched:"):]
end = end[:end.index("_update_kodi_episode_playcount(episode_id, 0)")]
assert "_clear_kodi_episode_state(episode_id)" in end, "Kodi keeps drawing a resume bar"
assert "_update_kodi_episode_playcount(episode_id, 1)" in end, "replaying would un-tick it"
print("  Kodi resume point cleared, playcount kept at 1")

# Our own store must not gain a resume point for something already finished.
mid = PLAY[PLAY.index("was_watched = "):PLAY.index("elif last_time > 60")]
assert "elif was_watched:" in mid, "a watched episode would collect a bookmark no menu can reach"
assert "_bookmarks.clear(episode_id)" in mid, "a watched episode would collect a bookmark no menu can reach"
print("  no orphan bookmark saved either")


def decide(threshold_hit, already_watched):
    if threshold_hit:
        return "playCount=1"
    return "left alone" if already_watched else "playCount=0"


for hit, seen, label in [(True, False, "watched through, was unwatched"),
                         (False, False, "stopped early, was unwatched"),
                         (False, True, "stopped early, already watched")]:
    print(f"  {label:<34} -> {decide(hit, seen)}")
assert decide(False, True) == "left alone"
assert decide(False, False) == "playCount=0"

print()
print("=== a watched episode never shows a resume bar ===")
lst = SRC[SRC.index("        bm = _bookmarks.get(stream_video_id)"):]
lst = lst[:lst.index("n_resume += 1") + 14]
assert "setResumePoint" in lst.split("else:")[1], "a watched episode would draw a bar"
assert "setResumePoint" not in lst.split("else:")[0], "the tick branch must not resume"
print("  setResumePoint only runs on the unwatched branch  OK")

# A stale bookmark from an older version has to stay reachable to clear it.
assert lst.index("_bookmarks.get") < lst.index("in series_watched"), "Clear Progress hidden"
print("  a stale bookmark is still looked up, so Clear Progress appears  OK")

print()
print("=== autoplay carries the resume point itself ===")
assert 'PlayMedia({play_next_url},noresume)' in PLAY, "Kodi would prompt as well"
assert 'list_item.setProperty("StartPercent"' in PLAY, "nothing would position playback"
bm = next(l.strip() for l in PLAY.split("\n") if l.strip().startswith("bm ="))
print(f"  {bm}")
assert "autoplay" in bm, "the from-beginning check would misfire on autoplay"

print()
print("=== refreshing from a widget must not touch the skin's container ===")
from lib.utils import refresh_container
from kodistub import recorder

for plugin_name, expected in (
        ("plugin.video.onepacepremium", ["UpdateLibrary(video,special://skin/foo)", "Container.Refresh"]),
        ("", ["UpdateLibrary(video,special://skin/foo)"]),
        ("skin.arctic.fuse.3", ["UpdateLibrary(video,special://skin/foo)"])):
    recorder.reset()
    recorder.infolabels["Container.PluginName"] = plugin_name
    refresh_container()
    where = "our list" if plugin_name == "plugin.video.onepacepremium" else "a widget"
    print(f"  {where:9}  ->  {' + '.join(b.split('(')[0] for b in recorder.builtins)}")
    assert recorder.builtins == expected, recorder.builtins

for name in ("mark_watched", "clear_progress"):
    fn = SRC[SRC.index(f"def {name}("):]
    fn = fn[:fn.index("refresh_container()")]
    assert 'executebuiltin("Container.Refresh")' not in fn, f"{name} still refreshes blind"
print("  mark_watched and clear_progress both go through it  OK")

# Stopping an episode moves the resume point, so shelves are stale too.
mon = PLAY[PLAY.index("def _monitor_playback"):]
assert "ping_widgets()" in mon, "shelves stay stale after playback"
assert mon.index("ping_widgets()") < mon.index('executebuiltin("Container.Refresh")'),     "the shelves are nudged before our own container redraws"
handoff = "if not play_next_url:" + chr(10) + "            # The resume point moved"
assert handoff in mon, "autoplay would ping between every episode"
print("  stopping an episode nudges the shelves too, but a handoff does not")

# A widget's FolderPath is our plugin URL, so that test alone lets the
# post-playback redraw fire straight at the skin's container.
guard = next(l.strip() for l in PLAY.split(chr(10)) if "ADDON_ID in path and" in l)
print(f"  {guard}")
assert "not is_widget()" in guard, "post-playback still redraws widgets"


print()
print("=== queued subtitle fetches do not outlive the call ===")
subs = PLAY[PLAY.index("pool = futures.ThreadPoolExecutor"):]
assert "cancel_futures=True" in subs[:subs.index(chr(10) * 3)], "left running"
print("  cancelled, not left running")

print()
print("all assertions passed")
