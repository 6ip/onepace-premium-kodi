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
print("=== a provider that cannot be reached fails once, and quietly ===")
import kodistub
import requests as _requests
from lib import utils

# A failure is remembered, so the prefetch and the per-series call after it
# do not both pay for the same timeout.
_store = {}
_was = (provider_api._cache.get, provider_api._cache.set,
        provider_api.fetch_data, provider_api._meta_url)
provider_api._cache.get = lambda key: _store.get(key)
provider_api._cache.set = lambda key, value, ttl: _store.__setitem__(key, value)
provider_api._meta_url = lambda ct, vid: f"https://srv/meta/{ct}/{vid}.json"
_asked = []
provider_api.fetch_data = lambda url: _asked.append(url) or None

assert provider_api._fetch_provider_meta("series", "pp_gone") is None
assert provider_api._fetch_provider_meta("series", "pp_gone") is None
print(f"  asked twice for one series -> {len(_asked)} request(s)")
assert len(_asked) == 1, "the failure was not remembered"

provider_api._prefetch_metas("series", ["pp_gone", "pp_new", "pp_other"])
print(f"  a prefetch with one known bad -> {len(_asked)} request(s) in all")
assert len(_asked) == 3, _asked
assert _asked.count(_asked[0]) == 1, "it went back for one it knew had gone"

# A real answer still lasts, so this is a short memory and not a blockade.
provider_api.fetch_data = lambda url: {"meta": {"name": "One Pace"}}
assert provider_api._fetch_provider_meta("series", "pp_back")["name"] == "One Pace"
(provider_api._cache.get, provider_api._cache.set,
 provider_api.fetch_data, provider_api._meta_url) = _was


def _no_connection(self, *a, **k):
    raise _requests.RequestException("no connection")


# Eight parallel failures are one problem, so they get one message.
_was_session = utils.session
utils.session = lambda: type("S", (), {"get": _no_connection})()
kodistub.recorder.reset()
utils.reset_for_invocation()
for _n in range(5):
    assert utils.fetch_data(f"https://srv/meta/series/pp_{_n}.json") is None
_said = [m for m, _ in kodistub.recorder.notifications if "Request failed" in m]
print(f"  five failures in one plugin call -> {len(_said)} message(s)")
assert len(_said) == 1, _said

utils.reset_for_invocation()
assert utils.fetch_data("https://srv/meta/series/pp_x.json") is None
_said = [m for m, _ in kodistub.recorder.notifications if "Request failed" in m]
assert len(_said) == 2, "the next plugin call should be allowed to speak"
utils.session = _was_session
print("  one message a call, and the next call may speak again  OK")

print()
print("all assertions passed")
