"""Reading an episode out of a filename, against names that really exist.

The cases below are the files and torrents sitting in a real folder of
downloads from torrents, pixeldrain and Google Drive — four release groups,
two qualities, dubs, and arcs released as a single episode. Every id this
produces is checked against the 841 the streams repo actually publishes, so a
plausible-looking wrong answer cannot pass.
"""
import json
import pathlib

import harness

harness.setup()

from lib import import_match as m

FIXTURES = pathlib.Path(harness.FIXTURES)
CONFIG = json.loads((FIXTURES / "streams_config.json").read_text(encoding="utf-8"))
ARC_MAP, ALIASES = CONFIG["ARC_MAP"], CONFIG["ALIASES"]
REAL_IDS = set(json.loads((FIXTURES / "stream_ids.json").read_text(encoding="utf-8")))

# filename -> (the id the repo publishes for it, which cut it is)
VIDEOS = [
    ("[One Pace][3-5] Romance Dawn 03 [720p][En CC][727376CF].mp4", "RO_3", ""),
    ("[One Pace][303] Long Ring Long Land 00 [720p][En Dub][CC513D00].mp4", "LR_0", ""),
    ("[Muhn Pace] Dressrosa 01.mp4", "MUHN_DR_1", ""),
    ("[Muhn Pace] Enies Lobby - 14.mp4", "MUHN_EN_14", ""),
    ("[Muhn Pace] Post Enies Lobby 05.mp4", "MUHN_PEN_5", ""),
    ("[Muhn Pace] Wano 91.mp4", "MUHN_WA_91", ""),
    ("[0998] Onigashima 18 - Ancient Types.mp4", "ONIG_18", ""),
    # The "+" is the longer cut, not a different arc: this file is byte-exact
    # with ONIG_9's Extended stream.
    ("[986] Onigashima+ 09 - My Name.mp4", "ONIG_9", "extended"),
    # Each group writes the same thing in its own order.
    ("[One Pace][94-95] Arlong Park 10 Extended [1080p][4B882BCA].mkv", "AR_10", "extended"),
    ("[Onigashima Paced][986] Onigashima Extended 09 [1080p].mp4", "ONIG_9", "extended"),
]

# Kuma numbers its episodes in a flat sequence, so the title identifies them.
KUMA = [
    ("One Piece - 1131 [Kuma Cut].mp4", "Kuma Cut 1131"),
    ("[DUB] One Piece - 1127+8 [Shaved Egghead].mp4", "Shaved Egghead 1127+8"),
]

# Named in the official torrents. An arc released as one episode carries no
# number, so the name alone cannot say which episode it is — that needs the
# catalog, and guessing "1" would be a confident wrong answer.
NEEDS_CATALOG = [
    "[One Pace][1-7] Romance Dawn [1080p].torrent",
    "[One Pace] One Piece Fan Letter [1080p][59510B34].mkv.torrent",
    "[One Pace][35-75] The Adventures of Buggy's Crew [1080p][1177A2B6].mkv.torrent",
]

print("=== every file in the folder lands on an id the repo publishes ===")
for name, want, cut in VIDEOS:
    parsed = m.parse(name)
    got = m.episode_id(parsed, ARC_MAP, ALIASES)
    print(f"  {name[:50]:<50} -> {got}{'  (' + cut + ')' if cut else ''}")
    assert got == want, (name, got, want)
    assert got in REAL_IDS, f"{got} is not a stream the repo has"
    assert parsed["variant"] == cut, (name, parsed["variant"], cut)

print()
print("=== the group decides the catalog, not the arc ===")
# "Dressrosa" is an arc in both One Pace and Muhn Pace. Reading the arc alone
# would file a Muhn Pace episode as the One Pace one of the same name.
muhn = m.parse("[Muhn Pace] Dressrosa 01.mp4")
onepace = m.parse("[One Pace][700-800] Dressrosa 01 [720p].mkv")
print(f"  [Muhn Pace] Dressrosa 01 -> {muhn['group']}, "
      f"{m.episode_id(muhn, ARC_MAP, ALIASES)}")
print(f"  [One Pace]  Dressrosa 01 -> {onepace['group']}, "
      f"{m.episode_id(onepace, ARC_MAP, ALIASES)}")
assert m.episode_id(muhn, ARC_MAP, ALIASES) == "MUHN_DR_1"
assert m.episode_id(onepace, ARC_MAP, ALIASES) == "DR_1"
assert {"MUHN_DR_1", "DR_1"} <= REAL_IDS

print()
print("=== Kuma is identified by the title the catalog uses ===")
for name, want in KUMA:
    parsed = m.parse(name)
    got = m.kuma_title(parsed)
    print(f"  {name[:46]:<46} -> {got!r}")
    assert parsed["group"] == m.KUMA and got == want, (name, got)
    # No arc, so there is no id to build from the name.
    assert m.episode_id(parsed, ARC_MAP, ALIASES) == ""

