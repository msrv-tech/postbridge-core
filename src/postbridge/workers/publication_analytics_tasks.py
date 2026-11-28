"""Provider metrics run independently of the publication delivery task."""

from postbridge.db import SESSION_LOCAL
from postbridge.services.publication_analytics import collect_audiences, collect_due
from postbridge.workers.celery_app import celery_app


@celery_app.task(name="postbridge.analytics.collect", soft_time_limit=65, time_limit=75, ignore_result=True)
def collect_publication_analytics():
    return collect_due(SESSION_LOCAL)


@celery_app.task(name="postbridge.analytics.audience", soft_time_limit=65, time_limit=75, ignore_result=True)
def collect_channel_audience():
    return collect_audiences(SESSION_LOCAL)
