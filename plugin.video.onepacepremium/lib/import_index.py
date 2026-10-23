"""What the streams repo knows about its own releases, in one request.

A filename cannot always say which episode it is. Two One Pace episodes are
both called "Post-Enies Lobby 03", and an April Fools cut is named exactly like
the episode it parodies — so reading the name alone files five of the repo's
867 releases under the wrong episode.

What does say, with no collisions at all across those 867: the byte size, the
CRC a release carries in its own name, and the full name. So the repo's release
list is read once and those three become the keys. Everything else is a guess,
and says so.
"""
import io
import json
import re
import tarfile

from .utils import log, session

# The whole repo as one tarball: 841 files, about 250 KB, one request. Reading
# them one at a time would be 841.
ARCHIVE_URL = ("https://codeload.github.com/6ip/onepace-streams"
               "/tar.gz/refs/heads/main")

_CRC = re.compile(r"\[([0-9A-Fa-f]{8})\]")


def _crc_of(filename):
    """The CRC a release carries. A bracket of plain digits is a chapter."""
    found = [c.upper() for c in _CRC.findall(filename or "") if not c.isdigit()]
    return found[-1] if found else ""


def _key(filename):
    """A name with the punctuation and case taken out of the comparison."""
    return re.sub(r"[^a-z0-9]", "", (filename or "").lower())


def build(entries):
    """Lookups from (episode_id, stream) pairs.

    First writer wins, so a repo that ever did repeat a size or a CRC would
    keep the earlier episode rather than silently reassigning it.
    """
    index = {"size": {}, "crc": {}, "name": {}}
    for episode_id, stream in entries:
        filename = stream.get("filename") or ""
        where = (episode_id,
                 "extended" if "extended" in filename.lower() else "")
        size = stream.get("videoSize") or 0
        if size:
            index["size"].setdefault(int(size), where)
        crc = _crc_of(filename)
        if crc:
            index["crc"].setdefault(crc, where)
        if filename:
            index["name"].setdefault(_key(filename), where)
    return index


def fetch():
    """The release list and the repo's config.json, from one download.

    The list comes back rather than the index built from it: it is what a
    cache can hold, since the index itself is keyed by integers and tuples and
    neither survives a trip through JSON.
    """
    payload = session().get(ARCHIVE_URL, timeout=60).content
    entries, config = [], {}
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            # Drop the "<repo>-<branch>/" the archive wraps everything in.
            tail = member.name.split("/", 1)[-1]
            handle = archive.extractfile(member)
            if handle is None:
                continue
            try:
                body = json.loads(handle.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                log(f"[import] skipped {tail!r}, which did not read as JSON")
                continue
            if tail == "config.json":
                config = body
            elif tail.startswith("stream/"):
                episode_id = tail.rsplit("/", 1)[-1][:-len(".json")]
                for stream in body.get("streams", ()):
                    # Only the two fields build() reads, so a cache of this
                    # stays small.
                    entries.append([episode_id,
                                    {"filename": stream.get("filename") or "",
                                     "videoSize": stream.get("videoSize") or 0}])
    log(f"[import] read {len(entries)} releases from the streams repo")
    return entries, config


def resolve(filename, size, index, arc_map, aliases=None):
    """Which episode a file holds, and on what evidence.

    how is "size", "crc" or "name" when the repo itself confirms it, "guess"
    when only the shape of the filename says so, and "" when nothing does. A
    guess is still worth offering, but not worth acting on unasked.
    """
    from . import import_match

    # Always read the name, even when the size settles it: the caller still
    # wants the cut, the arc and a title to show.
    parsed = import_match.parse(filename)

    if size:
        found = index["size"].get(int(size))
        if found:
            return _answer(found, "size", parsed)

    if parsed["crc"]:
        found = index["crc"].get(parsed["crc"])
        if found:
            return _answer(found, "crc", parsed)

    found = index["name"].get(_key(filename))
    if found:
        return _answer(found, "name", parsed)

    guess = import_match.episode_id(parsed, arc_map, aliases)
    if guess:
        return _answer((guess, parsed["variant"]), "guess", parsed)
    return _answer(("", parsed["variant"]), "", parsed)


def _answer(where, how, parsed):
    return {"episode_id": where[0], "variant": where[1], "how": how,
            "parsed": parsed}
