"""Stream preferences and the labels we build from them."""
import harness

S = harness.setup({"preferred_service": "", "preferred_version": "0"})
from lib.episode_routes import _preferred_streams, _binge_part, _episode_label

GROUPS = ["onepace|rd|standard", "onepace|rd|extended",
          "onepace|p2p|standard", "onepace|pm|extended", ""]

print("=== no preference means every stream ===")
print(f"  {len(_preferred_streams(GROUPS))} of {len(GROUPS)}")
assert _preferred_streams(GROUPS) == list(range(len(GROUPS)))

print()
print("=== narrowing by service and version ===")
for service, version, expect in [("rd", "0", [0, 1]), ("p2p", "0", [2]),
                                 ("", "1", [0, 2]), ("", "2", [1, 3]),
                                 ("rd", "2", [1])]:
    S["preferred_service"], S["preferred_version"] = service, version
    got = _preferred_streams(GROUPS)
    print(f"  service={service or '-':<4} version={version} -> {got}")
    assert got == expect, (service, version, got)

print()
print("=== a preference that matches nothing is ignored, never leaves you stuck ===")
S["preferred_service"], S["preferred_version"] = "nosuch", "0"
print(f"  unknown service -> {len(_preferred_streams(GROUPS))} streams")
assert _preferred_streams(GROUPS) == list(range(len(GROUPS)))

S["preferred_service"] = ""
only_standard = ["onepace|rd|standard", "onepace|p2p|standard"]
S["preferred_version"] = "2"
got = _preferred_streams(only_standard)
print(f"  extended wanted, only standard exists -> {got} (falls back)")
assert got == [0, 1]

S["preferred_service"], S["preferred_version"] = "", "0"
assert _preferred_streams([]) == [], "no streams must not crash"
print("  an empty stream list is handled")

print()
print("=== bingeGroup parsing survives junk ===")
for group, idx, expect in [("onepace|rd|extended", 1, "rd"),
                           ("onepace|rd|extended", 2, "extended"),
                           ("onepace", 1, ""), ("", 1, ""), (None, 2, "")]:
    got = _binge_part(group, idx)
    print(f"  {str(group):<22}[{idx}] -> {got!r}")
    assert got == expect

print()
print("=== episode labels ===")
S["episode_title_format"] = "0"
cases = [("Romance Dawn", 1, 1, "RO_1", "1x01. Romance Dawn"),
         ("Special", 0, 3, "pp_fan_3", "Special"),
         ("Complete", 17, 1, "pp_COMPLETE_EN1", "Complete")]
for title, season, ep, eid, expect in cases:
    got = _episode_label(title, season, ep, eid)
    print(f"  s{season} {eid:<16} -> {got!r}")
    assert got == expect, (eid, got)

S["episode_title_format"] = "1"
plain = _episode_label("Romance Dawn", 1, 1, "RO_1")
print(f"  title-only setting -> {plain!r}")
assert plain == "Romance Dawn"

print()
print("all assertions passed")
