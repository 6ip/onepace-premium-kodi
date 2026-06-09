from typing import Optional

from .provider_api import _parse_release_year

_TAGLINE_KEYS = (
    "videoInfo",
    "audioInfo",
    "qualityInfo",
    "groupInfo",
    "seedersInfo",
    "sizeInfo",
    "trackerInfo",
    "languagesInfo",
)


def _upgrade_metahub_url(url: Optional[str]):
    if url and "/poster/small/" in url:
        return url.replace("/poster/small/", "/poster/medium/")
    return url or None


def _set_ids(tags, stremio_id: str):
    if stremio_id.startswith("tt"):
        tags.setIMDBNumber(stremio_id)
        tags.setUniqueID(stremio_id, type="imdb")
    else:
        tags.setUniqueID(stremio_id, type="onepacepremium")


def _set_video_tags(tags, meta: dict, title: str):
    tags.setTitle(title)

    description = meta.get("description")
    if description:
        tags.setPlot(description)

    imdb_rating = meta.get("imdbRating")
    if imdb_rating:
        try:
            tags.setRating(float(imdb_rating))
        except (TypeError, ValueError):
            pass

    release_year = _parse_release_year(meta.get("releaseInfo"))
    if release_year:
        tags.setYear(release_year)

    genres = meta.get("genres")
    if genres:
        tags.setGenres(genres)


def _build_art(
    primary: Optional[str], poster: Optional[str], background: Optional[str],
    logo: Optional[str] = None,
):
    art = {}
    if primary:
        art["thumb"] = primary
        art["poster"] = primary
        art["icon"] = primary
        art["fanart"] = primary
        art["landscape"] = primary
        art["banner"] = primary
    if poster:
        art.setdefault("poster", poster)
        art.setdefault("icon", poster)
        art.setdefault("thumb", poster)
    if background:
        art.setdefault("fanart", background)
        art.setdefault("landscape", background)
        art.setdefault("banner", background)
    if logo:
        art["clearlogo"] = logo
        art["tvshow.clearlogo"] = logo
    # Prevent DefaultFolder.png from showing when no image is available
    art.setdefault("icon", "DefaultAddonNone.png")
    return art


def _set_art(list_item, meta: dict):
    poster = _upgrade_metahub_url(meta.get("poster"))
    background = _upgrade_metahub_url(meta.get("background")) or poster
    logo = meta.get("logo") or None
    art = _build_art(None, poster, background, logo)
    if art:
        list_item.setArt(art)


def _season_thumbnails(videos: list):
    thumbnails = {}
    for video in videos:
        season = video.get("season")
        thumbnail = video.get("thumbnail")
        if season is None or not thumbnail:
            continue

        episode_number = video.get("episode") or video.get("number") or 0
        current = thumbnails.get(season)
        if current is None or episode_number < current[0]:
            thumbnails[season] = (episode_number, thumbnail)

    return {season: value[1] for season, value in thumbnails.items()}


def _episode_number(video: dict):
    number = video.get("episode")
    if number is None:
        number = video.get("number")
    return number


def _set_episode_art(list_item, video: dict, meta: dict):
    episode_thumb = _upgrade_metahub_url(video.get("thumbnail"))
    poster = _upgrade_metahub_url(meta.get("poster"))
    background = _upgrade_metahub_url(meta.get("background"))
    logo = meta.get("logo") or None
    art = _build_art(episode_thumb, poster, episode_thumb or background or poster, logo)
    if art:
        list_item.setArt(art)


def _set_season_art(list_item, meta: dict, season_thumbnail: Optional[str]):
    season_thumb = _upgrade_metahub_url(season_thumbnail)
    poster = _upgrade_metahub_url(meta.get("poster"))
    background = _upgrade_metahub_url(meta.get("background")) or poster
    logo = meta.get("logo") or None
    art = _build_art(season_thumb, poster, background, logo)
    if art:
        list_item.setArt(art)


def _stream_tagline(video_info: dict):
    parts = (video_info.get(key) for key in _TAGLINE_KEYS)
    return " | ".join(part for part in parts if part)
