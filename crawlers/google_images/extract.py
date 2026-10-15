"""Extract original candidates without depending on Google's generated CSS classes."""

import html
import json
import re
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class ImageCandidate:
    url: str
    source_url: str = ""
    title: str = ""
    width: int = 0
    height: int = 0

    def to_dict(self):
        return asdict(self)


def original_url(value):
    if not isinstance(value, str):
        return ""
    value = html.unescape(value.strip())
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
            return ""
        host = parts.hostname.lower()
        if host == "gstatic.com" or host.endswith(".gstatic.com") or host == "google.com" or host.endswith(".google.com") or host.endswith(".google.com.hk"):
            return ""
        if host.startswith(("encrypted-tbn", "tbn")) or host == "localhost":
            return ""
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    except ValueError:
        return ""


def search_url(keyword, config):
    params = {"q": keyword, "udm": "2", "hl": config["search_language"], "safe": "active" if config["safe_search"] else "off"}
    filters = []
    for key, prefix, aliases in (("google_size", "isz", {"large": "l", "medium": "m", "icon": "i"}), ("google_type", "itp", {}), ("google_color", "ic", {})):
        if config[key] != "any":
            filters.append(f"{prefix}:{aliases.get(config[key], config[key])}")
    if filters:
        params["tbs"] = ",".join(filters)
    return "https://www.google.com/search?" + urlencode(params)


class ResultParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and (href := attrs.get("href")):
            try:
                query = parse_qs(urlsplit(href).query)
                if url := original_url(query.get("imgurl", [""])[0]):
                    # parse_qs decodes once; do not corrupt signed URLs by unquoting again.
                    self.results.append(ImageCandidate(url, query.get("imgrefurl", [""])[0], attrs.get("aria-label", "")))
            except ValueError:
                pass
        for key in ("data-ou", "data-iurl"):
            if url := original_url(attrs.get(key)):
                self.results.append(ImageCandidate(url, attrs.get("data-ru", ""), attrs.get("alt", "")))


TUPLE = re.compile(r'\[\s*("(?:\\.|[^"\\])*")\s*,\s*(\d{1,6})\s*,\s*(\d{1,6})\s*(?=[,\]])')


def extract_candidates(document):
    parser = ResultParser()
    parser.feed(document)
    # Google script data embeds [original_url, height, width] next to thumbnail tuples.
    # Decode string literals only; never execute remote JavaScript in the Python process.
    for match in TUPLE.finditer(document):
        try:
            literal = re.sub(r"\\x([0-9a-fA-F]{2})", r"\\u00\1", match[1])
            url = original_url(json.loads(literal))
            height, width = int(match[2]), int(match[3])
            if url and width > 0 and height > 0:
                parser.results.append(ImageCandidate(url, width=width, height=height))
        except (ValueError, TypeError):
            continue
    unique = {}
    for item in parser.results:
        unique.setdefault(item.url, item)
    return list(unique.values())
