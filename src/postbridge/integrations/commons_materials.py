"""Wikimedia Commons Action API search and file-level evidence."""
from datetime import date
from html import unescape
import re
from urllib.parse import urlsplit

from postbridge.integrations.material_catalog import MaterialRecord, mentions, plain
from postbridge.integrations.open_material_http import MAX_IMAGE_BYTES, official_url

API = "https://commons.wikimedia.org/w/api.php"
PUBLIC_DOMAIN = "https://creativecommons.org/publicdomain/mark/1.0/"
LICENSES = {
    "https://creativecommons.org/publicdomain/zero/1.0",
    "https://creativecommons.org/publicdomain/mark/1.0",
    "https://creativecommons.org/licenses/by/4.0",
}
PUBLIC_DOMAIN_TEMPLATES = (
    "Template:PD-USGov", "Template:PD-USGov-USGS", "Template:PD-USGov-NARA",
    "Template:PD-US-expired", "Template:PD-old-70-expired", "Template:PD-old-100-expired",
    "Template:PD-old-100", "Template:PD-self",
)
EXT_METADATA = ("DateTimeOriginal|ImageDescription|Artist|Credit|LicenseShortName|LicenseUrl|"
                "UsageTerms|AttributionRequired|Copyrighted|Restrictions|Categories")
MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december")


def _exact_year(value):
    value = re.sub(r"^taken (?:in|on)\s+", "", value, flags=re.I)
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?: [0-9]{2}:[0-9]{2}:[0-9]{2})?", value):
        return date.fromisoformat(value[:10]).year
    match = re.fullmatch(rf"(?:(?:[0-9]{{1,2}} )?(?:{MONTHS}) )?([0-9]{{4}})", value, re.I)
    return int(match[1]) if match else None


def _origin(credit, card):
    for link in re.findall(r'href=["\'](https://[^"\']+)["\']', credit or ""):
        url = unescape(link)
        parsed = urlsplit(url)
        if len(url) <= 360 and parsed.hostname and not parsed.hostname.endswith((".wikimedia.org", ".wikipedia.org")):
            return url
    return card


def _place(title, metadata, hints):
    description = plain(metadata("ImageDescription"), 900)
    categories = plain(metadata("Categories"), 900)
    # An editorial place merely guides the lookup. The record must independently
    # locate the depicted scene in its description and title or subject categories.
    if not hints:
        lead = re.match(r"^([^,.:]{2,65}),\s*[^,.:]{2,65},\s*[^,.:]{2,65}\.", description)
        hints = (lead[1],) if lead else ()
    for place in hints:
        if mentions(description, place) and (mentions(title, place) or mentions(categories, place)):
            return place, f"File description: {description[:260]}"
    return None


def parse_photo(page, scope):
    try:
        title = plain(page["title"].removeprefix("File:").rsplit(".", 1)[0], 220)
        info = page["imageinfo"][0]
        meta = info["extmetadata"]

        def metadata(key):
            value = meta.get(key, {}).get("value")
            return value if isinstance(value, str) else ""

        place = _place(title, metadata, scope.places)
        photo_evidence = " ".join(plain(metadata(field), 3000) for field in ("ImageDescription", "Categories"))
        photographic = re.search(r"\b(?:photo|photograph)(?:s|ic|y)?\b", f"{title} {photo_evidence}", re.I)
        catalog_date = plain(metadata("DateTimeOriginal"), 120)
        year = _exact_year(catalog_date)
        license_url = metadata("LicenseUrl").rstrip("/")
        template_names = {item["title"] for item in page.get("templates", [])}
        public_status = set(PUBLIC_DOMAIN_TEMPLATES) & template_names
        rights = plain(metadata("UsageTerms") or metadata("LicenseShortName"), 120)
        if not license_url and metadata("Copyrighted") == "False" and public_status:
            license_url = PUBLIC_DOMAIN.rstrip("/")
            rights = f"{rights}. {', '.join(sorted(public_status))}"
        card, image = info.get("descriptionurl"), info.get("url")
        author = plain(metadata("Artist"), 150)
        credit = metadata("Credit")
        if (not photographic or not place or not year or not rights or metadata("Restrictions")
                or license_url not in LICENSES or license_url.endswith("/by/4.0") and not author
                or not plain(credit, 180) or info.get("mediatype") != "BITMAP"
                or info.get("mime") not in {"image/jpeg", "image/png", "image/webp"}
                or not 0 < info.get("size", 0) <= MAX_IMAGE_BYTES
                or not official_url(card, "commons.wikimedia.org", "/wiki/File:")
                or not official_url(image, "upload.wikimedia.org", "/wikipedia/commons/")):
            return None
        return MaterialRecord(reference=f"https://commons.wikimedia.org/?curid={page['pageid']}",
            record_url=card, image_url=image, title=title, year=year, catalog_date=catalog_date,
            place=place[0], location_evidence=place[1], creator=author or "Unknown",
            credit=plain(credit, 180), license=license_url + "/", origin_url=_origin(credit, card),
            rights_evidence=rights[:240])
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None


class CommonsCatalogue:
    id = "wikimedia_commons"
    name = "Wikimedia Commons"
    search_language = "en"
    request_interval = .6  # Action API allows 200/minute with an identifying User-Agent.
    default_backoff = 60

    def __init__(self, transport, scope):
        self.transport, self.scope = transport, scope
        self.pages = {}

    def search(self, phrase):
        # extmetadata is expensive. Read a small batch, including only needed fields.
        response = self.transport.json(API, params={"action": "query", "format": "json", "formatversion": 2,
            "maxlag": 5, "generator": "search", "gsrnamespace": 6, "gsrsearch": f"{phrase} filetype:bitmap",
            "gsrlimit": 8, "prop": "imageinfo|templates", "iiprop": "url|mime|mediatype|size|extmetadata",
            "iiextmetadatafilter": EXT_METADATA, "tllimit": 50, "tltemplates": "|".join(PUBLIC_DOMAIN_TEMPLATES)})
        pages = [page for page in response.get("query", {}).get("pages", []) if type(page.get("pageid")) is int]
        for page in pages:
            self.pages[str(page["pageid"])] = page
        return [(str(page["pageid"]), "") for page in sorted(pages, key=lambda row: row.get("index", 0))]

    def photo(self, lookup):
        key, _ = lookup
        return parse_photo(self.pages[key], self.scope)

    def download(self, photo):
        if official_url(photo.image_url, "upload.wikimedia.org", "/wikipedia/commons/"):
            return self.transport.image(photo.image_url)
        return b"", ""
