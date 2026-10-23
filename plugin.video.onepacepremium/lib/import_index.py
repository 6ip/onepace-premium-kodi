"""What the streams repo knows about its own releases, in one request."""
import io
import json
import re
import tarfile

from .utils import log, session

# The whole repo as one tarball: 841 files, about 250 KB, one request.
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
    """Lookups from (episode_id, stream) pairs."""
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
    """The release list and the repo's config.json, from one download."""
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
                    # Only the fields build() reads, so the cache stays small.
                    entries.append([episode_id,
                                    {"filename": stream.get("filename") or "",
                                     "videoSize": stream.get("videoSize") or 0}])
    log(f"[import] read {len(entries)} releases from the streams repo")
    return entries, config


def resolve(filename, size, index, arc_map, aliases=None):
    """Which episode a file holds, and on what evidence."""
    from . import import_match

    # Read the name even when the size settles it: the cut is wanted.
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
