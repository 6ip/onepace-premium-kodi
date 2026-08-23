"""Episode and season lists: closing cleanly, hiding watched, counting."""
import harness

S = harness.setup({"hide_watched": "false", "show_specials": "true",
                   "flatten_single_season": "false"})
ONEPACE, MUHN, KUMA = (harness.meta("onepace"), harness.meta("muhnpace"),
                       harness.meta("kuma"))

from lib import episode_routes as er, provider_api, utils
utils.ensure_configured = er.ensure_configured = lambda: True
er._watched.cache_total = lambda *a, **k: None

META, WATCHED = [ONEPACE], [set()]
provider_api._fetch_provider_meta = er._fetch_provider_meta = lambda ct, sid: META[0]
er._watched.get_watched = lambda sid: WATCHED[0]


def episodes(season, watched=(), hide=False, meta=None):
    META[0] = ONEPACE if meta is None else meta
    WATCHED[0] = set(watched)
    S["hide_watched"] = "true" if hide else "false"
    rec = harness.recorder()
    er.list_episodes({"catalog_type": "series", "video_id": "pp_onepacee",
                      "season": str(season)})
    return rec


def seasons(watched=(), hide=False, meta=None, flatten=False):
    META[0] = ONEPACE if meta is None else meta
    WATCHED[0] = set(watched)
    S["hide_watched"] = "true" if hide else "false"
    S["flatten_single_season"] = "true" if flatten else "false"
    rec = harness.recorder()
    er.list_seasons({"catalog_type": "series", "video_id": "pp_onepacee"})
    return rec


S1 = [v["id"] for v in harness.episodes(ONEPACE, 1)]

print("=== every exit closes its directory exactly once ===")
cases = [
    ("a normal season",            lambda: episodes(1), True),
    ("all watched, hide off",      lambda: episodes(1, S1), True),
    ("all watched, hide on",       lambda: episodes(1, S1, hide=True), True),
    ("a season that does not exist", lambda: episodes(99), False),
]
for label, run, want in cases:
    rec = run()
    print(f"  {label:<30} end_directory={rec.ended}  notify={rec.notifications}")
    assert len(rec.ended) == 1, "must close exactly once"
    assert rec.ended[0] is want

print()
print("=== an empty season is the setting working, not an error ===")
rec = episodes(1, S1, hide=True)
assert rec.ended == [True] and rec.notifications[0][1] == "INFO"
print(f"  {rec.notifications[0][0]!r} as INFO, directory closed cleanly")
rec = episodes(0, [v["id"] for v in harness.episodes(ONEPACE, 0)], hide=True)
print(f"  specials worded as: {rec.notifications[0][0]!r}")
assert "Specials" in rec.notifications[0][0]

print()
print("=== hiding never changes what is counted ===")
watched = harness.watched_ids()
off = {li.label: li.properties for _, li, _ in seasons(watched).directories}
on = {li.label: li.properties for _, li, _ in seasons(watched, hide=True).directories}
print(f"  seasons shown: {len(off)} -> {len(on)} (hid {len(off) - len(on)})")
assert len(on) < len(off), "fully watched seasons should be hidden"
changed = [k for k in on if on[k] != off[k]]
print(f"  counts that differ on the seasons still shown: {changed or 'none'}")
assert not changed

print()
print("=== a season on show is never empty ===")
empty = []
for label in on:
    num = 0 if label == "Specials" else int(label.split()[-1])
    visible = [v for v in harness.episodes(ONEPACE, num) if v["id"] not in watched]
    if not visible:
        empty.append(label)
print(f"  shown seasons that would open empty: {empty or 'none'}")
assert not empty

print()
print("=== a one-season show names itself, flatten or not ===")
kuma_ids = [v["id"] for v in KUMA["videos"] if v.get("id")]
for flatten in (False, True):
    rec = seasons(kuma_ids, hide=True, meta=KUMA, flatten=flatten)
    note = rec.notifications[0][0] if rec.notifications else None
    print(f"  flatten {'on ' if flatten else 'off'} -> {note!r}")
    assert note and note.startswith(KUMA["name"]), note

print()
print("=== notice-card seasons stay visible ===")
muhn_eps = [v["id"] for v in MUHN["videos"]
            if v.get("id") and not str(v["id"]).startswith("pp_COMPLETE")]
META[0] = MUHN
WATCHED[0] = set(muhn_eps)
S["hide_watched"] = "true"
rec = harness.recorder()
er.list_seasons({"catalog_type": "series", "video_id": "pp_muhnpace"})
shown = [li.label for _, li, _ in rec.directories]
print(f"  every real episode watched, {len(shown)} season(s) remain: {shown}")
assert shown, "seasons holding an unwatchable notice card must not vanish"

print()
print("=== Show Specials changes what is counted ===")
from lib.provider_api import countable_episode_ids
specials = [v["id"] for v in ONEPACE["videos"] if v.get("season") == 0]
S["show_specials"] = "true"
on = countable_episode_ids(ONEPACE)
S["show_specials"] = "false"
off = countable_episode_ids(ONEPACE)
print(f"  {len(ONEPACE['videos'])} videos, {len(specials)} specials")
print(f"  on -> {len(on)}   off -> {len(off)}   difference {len(on) - len(off)}")
assert len(on) - len(off) == len(specials), (len(on), len(off), len(specials))
assert not any(e in set(off) for e in specials), "a special survived the filter"
S["show_specials"] = "true"

print()
print("all assertions passed")
