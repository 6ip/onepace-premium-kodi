"""Reading a One Pace episode out of whatever a file happens to be called.

Pure text work: no Kodi, no network, no disk. The arc map comes from the
streams repo's config.json and is passed in, so this stays testable against a
folder of real filenames.

Four release groups number their episodes differently, so which group a name
belongs to has to be read before the arc means anything — an arc code alone
would import a Muhn Pace file as the One Pace episode of the same name.
"""
import os
import re

ONEPACE, MUHN, ONIG, KUMA = "onepace", "muhn", "onig", "kuma"

# The catalog's own names for them, which is how the series id is looked up at
# import time — exactly as a rescan does it.
SERIES_NAMES = {
    ONEPACE: "One Pace",
    MUHN: "Muhn Pace",
    ONIG: "Onigashima Paced",
    KUMA: "Shaved Egghead & Kuma Cut",
}

# The tag a group puts in brackets. Order matters: the Kuma releases carry a
# cut name where the others carry a group name.
_GROUP_TAGS = (
    (ONEPACE, "one pace"),
    (MUHN, "muhn pace"),
    (ONIG, "onigashima paced"),
    (KUMA, "kuma cut"),
    (KUMA, "shaved egghead"),
)

_CUTS = {"kuma cut": "Kuma Cut", "shaved egghead": "Shaved Egghead"}

# Onigashima Paced numbers its main run flat but gives a later arc its own
# code, and config.json names neither. Written out rather than guessed at: an
# arc this does not list is one we would get wrong.
_ONIG_ARCS = {"onigashima": "", "endofwano": "WA"}

# A chapter range: [3-5], [303], [0998], [405-406].
_CHAPTERS = re.compile(r"^\d+(?:-\d+)?$")
# The CRC32 One Pace puts on a release.
_CRC = re.compile(r"^[0-9A-Fa-f]{8}$")
# Quality, and anything else the groups tack on.
_TAGS = re.compile(r"^(?:\d{3,4}p|dub|en\s?cc|en\s?dub|sub|hevc|x26[45]|10bit)$", re.I)

# "Romance Dawn 03", "Enies Lobby - 14", "One Piece - 1127+8", "End of Wano
# 03.5". The number is optional: a one-episode arc is released under the arc
# name alone.
_TRAILING_NUMBER = re.compile(
    r"^(?P<arc>.+?)(?:\s*-\s*|\s+)(?P<ep>\d+(?:\.\d+)?(?:\+\d+)?)$")

VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm")


def _normalise(arc):
    """The shape config.json's lowercase keys are written in."""
    return re.sub(r"[^a-z0-9]", "", (arc or "").lower())


def _canon(number):
    """"03" and "03.5" as the id itself writes them: "3" and "3.5"."""
    whole, _, fraction = number.partition(".")
    whole = str(int(whole or 0))
    return f"{whole}.{fraction}" if fraction else whole


def _split_number(text):
    """An arc and its trailing episode number, or the text and None."""
    found = _TRAILING_NUMBER.match(text)
    if not found:
        return text, None
    return found.group("arc").strip(), found.group("ep")


def parse(filename):
    """What a filename says, before anything is looked up.

    Returns a dict that is always the same shape; group is "" when nothing in
    the name identifies one.
    """
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    brackets = re.findall(r"\[([^\]]*)\]", stem)
    leftover = re.sub(r"\[[^\]]*\]", " ", stem)
    leftover = re.sub(r"\s+", " ", leftover).strip(" -_.")

    group, cut, chapters, crc, tags = "", "", "", "", []
    for raw in brackets:
        token = raw.strip()
        low = token.lower()
        matched = next((g for g, tag in _GROUP_TAGS if low == tag), "")
        if matched:
            group = group or matched
            if low in _CUTS:
                cut = _CUTS[low]
            continue
        if _CHAPTERS.match(token):
            chapters = chapters or token
            continue
        if _CRC.match(token) and not token.isdigit():
            crc = crc or token.upper()
            continue
        if _TAGS.match(token):
            tags.append(token)

    # One Pace writes "Arlong Park 10 Extended", Onigashima writes "Onigashima
    # Extended 09", and a shorthand release writes "Onigashima+". All three are
    # the longer cut, which the lists keep beside the standard one.
    extended = bool(re.search(r"(?i)\bextended\b", leftover))
    if extended:
        leftover = re.sub(r"(?i)\s*\bextended\b\s*", " ", leftover).strip(" -_.")

    episode, piece, title, token = None, "", "", ""
    arc, number = _split_number(leftover)
    if number is None:
        # "Onigashima 18 - Ancient Types": the number is not at the end until
        # the episode title is taken off. Tried second, because "Enies Lobby
        # - 14" puts the number itself after that same separator.
        before, sep, after = leftover.rpartition(" - ")
        if sep:
            head, trailing = _split_number(before)
            if trailing is not None:
                arc, number, title = head, trailing, after.strip()
    if number is not None:
        if group == KUMA:
            # Their numbers are One Piece episodes, not a place in a season.
            piece = number
        else:
            # Kept as text too: one arc numbers an episode 03.5, and the id
            # carries that fraction.
            token = _canon(number.split("+")[0])
            episode = int(float(token))

    if arc.endswith("+"):
        extended = True
        arc = arc.rstrip("+").strip()
    if not group and _normalise(arc) in _ONIG_ARCS:
        group = ONIG

    return {"group": group, "arc": arc, "episode": episode, "number": token,
            "piece": piece, "cut": cut, "title": title, "chapters": chapters,
            "crc": crc, "tags": tuple(tags),
            "variant": "extended" if extended or any(
                "extend" in t.lower() for t in tags) else ""}


def episode_id(parsed, arc_map, aliases=None):
    """The provider's own id for this file, or "" when the name cannot say.

    Kuma is the one group whose id carries no arc: its episodes are a flat
    sequence, so the title is what identifies them and the resolver matches on
    kuma_title() instead.
    """
    group, number = parsed["group"], parsed["number"]
    if not number:
        return ""
    if group == ONIG:
        code = _ONIG_ARCS.get(_normalise(parsed["arc"]))
        if code is None:
            return ""
        return f"ONIG_{code}_{number}" if code else f"ONIG_{number}"
    if group in (ONEPACE, MUHN):
        code = _arc_code(parsed["arc"], arc_map, aliases)
        if not code:
            return ""
        return f"MUHN_{code}_{number}" if group == MUHN else f"{code}_{number}"
    return ""


def _arc_code(arc, arc_map, aliases=None):
    """config.json keeps both a pretty name and a squashed one; try both."""
    if not arc:
        return ""
    for key in (arc, _normalise(arc)):
        if key in arc_map:
            return arc_map[key]
    for name, other in (aliases or {}).items():
        if _normalise(name) == _normalise(arc):
            return _arc_code(other, arc_map)
        if _normalise(other) == _normalise(arc):
            return _arc_code(name, arc_map)
    return ""


def kuma_title(parsed):
    """What the catalog calls this one: "Kuma Cut 1131", "Shaved Egghead 1127+8"."""
    if parsed["group"] != KUMA or not parsed["cut"] or not parsed["piece"]:
        return ""
    return f"{parsed['cut']} {parsed['piece']}"
