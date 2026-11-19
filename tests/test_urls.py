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
print("=== a provider that cannot be reached is mentioned once ===")
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


# Eight parallel failures are one problem, and so is the next screen: the
# connection has gone, and hearing that once is enough.
_was_session = utils.session
utils.session = lambda: type("S", (), {"get": _no_connection})()
kodistub.recorder.reset()          # also clears the home window
utils.reset_for_invocation()
for _n in range(5):
    assert utils.fetch_data(f"https://srv/meta/series/pp_{_n}.json") is None
_said = [m for m, _ in kodistub.recorder.notifications]
print(f"  five failures in one plugin call -> {len(_said)}: {_said}")
assert len(_said) == 1, _said
assert "No connection" in _said[0], "it should name the connection, not blame a host"

# Moving to the next screen is the same lost connection, not a new one.
utils.reset_for_invocation()
assert utils.fetch_data("https://srv/meta/series/pp_x.json") is None
print(f"  the screen after that -> {len(kodistub.recorder.notifications)} message(s)")
assert len(kodistub.recorder.notifications) == 1,     "a message a screen is what the fallback was meant to stop"


class _Answers:
    def get(self, url, timeout=None):
        return type("R", (), {"raise_for_status": lambda s: None,
                              "json": lambda s: {"ok": True}})()


# Losing it a second time is worth saying again, so a real outage is never
# swallowed by one that has already been mentioned.
utils.session = lambda: _Answers()
assert utils.fetch_data("https://srv/meta/series/pp_x.json") == {"ok": True}
utils.session = lambda: type("S", (), {"get": _no_connection})()
assert utils.fetch_data("https://srv/meta/series/pp_y.json") is None
print(f"  after the server answered again -> "
      f"{len(kodistub.recorder.notifications)} message(s)")
assert len(kodistub.recorder.notifications) == 2, "losing it again went unsaid"

# A server that answers with an error is a different thing to say.
kodistub.recorder.reset()


def _refused(self, *a, **k):
    err = _requests.RequestException("boom")
    err.response = type("R", (), {"status_code": 503})()
    raise err


utils.session = lambda: type("S", (), {"get": _refused})()
assert utils.fetch_data("https://srv/meta/series/pp_z.json") is None
_said = [m for m, _ in kodistub.recorder.notifications]
print(f"  a server error -> {_said}")
assert _said and "503" in _said[0], _said
utils.session = _was_session
print("  once a connection, not once a screen  OK")

print()
print("=== My Lists falls back to what is on disk ===")
from lib import downloads, my_lists

_HELD = {
    "files": {
        "C:/dl/One Pace/Season 01/1x03 - Romance Dawn.mkv": {
            "series_id": "pp_onepacee", "series_name": "One Pace",
            "episode_id": "RO_3", "season": "1", "episode": "3",
            "episode_title": "Romance Dawn",
            "thumb": "https://img/t3.jpg",
            "season_poster": "https://img/s1.jpg",
            "artwork": {"thumb": "C:/dl/One Pace/Season 01/1x03 - Romance Dawn-thumb.jpg",
                        "season_poster": "C:/dl/One Pace/season01-poster.jpg"},
            "video": {"id": "RO_3", "season": 1, "episode": 3,
                      "title": "Romance Dawn", "thumbnail": "https://img/t3.jpg"},
        },
    },
    "series": {"One Pace": {"name": "One Pace", "poster": "https://img/p.jpg",
                            "art": {"poster": "C:/dl/One Pace/poster.jpg"}}},
}
_ON_DISK = {"C:/dl/One Pace/Season 01/1x03 - Romance Dawn-thumb.jpg",
            "C:/dl/One Pace/season01-poster.jpg", "C:/dl/One Pace/poster.jpg"}

_was_read, _was_exists = downloads.read_index, downloads.xbmcvfs.exists
_was_meta, _was_enabled = my_lists._fetch_provider_meta, downloads.enabled
downloads.read_index = lambda: _HELD
downloads.xbmcvfs.exists = lambda path: path in _ON_DISK
downloads.enabled = lambda: True

