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
cp = cp[:cp.index("Container.Refresh")]
print("  Mark Unwatched -> no tick, no resume")
print("  Clear Progress -> keeps the tick, drops the resume")
assert "_watched." not in cp, "Clear Progress must not touch watched state"

print()
print("=== replaying a watched episode must not un-tick it ===")
end = PLAY[PLAY.index("        if marked:\n            _clear_kodi_episode_state"):]
end = end[:end.index("_update_kodi_episode_playcount(episode_id, 0)") + 44]
guard = next(l.strip() for l in end.split("\n") if l.strip().startswith("elif"))
print(f"  {guard}")
assert "get_watched" in guard and "not in" in guard


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
lst = SRC[SRC.index("        if stream_video_id in series_watched:"):]
lst = lst[:lst.index("n_resume += 1") + 14]
assert "_bookmarks.get" in lst.split("else:")[1], "the lookup must sit in the else branch"
print("  the bookmark lookup sits in the else branch  OK")

print()
print("=== autoplay carries the resume point itself ===")
assert 'PlayMedia({play_next_url},noresume)' in PLAY, "Kodi would prompt as well"
assert 'list_item.setProperty("StartPercent"' in PLAY, "nothing would position playback"
bm = next(l.strip() for l in PLAY.split("\n") if l.strip().startswith("bm ="))
print(f"  {bm}")
assert "autoplay" in bm, "the from-beginning check would misfire on autoplay"

print()
print("all assertions passed")
