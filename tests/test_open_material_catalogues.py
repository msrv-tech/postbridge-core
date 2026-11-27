from copy import deepcopy
from time import monotonic

import httpx
import pytest

from postbridge.integrations.commons_materials import parse_photo as parse_commons
from postbridge.integrations.material_catalog import CatalogueScope, CatalogError
from postbridge.integrations.met_materials import parse_photo as parse_met
from postbridge.integrations.open_material_http import PublicCatalogueHTTP, retry_seconds


def test_met_requires_a_public_photograph_with_certain_date_official_file_and_place():
    raw = {"objectID": 12, "objectURL": "https://www.metmuseum.org/art/collection/search/12",
        "primaryImageSmall": "https://images.metmuseum.org/CRDImages/ph/web-large/100.jpg",
        "title": "Garden in Bristol", "classification": "Photographs", "objectDate": "April 1, 1890",
        "objectBeginDate": 1890, "objectEndDate": 1890, "isPublicDomain": True}
    scope = CatalogueScope(("Bristol",))
    assert parse_met(raw, "12", scope).year == 1890
    for change in ({"isPublicDomain": False}, {"objectDate": "ca. 1890"}, {"title": "Seen previously in Bristol and now York"},
                   {"primaryImageSmall": "https://example.com/CRDImages/ph/web-large/100.jpg"}):
        assert parse_met({**raw, **change}, "12", scope) is None


def test_commons_requires_recorded_location_exact_date_and_explicit_reuse():
    raw = {"title": "File:Bridge in Bristol.jpg", "pageid": 12, "templates": [{"title": "Template:PD-old-100-expired"}],
        "imageinfo": [{"descriptionurl": "https://commons.wikimedia.org/wiki/File:Bridge_in_Bristol.jpg",
                       "url": "https://upload.wikimedia.org/wikipedia/commons/a/aa/Bridge_in_Bristol.jpg",
                       "mime": "image/jpeg", "mediatype": "BITMAP", "size": 18000,
                       "extmetadata": {key: {"value": value} for key, value in {
                           "ImageDescription": "The old bridge in Bristol", "Categories": "Photographs of Bristol",
                           "DateTimeOriginal": "Taken on 20 April 1890",
                           "Artist": "Ada Example", "Credit": 'Local Archive <a href="https://archive.example/record">record</a>',
                           "Copyrighted": "False", "LicenseShortName": "Public domain"}.items()}}]}
    scope = CatalogueScope(("Bristol",))
    record = parse_commons(raw, scope)
    assert record.place == "Bristol" and record.origin_url == "https://archive.example/record" and record.year == 1890
    assert parse_commons({**raw, "templates": []}, scope) is None
    assert parse_commons(raw, CatalogueScope(("Dublin",))) is None
    printed_copy = deepcopy(raw)
    printed_copy["imageinfo"][0]["extmetadata"]["Categories"] = {"value": "Newspaper headlines"}
    assert parse_commons(printed_copy, scope) is None
    for key, value in (("DateTimeOriginal", "1890 or 1891"), ("Restrictions", "Personality rights"), ("Copyrighted", "True")):
        other = deepcopy(raw)
        other["imageinfo"][0]["extmetadata"][key] = {"value": value}
        assert parse_commons(other, scope) is None


@pytest.mark.parametrize("status,body", [(429, {}), (503, {}), (200, {"error": {"code": "maxlag"}})])
def test_catalogue_obeys_retry_after_across_sessions(status, body, tmp_path):
    hits = []

    def upstream(request):
        hits.append(request)
        return httpx.Response(status, headers={"Retry-After": "125"}, json=body)

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        for _ in range(2):
            transport = PublicCatalogueHTTP(client, "test-catalogue", interval=.6,
                deadline=monotonic() + 2, backoff=10, state_dir=tmp_path)
            with pytest.raises(CatalogError, match="pause") as raised:
                transport.json("https://catalogue.example/api")
            assert raised.value.code == "MATERIAL_CATALOG_BACKOFF"
    assert len(hits) == 1
    assert retry_seconds("Wed, 21 Oct 2015 07:28:00 GMT", 99, now=1445412460) == 20
