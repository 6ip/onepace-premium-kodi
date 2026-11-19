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
print("=== a season number that arrives as a string still finds its poster ===")
# The feed has sent season: '1' before — one cached copy of KUMA_SHAVED still
# carries it. The map was keyed exactly as it arrived but looked up with an
# int, so every lookup missed and all seasons quietly shared the show poster.
import copy
import re as _re

strung = copy.deepcopy(ONEPACE)
for s in strung["seasons"]:
    s["season"] = str(s["season"])
META[0] = strung
WATCHED[0] = set()
S["hide_watched"] = "false"
rec = harness.recorder()
er.list_seasons({"catalog_type": "series", "video_id": "pp_onepacee"})
posters = {}
for url, li, _ in rec.directories:
    found = _re.search(r"season=(\d+)", url)
    if found:
        posters[int(found.group(1))] = li.art.get("poster")
distinct = len({p for p in posters.values() if p})
print(f"  {len(posters)} seasons listed, {distinct} distinct posters")
assert distinct > 1, "every season fell back to the same art"
META[0] = ONEPACE

print()
print("=== the menu background is the viewer's to choose ===")
# A chosen picture can be moved or deleted later, and a missing fanart leaves
# the menu on a black screen — so it is checked every time, not trusted once.
_real_exists = utils.xbmcvfs.exists
S["background_image"] = ""
print(f"  nothing chosen      -> {utils.background().rsplit('/', 1)[-1]}")
assert utils.background().endswith("/resources/fanart.png")

S["background_image"] = "/home/me/wallpaper.jpg"
utils.xbmcvfs.exists = lambda p: True
print(f"  chosen and present  -> {utils.background()}")
assert utils.background() == "/home/me/wallpaper.jpg"

utils.xbmcvfs.exists = lambda p: False
print(f"  chosen but missing  -> {utils.background().rsplit('/', 1)[-1]}")
assert utils.background().endswith("/resources/fanart.png"),     "a deleted picture would leave the menu black"

utils.xbmcvfs.exists = _real_exists
S["background_image"] = ""

# Every menu of ours has to use it, or one section keeps the shipped picture
# while the rest change, which reads as a bug.
_lib = harness.ADDON / "lib"
_users = {p.name for p in _lib.glob("*.py") if "background()" in p.read_text(encoding="utf-8")}
print(f"  used by: {sorted(_users - {'utils.py'})}")
assert {"catalog_routes.py", "my_lists.py", "downloads.py"} <= _users
_hard = [p.name for p in _lib.glob("*.py")
         if "resources/fanart.png" in p.read_text(encoding="utf-8") and p.name != "utils.py"]
assert not _hard, f"a fanart path is still hardcoded in {_hard}"
print("  all our menus go through it  OK")

print()
print("=== a one-season show carries Download Season on its series row ===")
# The season row is where that item lives, and flattening removes the row,
# so the series row has to stand in for it.
for meta, specials, want in ((KUMA, "true", 1), (KUMA, "false", 1),
                             (MUHN, "true", 14), (ONEPACE, "true", 34),
                             (ONEPACE, "false", 33)):
    S["show_specials"] = specials
    got = provider_api.listed_seasons(meta)
    print(f"  {meta['name']:<28} specials={specials:<5} -> {len(got)} season(s)"
          f"{'  <- stands in' if len(got) == 1 else ''}")
    assert len(got) == want, (meta["name"], specials, len(got))
S["show_specials"] = "true"

# Specials last, so the season list reads 1, 2, 3 ... Specials.
assert provider_api.listed_seasons(ONEPACE)[-1] == 0, "Specials moved to the front"
S["show_specials"] = "false"
assert 0 not in provider_api.listed_seasons(ONEPACE), "hidden means hidden"
S["show_specials"] = "true"

_cat = (harness.ADDON / "lib" / "catalog_routes.py").read_text(encoding="utf-8")
_row = _cat[_cat.index("series_ctx = ["):_cat.index("addContextMenuItems(series_ctx)")]
assert "listed_seasons(s_meta)" in _row, "it would need a second way to count seasons"
assert "len(only) == 1" in _row, "every series row would offer it, One Pace included"
assert 'get_setting("downloads_enabled")' in _row, "it would show with downloads off"
assert "season=only[0]" in _row, "the run would not know which season to fetch"

# One helper, or the row and the list can disagree about what flattens.
_er = (harness.ADDON / "lib" / "episode_routes.py").read_text(encoding="utf-8")
assert "seasons = listed_seasons(meta)" in _er, "list_seasons counts its own way again"
print("  same count on both sides, and only when there is one  OK")

print()
print("=== a season says its own piece, or the show speaks for it ===")
from lib.provider_api import season_overviews