print()
print("=== an arc with no episode number says so, rather than guessing ===")
for name in NEEDS_CATALOG:
    parsed = m.parse(name.removesuffix(".torrent"))
    got = m.episode_id(parsed, ARC_MAP, ALIASES)
    print(f"  {name[:56]:<56} arc={parsed['arc']!r}")
    assert parsed["arc"], f"the arc should still be readable from {name!r}"
    assert got == "", f"{name!r} guessed {got!r} from a name with no number"

print()
print("=== the noise around the name is read, not tripped over ===")
one = m.parse("[One Pace][3-5] Romance Dawn 03 [720p][En CC][727376CF].mp4")
print(f"  chapters={one['chapters']!r} crc={one['crc']!r} tags={one['tags']}")
assert one["chapters"] == "3-5" and one["crc"] == "727376CF"
assert one["arc"] == "Romance Dawn" and one["episode"] == 3
# A chapter range is digits and a CRC is hex; only one of them is both.
assert m.parse("[One Pace][303] Long Ring Long Land 00 [1080p][E85B9E9D].mkv")["crc"] == "E85B9E9D"
assert m.parse("[One Pace][303] Long Ring Long Land 00 [1080p][E85B9E9D].mkv")["chapters"] == "303"
print("  a numeric chapter is never mistaken for a CRC  OK")

print()
print("=== an unreadable name is unreadable, not wrong ===")
for name in ["Holiday 2019.mp4", "random download (1).mkv", "", "One Piece 1045.mkv"]:
    parsed = m.parse(name)
    got = m.episode_id(parsed, ARC_MAP, ALIASES)
    print(f"  {name!r:28} -> group={parsed['group']!r} id={got!r}")
    assert got == "" or got in REAL_IDS, (name, got)

print()
print("=== a v2 release is its own special, never folded into the arc ===")
# "[One Pace] Romance Dawn v2" is special_RO, season 0 episode 5 — not part of
# Romance Dawn at all. Matching arc names by prefix would quietly file it as
# RO_something and overwrite a real episode.
v2 = m.parse("[One Pace][Romance Dawn v2] Romance Dawn v2 [1080p][5E77445F].mkv")
got = m.episode_id(v2, ARC_MAP, ALIASES)
print(f"  arc={v2['arc']!r} -> id={got!r} crc={v2['crc']!r}")
assert v2["arc"] == "Romance Dawn v2", v2["arc"]
assert got == "", f"a v2 release resolved to {got!r}"
assert m._arc_code("Romance Dawn v2", ARC_MAP, ALIASES) == "", "arcs must match exactly"

print()
print("=== two different episodes can share every readable part of a name ===")
# WC_1 and the April Fools fools17 differ only by CRC. A filename cannot tell
# them apart, so the resolver has to try the CRC before it trusts the name.
real = m.parse("[One Pace][823-824] Whole Cake Island 01 [720p][3F24A1D8].mkv")
fools = m.parse("[One Pace][823-824] Whole Cake Island 01 [720p][EDB72EE5].mp4")
same = {k: real[k] for k in ("group", "arc", "episode", "chapters", "variant")}
print(f"  identical: {same}")
print(f"  CRC only:  {real['crc']} vs {fools['crc']}")
assert all(real[k] == fools[k] for k in same), "these should look the same"
assert real["crc"] != fools["crc"], "the CRC is the only thing left to go on"
assert m.episode_id(real, ARC_MAP, ALIASES) == m.episode_id(fools, ARC_MAP, ALIASES) == "WC_1"
print("  so a name-only import would file the April Fools cut as WC_1  KNOWN")

print()
print("=== the group names are the ones the catalog actually serves ===")
# The series id is looked up by name, so a name that drifts imports nothing.
served = {v["name"] for v in json.loads(
    (FIXTURES / "catalog_series.json").read_text(encoding="utf-8"))["metas"]}
for group, name in sorted(m.SERIES_NAMES.items()):
    print(f"  {group:<8} -> {name!r}")
    assert name in served, f"the catalog has no series called {name!r}"
assert set(m.SERIES_NAMES.values()) == served, served - set(m.SERIES_NAMES.values())

print()
print("=== aliases in config.json are honoured both ways ===")
for arc, want in [("Alabasta", "Arabasta"), ("Whisky Peak", "Whiskey Peak")]:
    direct = m._arc_code(arc, ARC_MAP, ALIASES)
    other = m._arc_code(want, ARC_MAP, ALIASES)
    print(f"  {arc!r} -> {direct!r}   {want!r} -> {other!r}")
    assert direct and direct == other, (arc, direct, other)

print()
print("all assertions passed")
