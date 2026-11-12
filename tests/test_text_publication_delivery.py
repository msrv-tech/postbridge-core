"""Lost text acknowledgements never authorize an automatic or manual resend."""
from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient

from postbridge.db import Base, ENGINE, SESSION_LOCAL, init_db
from postbridge.domain.errors import ConfigurationError, ExternalApiError, InternalError, ValidationError
from postbridge.models.domain import ChannelOrm, PublicationTargetOrm, TenantOrm
from postbridge.services.publication_delivery_state import (
    begin_text_dispatch, delivery_view, prepare_manual_dispatch,
)
from postbridge.services.publication_planning import create_content_with_plan_and_targets
from postbridge.services.publication_target_executor import (
    PublicationTargetExecutor, claim_publication_target_pending,
    recover_stuck_publication_targets, schedule_publication_target_retry,
)


@pytest.fixture
def target():
    Base.metadata.drop_all(bind=ENGINE); init_db()
    with SESSION_LOCAL() as db:
        tenant=TenantOrm(id=str(uuid4()),name='Test');db.add(tenant);db.flush()
        channel=ChannelOrm(id=str(uuid4()),tenant_id=tenant.id,platform='max',kind='destination',
                           title='Test destination',external_id='test-channel',status='connected')
        db.add(channel);db.commit()
        result=create_content_with_plan_and_targets(db,tenant_id=tenant.id,channel_ids=[channel.id],
                                                    title='Test',body_markdown='Exact text',target_status='pending')
        db.commit()
        return tenant.id,result.publication_target_ids[0]


class Publisher:
    def __init__(self,error=None,result='confirmed-1'):
        self.calls=[];self.error=error;self.result=result
    def publish_post(self,channel,post,credentials=None):
        self.calls.append((channel,post.text))
        if self.error:raise self.error
        return self.result


def transport_error():
    return ExternalApiError(code='EXTERNAL_API_MAX_REQUEST_ERROR',message='Acknowledgement lost',
                            source='max',retryable=True)


@pytest.mark.parametrize('error',[transport_error(),httpx.ReadTimeout('Lost acknowledgement'),RuntimeError('Lost acknowledgement')])
def test_lost_ack_survives_new_session_and_blocks_every_resend(target,error):
    _,target_id=target;publisher=Publisher(error)
    with SESSION_LOCAL() as db, pytest.raises((ExternalApiError,InternalError)):
        PublicationTargetExecutor(db,publisher).run(target_id)
    with SESSION_LOCAL() as db:
        row=db.get(PublicationTargetOrm,target_id)
        assert row.status=='unknown' and row.error_code=='PUBLICATION_DELIVERY_UNKNOWN'
        assert delivery_view(db,row)=={'delivery_outcome':'unknown','reconciliation_required':True,'delivery_retry_safe':False}
        assert not schedule_publication_target_retry(db,target_id,transport_error(),3)
        assert PublicationTargetExecutor(db,publisher).run(target_id)==0
        with pytest.raises(ValidationError) as refusal:
            prepare_manual_dispatch(db,row)
        assert refusal.value.code=='VALIDATION_PUBLICATION_RECONCILIATION_REQUIRED'
    assert len(publisher.calls)==1


@pytest.mark.parametrize('result',[None,'','   ',{},True])
def test_missing_success_identifier_is_not_invented_delivery(target,result):
    with SESSION_LOCAL() as db,pytest.raises(InternalError):
        PublicationTargetExecutor(db,Publisher(result=result)).run(target[1])
    with SESSION_LOCAL() as db:
        row=db.get(PublicationTargetOrm,target[1])
        assert row.status=='unknown' and row.external_post_id is None


def test_confirmed_success_remains_idempotent_after_restart(target):
    publisher=Publisher()
    with SESSION_LOCAL() as db:
        assert PublicationTargetExecutor(db,publisher).run(target[1])==1
    with SESSION_LOCAL() as db:
        row=db.get(PublicationTargetOrm,target[1])
        assert delivery_view(db,row)=={'delivery_outcome':'sent','reconciliation_required':False,'delivery_retry_safe':False}
        assert PublicationTargetExecutor(db,publisher).run(target[1])==0
    assert len(publisher.calls)==1


