from typing import Optional

import xbmc

from .provider_api import SERIES_STUDIOS, _parse_air_date, _parse_release_year
from .utils import build_url

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


def _upgrade_cast_photo(url: Optional[str]):
    """TMDb's face crop is tiny; Kodi looks better with the taller one."""
    return (url or "").replace("/w132_and_h132_face/", "/w276_and_h350_face/")


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


def _cast_list(meta: dict):
    """Actors with character and photo when the feed carries them."""
    extras = (meta.get("app_extras") or {}).get("cast") or []
    if extras:
        return [
            xbmc.Actor(e["name"], e.get("character") or "", i,
                       _upgrade_cast_photo(e.get("photo")))
            for i, e in enumerate(extras) if e.get("name")
        ]
    return [xbmc.Actor(n, order=i) for i, n in enumerate(meta.get("cast") or ()) if n]


def _trailer_url(meta: dict):
    """Our own action, so the button shows even without the YouTube add-on."""
    source = next((t.get("source") for t in meta.get("trailers") or () if t.get("source")), None)
    return build_url("play_trailer", ytid=source) if source else None


def _set_episode_rating(tags, video: dict):
    """Per-episode rating, if the feed ever carries one. Zero means unrated."""
    for key in ("rating", "imdbRating"):
        try:
            rating = float(video.get(key))
        except (TypeError, ValueError):
            continue
        if rating:
            tags.setRating(rating)
            return


def _set_show_tags(tags, meta: dict, premiered: bool = True, trailer: bool = True,
                   actors=None):
    """Series-level tags that only the full meta carries."""
    age_rating = meta.get("ageRating")
    if age_rating:
        tags.setMpaa(age_rating)

    if premiered:
        air_date = _parse_air_date(meta)
        if air_date:
            tags.setPremiered(air_date)

    country = meta.get("country")
    if country:
        tags.setCountries([c.strip() for c in str(country).split(",") if c.strip()])

    status = meta.get("status")
    if status:
        tags.setTvShowStatus(status)

    writer = meta.get("writer")
    if writer:
        tags.setWriters(writer if isinstance(writer, list) else [writer])

    actors = _cast_list(meta) if actors is None else actors
    if actors:
        tags.setCast(actors)

    if trailer:
        url = _trailer_url(meta)
        if url:
            tags.setTrailer(url)

    tags.setStudios(SERIES_STUDIOS)


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
    if poster:
        art.setdefault("poster", poster)
        art.setdefault("tvshow.poster", poster)
        art.setdefault("icon", poster)
        art.setdefault("thumb", poster)
    if background:
        art.setdefault("fanart", background)
        art.setdefault("landscape", background)
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


def _set_episode_art(list_item, video: dict, meta: dict,
                     season_poster: Optional[str] = None):
    episode_thumb = _upgrade_metahub_url(video.get("thumbnail"))
    show_poster = _upgrade_metahub_url(meta.get("poster"))
    poster = _upgrade_metahub_url(season_poster) or show_poster
    background = _upgrade_metahub_url(meta.get("background")) or show_poster
    logo = meta.get("logo") or None

    art = {
        "thumb": episode_thumb or poster,
        "landscape": episode_thumb or background,
        "icon": episode_thumb or poster or "DefaultAddonNone.png",
        # Poster too, so poster-style views still show the episode thumb.
        "poster": episode_thumb or poster,
        "season.poster": poster,
        "tvshow.poster": show_poster,
        "fanart": background,
    }
    if logo:
        art["clearlogo"] = logo
        art["tvshow.clearlogo"] = logo
    list_item.setArt({key: value for key, value in art.items() if value})


def _set_season_art(list_item, meta: dict, season_thumbnail: Optional[str]):
    show_poster = _upgrade_metahub_url(meta.get("poster"))
    poster = _upgrade_metahub_url(season_thumbnail) or show_poster
    background = _upgrade_metahub_url(meta.get("background")) or show_poster
    logo = meta.get("logo") or None

    art = {
        "thumb": poster,
        "poster": poster,
        "season.poster": poster,
        "tvshow.poster": show_poster,
        "icon": poster or "DefaultAddonNone.png",
        "landscape": background,
        "fanart": background,
    }
    if logo:
        art["clearlogo"] = logo
        art["tvshow.clearlogo"] = logo
    list_item.setArt({key: value for key, value in art.items() if value})


def _stream_tagline(video_info: dict):
    parts = (video_info.get(key) for key in _TAGLINE_KEYS)
    return " | ".join(part for part in parts if part)
