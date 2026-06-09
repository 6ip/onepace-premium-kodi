import re
from typing import Optional, Tuple
from urllib import parse

from . import cache as _cache
from .utils import fetch_data, get_catalog_provider_url

SERIES_CATALOG_EXCLUDED_NAMES = {"last videos", "calendar videos"}

_YEAR_RE = re.compile(r"\d{4}")
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


def _fetch_provider_meta(catalog_type: str, video_id: str):
    _, provider_base_url = _provider_context()
    url = _compose_url(
        provider_base_url,
        f"meta/{_provider_path(catalog_type)}/{_provider_path(video_id)}.json",
    )
    cached = _cache.get(url)
    if cached is not None:
        return cached
    response = fetch_data(url)
    meta = response["meta"] if response else None
    if meta is not None:
        _cache.set(url, meta, 21600)
    return meta


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