# The two shapes the feed really sends: a key with nothing in it (Muhn Pace)
# and no key at all (Onigashima, Kuma). Neither may reach .strip() bare.
_odd = {"seasons": [{"season": 1, "overview": "Real text."},
                    {"season": 2, "overview": None},
                    {"season": 3},
                    {"season": 4, "overview": "   "},
                    {"season": "5", "overview": "Arrived as a string."},
                    {"season": "x", "overview": "Not a season."}]}
_got = season_overviews(_odd)
print(f"  odd shapes -> {sorted(_got)}")
assert sorted(_got) == [1, 5], _got
assert _got[1] == "Real text." and _got[5] == "Arrived as a string."
assert season_overviews({}) == {} and season_overviews({"seasons": None}) == {}


def _plots(meta):
    """season number -> the plot its row ended up with."""
    rec = seasons(meta=meta)
    out = {}
    for url, li, _ in rec.directories:
        found = _re.search(r"season=(\d+)", url)
        if found:
            out[int(found.group(1))] = li._tag.calls.get("setPlot")
    return out


_show = ONEPACE["description"]
_one = _plots(ONEPACE)
_own = {s for s, p in _one.items() if p and p != _show}
print(f"  One Pace: {len(_own)}/{len(_one)} seasons with their own text")
assert len(_own) == len(_one), "a season fell back although the feed had text"
assert len({_one[s] for s in _one}) == len(_one), "seasons are sharing one plot"
assert _one[1] == next(s["overview"] for s in ONEPACE["seasons"]
                       if s["season"] == 1), "the wrong season's text"

# Kuma has a season name but no overview, so the show has to speak for it.
_k = _plots(KUMA)
print(f"  Kuma: {len(_k)} season(s), plot is the show's -> "
      f"{all(p == KUMA['description'] for p in _k.values())}")
assert _k and all(p == KUMA["description"] for p in _k.values()),     "a season with no overview should keep the series description"

# A second series, to show it is not One Pace's shape doing the work.
_m, _mshow = _plots(MUHN), MUHN["description"]
_mown = sorted(s for s, p in _m.items() if p and p != _mshow)
_expect = sorted(int(s["season"]) for s in MUHN["seasons"]
                 if (s.get("overview") or "").strip()
                 and int(s["season"]) in _m)
print(f"  Muhn Pace: own text on {_mown}")
assert _mown == _expect, (_mown, _expect)

# One list carrying both, since no fixture mixes them: the feed drops the key
# on some series and sends it empty on others, and both must fall back.
_mixed = copy.deepcopy(ONEPACE)
for _s in _mixed["seasons"]:
    if _s["season"] == 2:
        _s["overview"] = None
    elif _s["season"] == 5:
        _s.pop("overview", None)
    elif _s["season"] == 7:
        _s["overview"] = "   "
_mix = _plots(_mixed)
_fell = sorted(s for s, p in _mix.items() if p == _mixed["description"])
print(f"  mixed list: fell back on {_fell}, kept own text on "
      f"{len(_mix) - len(_fell)}")
assert _fell == [2, 5, 7], _fell
assert len(_mix) - len(_fell) == len(_mix) - 3, "others were dragged down too"
assert all(_mix[s] for s in _mix), "a season ended up with no plot at all"
META[0] = ONEPACE
print("  own text when there is some, the show's when there is not  OK")

print()
print("=== a season is called what the viewer asked it to be called ===")
import xml.etree.ElementTree as _ET
from lib.provider_api import season_labeller


def _labels(mode, meta=None):
    S["season_labels"] = mode
    return [li.label for _, li, _ in seasons(meta=meta).directories]


_num = _labels("0")
_arc = _labels("1")
_both = _labels("2")
print(f"  number -> {_num[:3]} ... {_num[-1]}")
print(f"  arc    -> {_arc[:3]} ... {_arc[-1]}")
print(f"  both   -> {_both[:2]} ... {_both[-1]}")

assert _num[0] == "Season 1" and _num[-1] == "Specials", _num[:2]
assert _arc[0] == "Romance Dawn" and _arc[1] == "Orange Town", _arc[:2]
assert _both[0] == "Season 1 · Romance Dawn", _both[0]
assert len(_num) == len(_arc) == len(_both), "a mode lost or gained a row"

# The feed calls season 0 "Specials", which is what we call it already.
assert _arc[-1] == "Specials" and _both[-1] == "Specials",     "Specials should not read as Specials twice"

# Nothing silently keeps a number when a name exists, and vice versa.
assert not any(l.startswith("Season ") for l in _arc[:-1]),     f"arc mode left a numbered row: {[l for l in _arc[:-1] if l.startswith('Season ')]}"
assert all(" · " in l for l in _both[:-1]), "both mode missed a row"