@pytest.mark.parametrize('status',[403,429])
def test_positive_rejection_is_preserved_and_only_it_allows_retry(target,status):
    exc=ExternalApiError(code='MAX_REJECTED',message='Rejected',source='max',retryable=True,
                         details={'status_code':status,'publication_outcome':'rejected'})
    with SESSION_LOCAL() as db,pytest.raises(ExternalApiError):
        PublicationTargetExecutor(db,Publisher(exc)).run(target[1])
    with SESSION_LOCAL() as db:
        row=db.get(PublicationTargetOrm,target[1])
        assert row.status=='failed' and row.error_code=='MAX_REJECTED'
        assert delivery_view(db,row)['delivery_outcome']=='rejected'
        assert schedule_publication_target_retry(db,target[1],exc,3)
        assert PublicationTargetExecutor(db,Publisher()).run(target[1])==1


@pytest.mark.parametrize('status',[400,401,403,429])
def test_post_dispatch_http_error_without_final_boundary_proof_is_unknown(target,status):
    exc=ExternalApiError(code='MULTISTEP_ERROR',message='Step failed',source='max',retryable=True,
                         details={'status_code':status})
    with SESSION_LOCAL() as db,pytest.raises(ExternalApiError):
        PublicationTargetExecutor(db,Publisher(exc)).run(target[1])
    with SESSION_LOCAL() as db:
        row=db.get(PublicationTargetOrm,target[1])
        assert row.status=='unknown' and not schedule_publication_target_retry(db,target[1],exc,3)
        with pytest.raises(ValidationError):prepare_manual_dispatch(db,row)


def test_text_delivery_needs_no_credentials_encryption_key(target,monkeypatch):
    monkeypatch.setenv('CREDENTIALS_ENCRYPTION_KEY','')
    with SESSION_LOCAL() as db:
        assert PublicationTargetExecutor(db,Publisher()).run(target[1])==1
        from postbridge.models.domain import VideoDeliveryStateOrm
        state=json.loads(db.get(VideoDeliveryStateOrm,target[1]).encrypted_state)
        assert state['kind']=='text_publication_v1' and state['outcome']=='sent'
        assert set(state)=={'kind','attempt_id','outcome'}


def test_definite_pre_dispatch_configuration_failure_is_not_sent(target,monkeypatch):
    def unavailable(platform):raise ConfigurationError('Publisher configuration unavailable')
    monkeypatch.setattr('postbridge.services.publication_target_executor.get_publisher',unavailable)
    with SESSION_LOCAL() as db,pytest.raises(ConfigurationError):
        PublicationTargetExecutor(db).run(target[1])
    with SESSION_LOCAL() as db:
        row=db.get(PublicationTargetOrm,target[1])
        assert row.status=='failed' and delivery_view(db,row)['delivery_outcome']=='not_sent'
        prepare_manual_dispatch(db,row)
        assert row.status=='pending'
        assert PublicationTargetExecutor(db,Publisher()).run(target[1])==1


@pytest.mark.parametrize('phase',['pre_dispatch','dispatching','legacy'])
def test_watchdog_only_recovers_proven_pre_dispatch_claim(target,phase):
    target_id=target[1]
    with SESSION_LOCAL() as db:
        if phase=='legacy':db.get(PublicationTargetOrm,target_id).status='publishing'
        else:
            assert claim_publication_target_pending(db,target_id,text_attempt_id='first-attempt')
            if phase=='dispatching':begin_text_dispatch(db,target_id,'first-attempt')
        row=db.get(PublicationTargetOrm,target_id);row.updated_at=datetime.now(UTC)-timedelta(hours=1);db.commit()
    with SESSION_LOCAL() as db:
        assert recover_stuck_publication_targets(db,timeout_seconds=1)==([target_id] if phase=='pre_dispatch' else [])
        assert db.get(PublicationTargetOrm,target_id).status==('pending' if phase=='pre_dispatch' else 'unknown')
        if phase=='pre_dispatch':
            assert claim_publication_target_pending(db,target_id,text_attempt_id='second-attempt')
            with pytest.raises(ValidationError):begin_text_dispatch(db,target_id,'first-attempt')
            begin_text_dispatch(db,target_id,'second-attempt')


