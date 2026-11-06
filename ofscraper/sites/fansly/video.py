"""Choosing and reading Fansly video streams.

A Fansly video offers up to three deliveries:
- direct: the media's own MP4 file (``locationId`` 1). Fansly caps it at 720
  on the short side.
- HLS: a ``application/vnd.apple.mpegurl`` variant (``locationId`` 102).
- DASH: a ``application/dash+xml`` variant (``locationId`` 103).

The largest rendition wins; on a tie direct beats HLS, which beats DASH.
"""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urljoin, urlparse

DIRECT, HLS, DASH = "direct", "hls", "dash"
_RANK = {DIRECT: 0, HLS: 1, DASH: 2}
HLS_MIME = "application/vnd.apple.mpegurl"
DASH_MIME = "application/dash+xml"
_LOCATION_IDS = {DIRECT: "1", HLS: "102", DASH: "103"}
_SIGNED = ("Policy", "Key-Pair-Id", "Signature")


class StreamError(Exception):
    """A stream that can't be downloaded; the message says why."""


class EmptyMasterPlaylist(StreamError):
    pass


class ProtectedStream(StreamError):
    pass


@dataclass
class Candidate:
    kind: str
    url: str
    width: int = 0
    height: int = 0
    cookies: dict = field(default_factory=dict)
    variant_id: str = None

    @property
    def area(self) -> int:
        return int(self.width or 0) * int(self.height or 0)

    @property
    def is_stream(self) -> bool:
        return self.kind in (HLS, DASH)


def _meta(obj) -> dict:
    raw = obj.get("metadata")
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def cloudfront_cookies(location: dict) -> dict:
    """CloudFront cookies for a location: its ``metadata``, else its URL query."""
    meta = location.get("metadata")
    source = meta if isinstance(meta, dict) else {}
    if not all(source.get(k) for k in _SIGNED):
        source = dict(parse_qsl(urlparse(location.get("location") or "").query))
    return {f"CloudFront-{k}": source[k] for k in _SIGNED if source.get(k)}


def _location(obj, kind):
    for loc in obj.get("locations") or []:
        if str(loc.get("locationId")) == _LOCATION_IDS[kind] and loc.get("location"):
            return loc
    return None


def _size(obj) -> tuple:
    """Largest rendition size: ``metadata.variants`` if listed, else width/height."""
    best = (int(obj.get("width") or 0), int(obj.get("height") or 0))
    for r in _meta(obj).get("variants") or []:
        try:
            w, h = int(r.get("w") or 0), int(r.get("h") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if w * h > best[0] * best[1]:
            best = (w, h)
    return best


def candidates(media: dict) -> list:
    """Downloadable deliveries of a raw Fansly Media object.

    The direct file is the media's own ``locationId`` 1, the URL the scraper
    stores as ``files.full.url`` and the normal downloader fetches.
    """
    out = []
    if not media:
        return out

    def add(obj, kind):
        loc = _location(obj, kind)
        if not loc:
            return
        w, h = _size(obj) if kind != DIRECT else (obj.get("width"), obj.get("height"))
        out.append(Candidate(kind, loc["location"], int(w or 0), int(h or 0),
                             cloudfront_cookies(loc), str(obj.get("id") or "") or None))

    if (media.get("mimetype") or "").lower().startswith("video/"):
        add(media, DIRECT)
    for v in media.get("variants") or []:
        mime = (v.get("mimetype") or "").lower()
        if mime == HLS_MIME:
            add(v, HLS)
        elif mime == DASH_MIME:
            add(v, DASH)
    return out


def select_best(media: dict):
    """Best candidate, or None when the media has nothing downloadable."""
    found = candidates(media)
    if not found:
        return None
    return max(found, key=lambda c: (c.area, -_RANK[c.kind]))


def raw_media(ele):
    """The raw Fansly Media object a scraped item carries, or None."""
    try:
        fansly = (ele.media or {}).get("fansly")
    except AttributeError:
        return None
    return fansly.get("media") if isinstance(fansly, dict) else None


def uses_stream(ele) -> bool:
    """True only for a Fansly item, on a Fansly run, whose best delivery is HLS or DASH."""
    raw = raw_media(ele)
    if not raw:
        return False
    from ofscraper import sites

    if not sites.is_fansly():
        return False
    best = select_best(raw)
    return bool(best and best.is_stream)


_RES = re.compile(r"RESOLUTION=(\d+)x(\d+)", re.I)
_BW = re.compile(r"(?<![-A-Z])BANDWIDTH=(\d+)", re.I)


def master_renditions(text: str, base_url: str) -> list:
    """(width, height, bandwidth, absolute url) for each rendition in a master playlist."""
    out = []
    lines = [line.strip() for line in (text or "").splitlines()]
    for i, line in enumerate(lines):
        if not line.upper().startswith("#EXT-X-STREAM-INF"):
            continue
        uri = next((x for x in lines[i + 1:] if x and not x.startswith("#")), None)
        if not uri:
            continue
        res = _RES.search(line)
        bw = _BW.search(line)
        w, h = (int(res.group(1)), int(res.group(2))) if res else (0, 0)
        out.append((w, h, int(bw.group(1)) if bw else 0, urljoin(base_url, uri)))
    return out


def best_rendition_url(text: str, base_url: str) -> str:
    """URL of the largest rendition. A plain media playlist is used as it is."""
    renditions = master_renditions(text, base_url)
    if renditions:
        return max(renditions, key=lambda r: (r[0] * r[1], r[2]))[3]
    if "#EXTINF" in (text or ""):
        return base_url
    raise EmptyMasterPlaylist("HLS master playlist lists no renditions; nothing to download")


_PROTECTION = re.compile(r"<\s*(?:[\w-]+:)?ContentProtection\b")


def dash_is_protected(mpd_text: str) -> bool:
    return bool(_PROTECTION.search(mpd_text or ""))


def ffmpeg_command(ffmpeg, url, out_path, headers: dict, cookies: dict) -> list:
    """ffmpeg stream copy of an HLS/DASH URL into one MP4.

    ``headers`` and ``cookies`` go on every HTTP request ffmpeg makes,
    playlist/manifest and segments alike.
    """
    headers = dict(headers or {})
    user_agent = headers.pop("User-Agent", None) or headers.pop("user-agent", None)
    lines = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    if cookies:
        lines += "Cookie: " + "; ".join(f"{k}={v}" for k, v in cookies.items()) + "\r\n"
    cmd = [str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if user_agent:
        cmd += ["-user_agent", user_agent]
    if lines:
        cmd += ["-headers", lines]
    cmd += ["-i", url, "-c", "copy", "-f", "mp4", str(out_path)]
    return cmd
