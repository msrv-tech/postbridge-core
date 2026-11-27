"""Provider registry for material searches."""
from contextlib import contextmanager

import httpx

from postbridge.integrations.commons_materials import CommonsCatalogue
from postbridge.integrations.met_materials import MetCatalogue
from postbridge.integrations.open_material_http import PublicCatalogueHTTP, USER_AGENT


CATALOGUES = {catalogue.id: catalogue for catalogue in (CommonsCatalogue, MetCatalogue)}


def catalogue_options():
    return [{"id": item.id, "name": item.name, "search_language": item.search_language} for item in CATALOGUES.values()]


@contextmanager
def open_catalogue(catalogue_id, scope, deadline):
    catalogue = CATALOGUES[catalogue_id]
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        transport = PublicCatalogueHTTP(client, catalogue_id, interval=catalogue.request_interval,
            deadline=deadline, backoff=catalogue.default_backoff)
        yield catalogue(transport, scope)
