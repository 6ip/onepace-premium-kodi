"""Shared setup for the suites: fake Kodi, the add-on on sys.path, fixtures."""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ADDON = HERE.parent / "plugin.video.onepacepremium"
FIXTURES = HERE / "fixtures"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def setup(settings=None, handle="1", query="?"):
    """Install the stubs and return the mutable settings store."""
    import kodistub
    store = kodistub.install(ADDON, FIXTURES, settings)
    sys.argv = ["plugin://plugin.video.onepacepremium/", handle, query]
    return store


def meta(name):
    """A frozen provider meta: onepace, muhnpace or kuma."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["d"]


def state(name):
    """The frozen watched.json / bookmarks.json."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def watched_ids(series_id="pp_onepacee"):
    return set(state("watched").get(series_id, []))


def episodes(meta_dict, season=None):
    """Videos in listing order, optionally for one season."""
    vids = [v for v in meta_dict.get("videos", ()) if v.get("season") is not None]
    if season is not None:
        vids = [v for v in vids if v.get("season") == season]
    return sorted(vids, key=lambda v: (v.get("season") or 0, v.get("episode") or 0))


def recorder():
    import kodistub
    kodistub.recorder.reset()
    return kodistub.recorder


def dialog():
    """Queues that decide what the next dialogs answer."""
    import kodistub
    d = kodistub.Dialog
    d.select_answers, d.multiselect_answers = [], []
    d.yesno_answers, d.browse_answers, d.inputs = [], [], []
    return d