# Online, nothing changes: the provider still answers first.
my_lists._fetch_provider_meta = lambda ct, sid: ONEPACE
meta, videos, posters, title = my_lists._get_series_meta("pp_onepacee")
print(f"  online  -> {len(videos)} episode(s) from the provider")
assert len(videos) > 1, "the provider's own list should be used when it answers"

# Offline, the rows we hold are the only ones worth offering.
my_lists._fetch_provider_meta = lambda ct, sid: None
meta, videos, posters, title = my_lists._get_series_meta("pp_onepacee")
print(f"  offline -> {len(videos)} episode(s) from the index, title {title!r}")
assert list(videos) == ["RO_3"], videos
assert title == "One Pace", title
assert videos["RO_3"]["thumbnail"].endswith("-thumb.jpg"),     "it drew the link when the picture was beside the video"
assert posters[1].endswith("season01-poster.jpg"), posters
assert meta["poster"].endswith("One Pace/poster.jpg"), "the series poster was not the kept one"

# A series we hold nothing of stays skipped, as it was before.
meta, videos, posters, title = my_lists._get_series_meta("pp_muhnpace")
print(f"  offline, nothing held -> meta={meta!r}")
assert meta is None and videos == {}, (meta, videos)

# With downloads switched off there is no index to fall back on.
downloads.enabled = lambda: False
assert my_lists._get_series_meta("pp_onepacee")[0] is None, "it read the index anyway"

(downloads.read_index, downloads.xbmcvfs.exists,
 my_lists._fetch_provider_meta, downloads.enabled) = (_was_read, _was_exists,
                                                      _was_meta, _was_enabled)
print("  held episodes offline, nothing invented, and off means off  OK")

print()
print("=== with no connection, what is on disk stands in ===")
from lib import catalog_routes, playback

_TWO = {
    "files": {
        "C:/dl/One Pace/Season 01/1x01.mkv": {
            "series_id": "pp_onepacee", "series_name": "One Pace",
            "episode_id": "RO_1", "season": "1", "episode": "1",
            "video": {"id": "RO_1", "season": 1, "episode": 1,
                      "title": "Romance Dawn 01"},
        },
        "C:/dl/One Pace/Season 01/1x02.mkv": {
            "series_id": "pp_onepacee", "series_name": "One Pace",
            "episode_id": "RO_2", "season": "1", "episode": "2",
            "video": {"id": "RO_2", "season": 1, "episode": 2,
                      "title": "Romance Dawn 02"},
        },
        "C:/dl/Muhn Pace/Season 17/17x01.mkv": {
            "series_id": "pp_muhnpace", "series_name": "Muhn Pace",
            "episode_id": "MP_1", "season": "17", "episode": "1",
            "video": {"id": "MP_1", "season": 17, "episode": 1,
                      "title": "Enies Lobby 01"},
        },
    },
    "series": {
        "One Pace": {"name": "One Pace", "description": "The show's own words.",
                     "order": 0,
                     "seasons": [{"season": 1, "name": "Romance Dawn",
                                  "overview": "Luffy sets out.",
                                  "poster": "https://img/s1.jpg"},
                                 {"season": 2, "name": "Orange Town",
                                  "poster": "https://img/s2.jpg"}]},
        "Muhn Pace": {"name": "Muhn Pace", "order": 1},
    },
}
downloads.read_index = lambda: _TWO
downloads.enabled = lambda: True
downloads.xbmcvfs.exists = lambda path: False

_held = downloads.held_meta("pp_onepacee")
print(f"  held_meta -> {len(_held['videos'])} video(s), "
      f"{len(_held['seasons'])} season(s), {_held['name']!r}")
assert [v["id"] for v in _held["videos"]] == ["RO_1", "RO_2"], _held["videos"]
assert _held["description"] == "The show's own words.", "the series record was dropped"
# The whole seasons array rides along, so names and overviews survive offline.
assert _held["seasons"][0]["name"] == "Romance Dawn", _held["seasons"]
assert _held["seasons"][0]["overview"] == "Luffy sets out.", _held["seasons"]
assert downloads.held_meta("pp_nothing") is None, "it invented a series"
downloads.enabled = lambda: False
assert downloads.held_meta("pp_onepacee") is None, "downloads are off"
downloads.enabled = lambda: True
print("  the provider's own episode entries, kept whole  OK")

