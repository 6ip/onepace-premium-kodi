"""What the next-episode card offers, and what it refuses to offer."""
import harness

S = harness.setup({"autoplay_next": "true", "autoplay_next_season": "true",
                   "hide_watched": "false"})
ONEPACE, MUHN = harness.meta("onepace"), harness.meta("muhnpace")

import lib.playback as pb
from lib import provider_api, episode_routes

provider_api._fetch_provider_meta = lambda ct, sid: {
    "pp_onepacee": ONEPACE, "pp_muhnpace": MUHN}.get(sid)
episode_routes.episode_has_stream = lambda ct, vid: PLAYABLE[0]
PLAYABLE = [True]
WATCHED = [set()]
pb._watched.get_watched = lambda sid: WATCHED[0]

ORDER = [v["id"] for v in harness.episodes(ONEPACE) if v["season"]]


def nxt(eid, series="pp_onepacee"):
    r = pb._next_episode(series, eid)
    return r["url"].split("video_id=")[1].split("&")[0] if r else None


print("=== ordinary order ===")
bad = [(e, nxt(e), ORDER[i + 1]) for i, e in enumerate(ORDER[:-1]) if nxt(e) != ORDER[i + 1]]
print(f"  {len(ORDER) - 1} transitions, {len(bad)} out of order")
assert not bad, bad[:3]

print()
print("=== the end of the run ===")
print(f"  last episode {ORDER[-1]} -> {nxt(ORDER[-1])}")
assert nxt(ORDER[-1]) is None
print(f"  unknown id 'NOPE' -> {nxt('NOPE')}")
assert nxt("NOPE") is None

print()
print("=== specials are never offered, and never lead anywhere ===")
specials = [v["id"] for v in ONEPACE["videos"] if v.get("season") == 0]
print(f"  {len(specials)} specials; from the first one -> {nxt(specials[0])}")
assert nxt(specials[0]) is None
assert not any(nxt(e) in specials for e in ORDER), "a special was offered as next"

print()
print("=== notice cards are never offered ===")
notices = [v["id"] for v in MUHN["videos"] if str(v["id"]).startswith("pp_COMPLETE")]
muhn_order = [v["id"] for v in harness.episodes(MUHN) if v["season"]]
print(f"  notice cards: {notices}")
for n in notices:
    assert nxt(n, "pp_muhnpace") is None, f"{n} resolved to something"
assert not any(nxt(e, "pp_muhnpace") in notices for e in muhn_order), "a notice card was offered"
print("  none offered, none lead anywhere  OK")

print()
print("=== Continue To Next Season ===")
s1 = [v["id"] for v in harness.episodes(ONEPACE, 1)]
S["autoplay_next_season"] = "false"
off = nxt(s1[-1])
S["autoplay_next_season"] = "true"
on = nxt(s1[-1])
print(f"  end of season 1 ({s1[-1]}): off -> {off}, on -> {on}")
assert off is None and on is not None

print()
print("=== an episode with no stream is not offered ===")
PLAYABLE[0] = False
print(f"  no stream -> {nxt(ORDER[0])}")
assert nxt(ORDER[0]) is None
PLAYABLE[0] = True

print()
print("=== Hide Watched makes the card skip what you have seen ===")
WATCHED[0] = harness.watched_ids()
S["hide_watched"] = "false"
plain = nxt(ORDER[0])
S["hide_watched"] = "true"
skipping = nxt(ORDER[0])
print(f"  hide off -> {plain}   hide on -> {skipping}")
assert plain in WATCHED[0], "the fixture should have the next one watched"
assert skipping not in WATCHED[0], "it should have skipped past the watched run"

offers = [nxt(e) for e in ORDER]
print(f"  across {len(ORDER)} episodes, watched ones offered: "
      f"{sum(1 for o in offers if o in WATCHED[0])}")
assert not any(o in WATCHED[0] for o in offers)

S["autoplay_next_season"] = "false"
print(f"  boundary still held while skipping: {nxt(s1[-1])}")
assert nxt(s1[-1]) is None
S["autoplay_next_season"] = "true"

print()
print("=== everything ahead watched -> no card ===")
WATCHED[0] = set(ORDER)
print(f"  from {ORDER[0]} -> {nxt(ORDER[0])}")
assert nxt(ORDER[0]) is None
WATCHED[0] = set()
S["hide_watched"] = "false"

print()
print("=== what the card is handed ===")
card = pb._next_episode("pp_onepacee", ORDER[0])
for k in ("series", "episode", "title", "thumb"):
    print(f"  {k:<8} {str(card[k])[:60]}")
assert card["series"] and "(" in card["episode"] and card["thumb"]
assert "autoplay=1" in card["url"] and "check_resume" in card["url"]

print()
print("all assertions passed")