def test_old_worker_cannot_use_a_cached_receipt_after_another_worker_claims(target):
    from postbridge.models.domain import VideoDeliveryStateOrm
    target_id=target[1]
    with SESSION_LOCAL() as old:
        assert claim_publication_target_pending(old,target_id,text_attempt_id='old-worker')
        cached=old.get(VideoDeliveryStateOrm,target_id)
        old.get(PublicationTargetOrm,target_id).updated_at=datetime.now(UTC)-timedelta(hours=1)
        old.commit()
        with SESSION_LOCAL() as fresh:
            assert recover_stuck_publication_targets(fresh,timeout_seconds=1)==[target_id]
            assert claim_publication_target_pending(fresh,target_id,text_attempt_id='new-worker')
        # Keep the old ORM instance alive, including its cached first receipt.
        assert cached.target_id==target_id
        with pytest.raises(ValidationError):begin_text_dispatch(old,target_id,'old-worker')
    with SESSION_LOCAL() as db:
        begin_text_dispatch(db,target_id,'new-worker')


def test_local_commit_loss_after_success_does_not_make_a_second_send_safe(target,monkeypatch):
    target_id=target[1];publisher=Publisher()
    with SESSION_LOCAL() as db:
        commit=db.commit
        def fail_after_publish():
            if db.get(PublicationTargetOrm,target_id).status=='published':
                raise RuntimeError('Local success receipt was not committed')
            commit()
        monkeypatch.setattr(db,'commit',fail_after_publish)
        with pytest.raises(RuntimeError):PublicationTargetExecutor(db,publisher).run(target_id)
    with SESSION_LOCAL() as db:
        row=db.get(PublicationTargetOrm,target_id)
        assert row.status=='publishing' and delivery_view(db,row)['reconciliation_required']
        row.updated_at=datetime.now(UTC)-timedelta(hours=1);db.commit()
        assert recover_stuck_publication_targets(db,timeout_seconds=1)==[]
        assert PublicationTargetExecutor(db,publisher).run(target_id)==0
    assert len(publisher.calls)==1


@pytest.mark.parametrize('proven_rejection',[False,True])
def test_worker_releases_live_sync_dedup_fence_only_for_proven_rejection(target,monkeypatch,proven_rejection):
    from postbridge.storage.batch_import_run_store import BatchImportRunStore
    from postbridge.services.live_sync_publish_service import ingest_live_sync_publication
    from postbridge.workers.tasks import process_publication_target_task
    error=transport_error()
    if proven_rejection:
        error.retryable=False;error.details={'publication_outcome':'rejected'}
    publisher=Publisher(error);queued=[]
    monkeypatch.setattr('postbridge.services.publication_target_executor.get_publisher',lambda platform:publisher)
    monkeypatch.setattr(process_publication_target_task,'apply_async',lambda *a,**k:queued.append(k))
    with SESSION_LOCAL() as db:
        channel=db.get(PublicationTargetOrm,target[1]).channel_id
        initial=ingest_live_sync_publication(db,tenant_id=target[0],target_core_channel_id=channel,
            source_channel='source',target_channel='destination',target_platform='max',
            post={'source_post_id':'source-post','text':'Exact text'},correlation_id='test')
    with pytest.raises(ExternalApiError):
        process_publication_target_task.run(initial.target_id,'test',live_sync_source_channel='source',
            live_sync_source_post_id='source-post',live_sync_target_channel='destination',live_sync_target_platform='max')
    with SESSION_LOCAL() as db:
        if proven_rejection:
            assert BatchImportRunStore(db).claim_publish('source','source-post','destination')
        else:
            assert not BatchImportRunStore(db).claim_publish('source','source-post','destination')
            assert ingest_live_sync_publication(db,tenant_id=target[0],target_core_channel_id=channel,
                source_channel='source',target_channel='destination',target_platform='max',
                post={'source_post_id':'source-post','text':'Exact text'},correlation_id='test').skipped
    assert len(publisher.calls)==1 and queued==[]


