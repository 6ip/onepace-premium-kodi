"""The URLs we build, and the season the list should follow."""
import harness
from urllib import parse

harness.setup()
ONEPACE = harness.meta("onepace")
from lib import provider_api
import lib.playback as pb

VIDS = {v["id"]: v for v in ONEPACE["videos"]}
q = lambda u: dict(parse.parse_qsl(u.split("?", 1)[1]))

print("=== autoplay is the only difference from an ordinary row ===")
poster = next(s["poster"] for s in ONEPACE["seasons"] if s.get("season") == VIDS["AR_10"]["season"])
row = q(provider_api.episode_play_url(VIDS["AR_10"], ONEPACE, "pp_onepacee",
                                      "series", poster, "AR_10"))
auto = q(provider_api.episode_play_url(VIDS["AR_10"], ONEPACE, "pp_onepacee",
                                       "series", poster, "AR_10", autoplay=True))
print(f"  row  autoplay={row.get('autoplay')!r}")
print(f"  card autoplay={auto.get('autoplay')!r}")
assert "autoplay" not in row, "ordinary rows must keep the URL Kodi already knows"
assert auto.get("autoplay") == "1"
assert {k: v for k, v in auto.items() if k != "autoplay"} == row

print()
print("=== the row carries what playback needs ===")
for key in ("catalog_type", "video_id", "thumb", "parent_id", "series_name",
            "episode_title", "season", "episode", "season_poster"):
    assert key in row, key
print(f"  {len(row)} parameters, including {', '.join(sorted(row)[:5])}...")

print()
print("=== only a different season moves the list ===")
B = "plugin://plugin.video.onepacepremium/?action=list_episodes&catalog_type=series&video_id=pp_onepacee&season="
cases = [
    ("same season",                 B + "2", 2, False),
    ("season roll",                 B + "1", 2, True),
    ("hide-watched jump",           B + "1", 6, True),
    ("1 must not match 11",         B + "11", 1, True),
    ("11 must not match 1",         B + "1", 11, True),
    ("specials",                    B + "0", 0, False),
    ("In Progress list",            "plugin://plugin.video.onepacepremium/?action=list_in_progress", 2, False),
    ("season list",                 "plugin://plugin.video.onepacepremium/?action=list_seasons&video_id=x", 2, False),
    ("root menu",                   "plugin://plugin.video.onepacepremium/", 2, False),
]
for label, path, season, want in cases:
    got = pb._showing_other_season(path, season)
    print(f"  {label:<24} -> {'move' if got else 'refresh'}")
    assert got is want, label

print()
print("all assertions passed")
