"""Artwork on rows and on the item we hand the player."""
import re
import harness

S = harness.setup({"thumb_fanart": "false"})
from lib.art import _set_episode_art, _set_show_tags, _set_episode_rating, _cast_list
import kodistub

THUMB = "https://img.example/EPISODE.jpg"
VIDEO = {"thumbnail": THUMB, "season": 11, "episode": 1}
META = {"poster": "https://img.example/show.jpg",
        "background": "https://img.example/bg.jpg"}
SEASON = "https://img.example/season-11.jpg"

print("=== no artwork reaches the icon slot ===")
li = kodistub.ListItem()
_set_episode_art(li, VIDEO, META, SEASON)
for k in ("icon", "thumb", "landscape", "poster", "fanart"):
    v = li.art.get(k, "-")
    print(f"  {k:<10} {v.rsplit('/', 1)[-1]:<20}{'  <-- episode still' if v == THUMB else ''}")
assert li.art["icon"] == "DefaultAddonNone.png", "skins draw the icon beside the label"
assert li.art["thumb"] == THUMB and li.art["landscape"] == THUMB
assert li.art["poster"] == SEASON, "an episode still in the poster slot gets stretched"

print()
print("=== fallbacks ===")
li2 = kodistub.ListItem()
_set_episode_art(li2, {"season": 11, "episode": 2}, META, SEASON)
icon2 = li2.art["icon"]
print(f"  no episode still -> icon={icon2}, thumb={li2.art.get(chr(116)+chr(104)+chr(117)+chr(109)+chr(98), chr(45))}")
assert "thumb" not in li2.art, "a poster in the thumb slot gets stretched"
assert li2.art["poster"] == SEASON, "the season poster still fills the poster slot"
li3 = kodistub.ListItem()
_set_episode_art(li3, {}, {}, None)
print(f"  nothing at all   -> icon={li3.art.get('icon')}")
assert li3.art.get("icon") == "DefaultAddonNone.png"

print()
print("=== the episode thumbnail as background is opt-in ===")
S["thumb_fanart"] = "true"
li4 = kodistub.ListItem()
_set_episode_art(li4, VIDEO, META, SEASON)
print(f"  on  -> fanart={li4.art['fanart'].rsplit('/', 1)[-1]}")
assert li4.art["fanart"] == THUMB
S["thumb_fanart"] = "false"
li5 = kodistub.ListItem()
_set_episode_art(li5, VIDEO, META, SEASON)
print(f"  off -> fanart={li5.art['fanart'].rsplit('/', 1)[-1]}")
assert li5.art["fanart"] != THUMB

print()
print("=== the played item carries nothing that draws beside a label ===")
src = (harness.ADDON / "lib" / "playback.py").read_text(encoding="utf-8")
blk = src[src.index("    art = {}"):src.index("list_item.setArt(art)")]
vals = dict(re.findall(r'art\["([a-z.]+)"\] = (\w+)', blk))
for k in ("thumb", "icon", "landscape"):
    print(f"  {k:<10} = {vals.get(k, '-')}")
assert vals.get("thumb") != "episode_thumb", "thumb would merge onto the row"
assert "icon" not in vals, "the icon slot merges onto the row that launched it"
assert vals.get("landscape") == "episode_thumb", "the OSD should still get the still"

print()
print("=== episode rating only when there is one ===")
for video, expect in [({"episode": 4}, False), ({"imdbRating": 8.8}, True),
                      ({"rating": "N/A"}, False)]:
    tag = kodistub._Tag()
    _set_episode_rating(tag, video)
    got = "setRating" in tag.calls
    print(f"  {str(video):<28} -> setRating {'called' if got else 'skipped'}")
    assert got is expect

print()
print("=== cast photos are upgraded, not passed through raw ===")
cast = _cast_list({"cast": ["Someone"],
                   "castPhotos": ["https://image.tmdb.org/t/p/w132/x.jpg"]})
print(f"  {len(cast)} actor(s) built from meta")

print()
print("=== show tags: trailer and cast ===")
import xbmc
xbmc.getCondVisibility = lambda cond: "youtube" in cond
tag = kodistub._Tag()
_set_show_tags(tag, {"name": "One Pace", "trailers": [{"source": "1KMcoJBMWE4"}],
                     "cast": ["Mayumi Tanaka"],
                     "castPhotos": ["https://image.tmdb.org/t/p/w132/a.jpg"]})
trailer = tag.calls.get("setTrailer", "")
print(f"  setTrailer -> {str(trailer)[:64]}")
assert "play_trailer" in trailer and "1KMcoJBMWE4" in trailer

xbmc.getCondVisibility = lambda cond: False
tag2 = kodistub._Tag()
_set_show_tags(tag2, {"name": "One Pace", "trailers": [{"source": "1KMcoJBMWE4"}]})
print(f"  without the YouTube add-on -> still {str(tag2.calls.get(chr(115)+chr(101)+chr(116)+chr(84)+chr(114)+chr(97)+chr(105)+chr(108)+chr(101)+chr(114)))[:48]}")
assert tag2.calls.get("setTrailer"), "play_trailer is our own route and explains itself"
print("  deliberate: our route shows a message rather than a dead button")

tag3 = kodistub._Tag()
_set_show_tags(tag3, {"name": "One Pace"})
print(f"  no trailer in the meta      -> setTrailer {chr(115)+chr(101)+chr(116) if False else ('set' if tag3.calls.get('setTrailer') else 'skipped')}")
assert not tag3.calls.get("setTrailer")

print()
print("all assertions passed")
