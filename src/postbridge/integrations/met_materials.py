"""The Met Collection API and Open Access photographs."""
import re

from postbridge.integrations.material_catalog import MaterialRecord, mentions, normalize_text, plain
from postbridge.integrations.open_material_http import official_url

API = "https://collectionapi.metmuseum.org/public/collection"
OPEN_ACCESS = "https://www.metmuseum.org/hubs/open-access"
CC0 = "https://creativecommons.org/publicdomain/zero/1.0/"


def _place(row, title, hints):
    if native := next((plain(row.get(key), 100) for key in ("city", "locale", "country", "region")
                        if plain(row.get(key), 100)), ""):
        return native, f"Geography: {native}"
    # Some historical photographs describe the location in the catalogue title.
    tags = {normalize_text(tag["term"]) for tag in row.get("tags") or []
            if isinstance(tag, dict) and isinstance(tag.get("term"), str)}
    for hint in hints:
        name = normalize_text(hint)
        if name and (re.search(rf"(?:^| )(?:(?:in|at|au|a|en|du) )?{re.escape(name)}$", normalize_text(title))
                     or name in tags and mentions(title, hint)):
            return hint, f"Catalogue title: {title}"
    return None


def parse_photo(row, object_id, scope):
    try:
        start, end = row.get("objectBeginDate"), row.get("objectEndDate")
        title = plain(row.get("title"), 220)
        date = plain(row.get("objectDate"), 120)
        place = _place(row, title, scope.places)
        card, image = row.get("objectURL"), row.get("primaryImage") or row.get("primaryImageSmall")
        if (str(row.get("objectID")) != object_id or row.get("isPublicDomain") is not True
                or row.get("classification") != "Photographs" or type(start) is not int or start != end
                or not re.search(rf"\b{start}\b", date) or re.search(r"\b(?:ca|circa|probably|possibly|about|before|after)\b", date, re.I)
                or not place or not official_url(card, "www.metmuseum.org", f"/art/collection/search/{object_id}")
                or not official_url(image, "images.metmuseum.org", "/CRDImages/")):
            return None
        return MaterialRecord(reference=f"{API}/v1/objects/{object_id}", record_url=card, image_url=image,
            title=title, year=start, catalog_date=date, place=place[0], location_evidence=place[1],
            creator=plain(row.get("artistDisplayName"), 150) or "Unknown",
            credit=plain(row.get("creditLine"), 180) or "The Metropolitan Museum of Art",
            license=CC0, origin_url=card, rights_evidence=f"Open Access public-domain image. {OPEN_ACCESS}")
    except (TypeError, ValueError, AttributeError):
        return None


class MetCatalogue:
    id = "the_met"
    name = "The Metropolitan Museum of Art"
    search_language = "en"
    request_interval = .15  # Documented ceiling is 80 requests per second.
    default_backoff = 30

    def __init__(self, transport, scope):
        self.transport, self.scope = transport, scope
        self.previews = {}

    def search(self, phrase):
        query = {"q": phrase, "hasImages": "true", "departmentId": 19, "limit": 18}
        if self.scope.year_from > 1 or self.scope.year_to < 9999:
            query["dateBegin"], query["dateEnd"] = self.scope.year_from, self.scope.year_to
        response = self.transport.json(f"{API}/v1.1/search", params=query)
        return [(str(value), "") for value in response.get("objectIDs") or [] if type(value) is int and value > 0]

    def photo(self, lookup):
        object_id, _ = lookup
        response = self.transport.json(f"{API}/v1/objects/{object_id}")
        record = parse_photo(response, object_id, self.scope)
        preview = response.get("primaryImageSmall")
        if record and preview != record.image_url and official_url(preview, "images.metmuseum.org", "/CRDImages/"):
            self.previews[record.reference] = preview
        return record

    def download(self, photo):
        if official_url(photo.image_url, "images.metmuseum.org", "/CRDImages/"):
            data, mime = self.transport.image(photo.image_url)
            if data or photo.reference not in self.previews:
                return data, mime
            return self.transport.image(self.previews[photo.reference])
        return b"", ""