# A season the feed never named has to keep its number, in either mode.
_noname = copy.deepcopy(ONEPACE)
for _s in _noname["seasons"]:
    if _s["season"] == 3:
        _s["name"] = None
    elif _s["season"] == 4:
        _s.pop("name", None)
_fb_arc = _labels("1", meta=_noname)
_fb_both = _labels("2", meta=_noname)
print(f"  unnamed seasons 3 and 4 -> {_fb_arc[2:4]} / {_fb_both[2:4]}")
assert _fb_arc[2:4] == ["Season 3", "Season 4"], _fb_arc[2:4]
assert _fb_both[2:4] == ["Season 3", "Season 4"], _fb_both[2:4]
assert _fb_arc[0] == "Romance Dawn", "one unnamed season dragged the rest down"

# This feed has sent season numbers as strings before, which once cost every
# season its poster. The names are keyed the same way, so check them too.
_strung2 = copy.deepcopy(ONEPACE)
for _s in _strung2["seasons"]:
    _s["season"] = str(_s["season"])
S["season_labels"] = "1"
assert season_labeller(_strung2)(1) == "Romance Dawn", "a string season lost its name"
assert season_labeller(_strung2)(0) == "Specials", "specials lost their name"

# An unset or unknown value must read as the plain number, never crash.
for _odd in ("", "9", "arc", None):
    S["season_labels"] = _odd if _odd is not None else ""
    assert season_labeller(ONEPACE)(1) == "Season 1", _odd
S["season_labels"] = "0"
META[0] = ONEPACE

# The notice when a season finishes has to name it like the row did.
for _mode, _want in (("0", "Season 1 fully watched"),
                     ("1", "Romance Dawn fully watched"),
                     ("2", "Season 1 · Romance Dawn fully watched")):
    S["season_labels"] = _mode
    _said = [m for m, _ in episodes(1, S1, hide=True).notifications]
    print(f"  mode {_mode} finished-season notice -> {_said}")
    assert _want in _said, (_mode, _said)
S["season_labels"] = "0"
print("  three modes, the number when unnamed, the number when unset  OK")

print()
print("=== every season label comes from the one place ===")
# Seven sites used to spell this out; the list and the download summary
# disagreeing is exactly what the setting would expose.
_spelled = []
for _f in sorted((harness.ADDON / "lib").glob("*.py")):
    for _n, _line in enumerate(_f.read_text(encoding="utf-8").splitlines(), 1):
        if '"Specials" if' in _line:
            _spelled.append(f"{_f.name}:{_n}")
print(f"  spelled out in: {_spelled}")
assert _spelled == ["provider_api.py:194"] or (
    len(_spelled) == 1 and _spelled[0].startswith("provider_api.py")),     f"a season label is still built by hand: {_spelled}"

_row = [s for s in _ET.parse(harness.ADDON / "resources" / "settings.xml")
        .getroot().iter("setting") if s.get("id") == "season_labels"]
assert _row, "the setting is missing from settings.xml"
assert _row[0].get("default") == "0", "it should start on the season number"
assert _row[0].get("values").split("|") == ["Season number", "Arc name", "Both"],     _row[0].get("values")
print(f"  settings row: {_row[0].get('values')!r}, default {_row[0].get('default')}  OK")

print()
print("=== with no connection the lists fall back to what is held ===")
from lib import downloads as _dl

_INDEX = {
    "files": {
        "C:/dl/One Pace/Season 01/1x01.mkv": {
            "series_id": "pp_onepacee", "series_name": "One Pace",
            "episode_id": "RO_1", "season": "1", "episode": "1",
            "video": {"id": "RO_1", "season": 1, "episode": 1, "title": "Ep one"},
        },
        "C:/dl/One Pace/Season 02/2x01.mkv": {
            "series_id": "pp_onepacee", "series_name": "One Pace",
            "episode_id": "OT_1", "season": "2", "episode": "1",
            "video": {"id": "OT_1", "season": 2, "episode": 1, "title": "Ep two"},
        },
    },
    "series": {"One Pace": {"name": "One Pace",
                            "description": "The show's own words.",
                            "seasons": [{"season": 1, "name": "Romance Dawn"},
                                        {"season": 2, "name": "Orange Town"}]}},
}
_keep = (_dl.read_index, _dl.enabled, _dl.xbmcvfs.exists,
         er._fetch_provider_meta, er._watched.cache_total)
_dl.read_index, _dl.enabled = (lambda: _INDEX), (lambda: True)
_dl.xbmcvfs.exists = lambda p: False
WATCHED[0] = set()