_series = downloads.held_series()
print(f"  held_series -> {[s['name'] for s in _series]}")
# Where the catalogue puts them, not the alphabet: One Pace sits first online
# and under On Device, so it has to sit first here too.
assert [s["id"] for s in _series] == ["pp_onepacee", "pp_muhnpace"], _series
assert all("name" in s and "id" in s for s in _series), "a catalogue row needs both"

# A series the index has never ranked goes last, rather than jumping the queue.
_unranked = {"files": dict(_TWO["files"]), "series": dict(_TWO["series"])}
_unranked["files"]["C:/dl/Zed/1x01.mkv"] = {
    "series_id": "pp_zed", "series_name": "Aaa Pace", "episode_id": "ZZ_1",
    "season": "1", "episode": "1",
    "video": {"id": "ZZ_1", "season": 1, "episode": 1, "title": "Zed"},
}
_unranked["series"]["Aaa Pace"] = {"name": "Aaa Pace"}
_was_ranks = provider_api.series_order
provider_api.series_order = lambda: {}
downloads.read_index = lambda: _unranked
print(f"  with one unranked -> {[s['name'] for s in downloads.held_series()]}")
assert [s["id"] for s in downloads.held_series()] ==     ["pp_onepacee", "pp_muhnpace", "pp_zed"], "an unranked series jumped the queue"
provider_api.series_order = _was_ranks
downloads.read_index = lambda: _TWO
print("  the order the catalogue gave them, kept offline  OK")

# The next episode is still offered once the provider has gone quiet.
_was_next = (provider_api._fetch_provider_meta, downloads.path_for)
provider_api._fetch_provider_meta = lambda ct, sid: None
downloads.path_for = lambda eid: "C:/dl/x.mkv" if eid in {"RO_1", "RO_2"} else ""
playback._watched.get_watched = lambda sid: set()
_up = playback._next_episode("pp_onepacee", "RO_1")
print(f"  after RO_1 -> {_up and _up['title']!r}")
assert _up and _up["title"] == "Romance Dawn 02", _up
assert playback._next_episode("pp_onepacee", "RO_2") is None, "there is no third"
provider_api._fetch_provider_meta, downloads.path_for = _was_next
print("  autoplay carries on past the six hours the cache lasts  OK")

# Browse is the only way into a season list, so without this the fallback
# there is unreachable once the catalogue has gone too.
_was_browse = (catalog_routes._fetch_provider_manifest,
               catalog_routes.ensure_configured,
               catalog_routes._watched.get_all_series_stats)
catalog_routes._fetch_provider_manifest = lambda: None
catalog_routes.ensure_configured = lambda: True
catalog_routes._watched.get_all_series_stats = lambda: {"pp_onepacee": (2, 479)}

kodistub.recorder.reset()
catalog_routes.list_browse({})
_rows = [(url, li) for url, li, _ in kodistub.recorder.directories]
print(f"  browse with no manifest -> {[li.label for _, li in _rows]}")
assert [li.label for _, li in _rows] == ["One Pace", "Muhn Pace"], _rows
assert all("list_seasons" in url for url, _ in _rows), "they must open a season list"

# Two rows are held of One Pace; the real show has 479 episodes. The count has
# to come from the watched store or every series would read as finished.
_props = dict(_rows[0][1].properties)
print(f"  One Pace row -> {_props}")
assert _props.get("TotalEpisodes") == "479", _props
assert _props.get("WatchedEpisodes") == "2", _props

# Nothing held at all is still an empty screen, not an error.
downloads.read_index = lambda: {"files": {}, "series": {}}
kodistub.recorder.reset()
catalog_routes.list_browse({})
print(f"  nothing held -> {len(kodistub.recorder.directories)} row(s), "
      f"closed {kodistub.recorder.ended}")
assert not kodistub.recorder.directories and kodistub.recorder.ended == [True]
downloads.read_index = lambda: _TWO

(catalog_routes._fetch_provider_manifest, catalog_routes.ensure_configured,
 catalog_routes._watched.get_all_series_stats) = _was_browse
print("  browse offers what is held, counted from the watched store  OK")

print()
print("all assertions passed")