def test_api_and_outbox_export_evidence_and_manual_dispatch_refuses_unknown(target,monkeypatch):
    from postbridge.api.main import app
    from postbridge.db import PublicationStatusEventOutboxOrm
    with SESSION_LOCAL() as db,pytest.raises(ExternalApiError):
        PublicationTargetExecutor(db,Publisher(transport_error())).run(target[1])
    queued=[]
    monkeypatch.setattr('postbridge.api.service_internal.process_publication_target_task.delay',lambda *a:queued.append(a))
    client=TestClient(app)
    headers={'Authorization':'Bearer svc-test-secret','X-Tenant-Id':target[0]}
    response=client.get(f'/internal/service/publication-targets/{target[1]}',headers=headers)
    assert response.status_code==200,response.text
    assert response.json()['delivery_outcome']=='unknown' and response.json()['reconciliation_required']
    refused=client.post(f'/internal/service/publication-targets/{target[1]}/dispatch',headers=headers)
    assert refused.status_code>=400 and 'RECONCILIATION_REQUIRED' in refused.text
    assert queued==[]
    with SESSION_LOCAL() as db:
        events=list(db.scalars(select(PublicationStatusEventOutboxOrm).where(
            PublicationStatusEventOutboxOrm.publication_target_id==target[1]).order_by(PublicationStatusEventOutboxOrm.id)))
        last=json.loads(events[-1].payload_json)['publication_target']
        assert last['delivery_outcome']=='unknown' and last['reconciliation_required']


def test_legacy_failed_without_evidence_is_not_a_retry_permission(target):
    with SESSION_LOCAL() as db:
        row=db.get(PublicationTargetOrm,target[1]);row.status='failed';db.commit()
        assert delivery_view(db,row)['reconciliation_required']
        assert not schedule_publication_target_retry(db,target[1],transport_error(),3)
        with pytest.raises(ValidationError):prepare_manual_dispatch(db,row)


def test_unknown_import_target_is_not_completed_or_released_by_reconciliation(target):
    from postbridge.db import BatchImportRunOrm, BatchImportEnqueuedPostOrm, BatchImportFetchedPostOrm
    from postbridge.storage.batch_import_run_store import BatchImportRunStore
    from postbridge.sync.batch_import_run_reconcile import (
        reconcile_batch_import_runs, try_release_batch_import_published_post_claim,
    )
    target_id=target[1]
    with SESSION_LOCAL() as db,pytest.raises(ExternalApiError):
        PublicationTargetExecutor(db,Publisher(transport_error())).run(target_id)
    with SESSION_LOCAL() as db:
        channel_id=db.get(PublicationTargetOrm,target_id).channel_id
        store=BatchImportRunStore(db)
        job,_=store.create_run(tenant_id=target[0],source_channel='source',target_channel='destination',
            requested_limit=1,correlation_id='test',source_core_channel_id=channel_id,target_core_channel_id=channel_id)
        run=db.get(BatchImportRunOrm,job.id)
        run.status='running';run.batch_import_dispatch_enqueued_at=datetime.now(UTC)
        db.add(BatchImportEnqueuedPostOrm(batch_import_run_id=job.id,source_post_id='p',publication_target_id=target_id))
        db.add(BatchImportFetchedPostOrm(batch_import_run_id=job.id,source_post_id='p',text='Text',sort_order=0))
        assert store.claim_publish('source','p','destination');db.commit()
        assert reconcile_batch_import_runs(db)==1
        assert run.status=='failed' and run.error_code=='PUBLICATION_DELIVERY_UNKNOWN'
        assert not store.retry_manual(job.id,'manual')
        try_release_batch_import_published_post_claim(db,target_id)
        store.release_retryable_claims_for_run(job.id)
        assert not store.claim_publish('source','p','destination')
