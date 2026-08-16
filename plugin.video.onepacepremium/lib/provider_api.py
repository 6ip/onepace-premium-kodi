import re
import time
from typing import Optional, Tuple
from urllib import parse

from . import cache as _cache
from .utils import fetch_data, get_catalog_provider_url, get_setting, log

SERIES_CATALOG_EXCLUDED_NAMES = {"last videos", "calendar videos"}

# Not in the feed, but constant for the source series.
SERIES_STUDIOS = ["Toei Animation", "Fuji TV"]

_YEAR_RE = re.compile(r"\d{4}")
_AIR_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_CATALOG_PRIORITY_MAP = {"popular": 0, "new": 1, "featured": 2}
_PROVIDER_CONTEXT_CACHE: Optional[Tuple[str, str]] = None


def _compose_url(base_url: str, path: str):
    return parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _provider_context():
    global _PROVIDER_CONTEXT_CACHE
    if _PROVIDER_CONTEXT_CACHE is not None:
        return _PROVIDER_CONTEXT_CACHE

    configured = get_catalog_provider_url()
    if configured.endswith("/manifest.json"):
        context = (configured, configured[: -len("/manifest.json")])
    elif configured.endswith(".json"):
        context = (configured, configured.rsplit("/", 1)[0])
    else:
        context = (f"{configured}/manifest.json", configured)

    _PROVIDER_CONTEXT_CACHE = context
    return context


def _provider_path(value: str):
    return parse.quote(str(value), safe="")


def _fetch_provider_manifest():
    manifest_url, _ = _provider_context()
    cached = _cache.get(manifest_url)
    if cached is not None:
        return cached
    data = fetch_data(manifest_url)
    if data is not None:
        _cache.set(manifest_url, data, 86400)
    return data


def _fetch_catalog(url: str):
    cached = _cache.get(url)
    if cached is not None:
        return cached
    data = fetch_data(url)
    if data is not None:
        _cache.set(url, data, 86400)
    return data


def _meta_url(catalog_type: str, video_id: str):
    _, provider_base_url = _provider_context()
    return _compose_url(
        provider_base_url,
        f"meta/{_provider_path(catalog_type)}/{_provider_path(video_id)}.json",
    )


def _fetch_provider_meta(catalog_type: str, video_id: str):
    url = _meta_url(catalog_type, video_id)
    cached = _cache.get(url)
    if cached is not None:
        return cached
    response = fetch_data(url)
    meta = response["meta"] if response else None
    if meta is not None:
        _cache.set(url, meta, 21600)
    return meta


def _prefetch_metas(catalog_type: str, video_ids):
    """Warm the meta cache in parallel so the per-id calls below hit it."""
    _provider_context()  # resolve once here, not from the threads
    missing = [
        vid for vid in dict.fromkeys(video_ids)
        if vid and _cache.get(_meta_url(catalog_type, vid)) is None
    ]
    if len(missing) < 2:
        return
    from concurrent import futures
    started = time.time()
    with futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda vid: _fetch_provider_meta(catalog_type, vid), missing))
    log(f"[meta] prefetched {len(missing)} series in {time.time() - started:.2f}s")


def countable_episode_ids(meta: dict):
    """Episode ids that count toward totals — specials excluded when hidden."""
    keep_specials = get_setting("show_specials") != "false"
    return [
        v["id"] for v in meta.get("videos", ())
        if v.get("id") and (keep_specials or v.get("season") != 0)
    ]


def _catalog_url(catalog_type: str, catalog_id: str, extra: str):
    _, provider_base_url = _provider_context()
    return _compose_url(
        provider_base_url,
        f"catalog/{_provider_path(catalog_type)}/{_provider_path(catalog_id)}/{extra}.json",
    )


def _catalog_specs(manifest: dict, catalog_type: str):
    # "anime" catalogs are treated as "series" so they use the proven series pipeline
    match_types = {catalog_type, "anime"} if catalog_type == "series" else {catalog_type}
    specs = []
    for catalog in manifest.get("catalogs", ()):
        if catalog["type"] not in match_types:
            continue

        catalog_id = catalog.get("id")
        if not catalog_id:
            continue

        catalog_name = catalog.get("name") or catalog_id
        if (
            catalog_type == "series"
            and catalog_name.strip().lower() in SERIES_CATALOG_EXCLUDED_NAMES
        ):
            continue

        has_search = any(e.get("name") == "search" for e in catalog.get("extra", ()))
        specs.append({"id": catalog_id, "name": catalog_name, "has_search": has_search})
    return specs


def _catalog_priority(name: str):
    return _CATALOG_PRIORITY_MAP.get(name.strip().lower(), 100)


def _parse_release_year(release_info):
    if not release_info:
        return None
    match = _YEAR_RE.search(str(release_info))
    return int(match.group()) if match else None


def _parse_air_date(video):
    """Episode air date as YYYY-MM-DD, which is what Kodi's info tags expect.

    The feed carries full ISO timestamps ("2026-06-28T14:15:00.000Z"); Kodi wants
    just the date part. firstAired wins over released when both are present.
    """
    for field in ("firstAired", "released"):
        match = _AIR_DATE_RE.match(str(video.get(field) or ""))
        if match:
            return match.group(1)
    return None


def _parse_runtime_seconds(video):
    """Episode runtime in seconds. The feed carries minutes as a string."""
    try:
        minutes = int(str(video.get("runtime") or "").strip())
    except ValueError:
        return None
    return minutes * 60 if minutes > 0 else None