_totals = []
er._watched.cache_total = lambda sid, total: _totals.append((sid, total))

er._fetch_provider_meta = lambda ct, sid: None
_rec = harness.recorder()
er.list_seasons({"catalog_type": "series", "video_id": "pp_onepacee"})
_offline = [li.label for _, li, _ in _rec.directories]
print(f"  offline season list -> {_offline}, closed {_rec.ended}")
assert _offline == ["Season 1", "Season 2"], _offline
assert _rec.ended == [True], "an offline list still has to close cleanly"

# The index holds two episodes; the real show has hundreds. Writing that count
# would still be wrong once the connection came back.
print(f"  totals written to the watched store: {_totals or 'none'}")
assert not _totals, "an offline count would outlive the connection coming back"

_rec = harness.recorder()
er.list_episodes({"catalog_type": "series", "video_id": "pp_onepacee",
                  "season": "1"})
print(f"  offline episode list -> {len(_rec.directories)} row(s)")
assert len(_rec.directories) == 1, "only the episode actually held belongs here"
assert _rec.ended == [True]

# Seasons carry their names offline too: the index kept the whole array.
S["season_labels"] = "1"
_rec = harness.recorder()
er.list_seasons({"catalog_type": "series", "video_id": "pp_onepacee"})
_named = [li.label for _, li, _ in _rec.directories]
print(f"  offline, named -> {_named}")
assert _named == ["Romance Dawn", "Orange Town"], _named
S["season_labels"] = "0"

# Nothing held, nothing to show: it must not draw an empty list as success.
er._fetch_provider_meta = lambda ct, sid: None
_rec = harness.recorder()
er.list_seasons({"catalog_type": "series", "video_id": "pp_nothing"})
print(f"  nothing held -> {len(_rec.directories)} row(s), closed {_rec.ended}")
assert not _rec.directories and _rec.ended == [False], _rec.ended

# Back online the real count is written again.
er._fetch_provider_meta = lambda ct, sid: ONEPACE
_totals.clear()
_rec = harness.recorder()
er.list_seasons({"catalog_type": "series", "video_id": "pp_onepacee"})
print(f"  back online -> {len(_rec.directories)} season(s), totals {_totals}")
assert len(_rec.directories) > 2, "the provider's own list should be used again"
assert _totals and _totals[0][1] > 100, _totals

(_dl.read_index, _dl.enabled, _dl.xbmcvfs.exists,
 er._fetch_provider_meta, er._watched.cache_total) = _keep
META[0] = ONEPACE
print("  held when away, the provider's when back, and no count either way  OK")

print()
print("=== a file deleted elsewhere loses its mark on the next list ===")
from lib import downloads as _dl

_two = S1[:2]
_index = {"files": {
    f"C:/dl/One Pace/Season 01/{_i}.mkv": {
        "series_id": "pp_onepacee", "series_name": "One Pace",
        "episode_id": _eid, "season": "1", "episode": str(_i + 1),
    } for _i, _eid in enumerate(_two)
}, "series": {}}
# The second one was deleted in Explorer; the folder is still there.
_live = {"C:/dl/One Pace/Season 01/0.mkv", "C:/dl/One Pace/Season 01/"}

_held_keep = (_dl.read_index, _dl.xbmcvfs.exists, _dl.enabled,
              _dl.downloaded_ids, _dl.outdated_ids, _dl.imported_ids)
_dl.read_index = lambda: _index
_dl.enabled = lambda: True
_dl.xbmcvfs.exists = lambda p: p in _live
_dl.downloaded_ids = lambda: set(_two)
_dl.outdated_ids = lambda: set()
_dl.imported_ids = lambda: set()

_rows = [li.label for _, li, _ in episodes(1).directories]
_marked = [l for l in _rows if "[D]" in l]
print(f"  index claims 2 held, {len(_marked)} still marked")
assert len(_marked) == 1, _marked
assert _marked[0].count("[D]") == 1, _marked

# Put the file back and the mark returns: nothing was forgotten, only unmarked.
_live.add("C:/dl/One Pace/Season 01/1.mkv")
_rows = [li.label for _, li, _ in episodes(1).directories]
print(f"  with the file back -> {len([l for l in _rows if '[D]' in l])} marked")
assert len([l for l in _rows if "[D]" in l]) == 2, "the row was dropped, not unmarked"

(_dl.read_index, _dl.xbmcvfs.exists, _dl.enabled,
 _dl.downloaded_ids, _dl.outdated_ids, _dl.imported_ids) = _held_keep
print("  unmarked while it is gone, marked again when it is back  OK")

print()
print("all assertions passed")
