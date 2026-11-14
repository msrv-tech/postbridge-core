"""Regional off switches reject side effects while retaining stored records."""

from types import SimpleNamespace
from uuid import uuid4
from datetime import UTC, datetime, timedelta

import pytest

from postbridge.domain.errors import ValidationError
from postbridge.integrations import registry
from postbridge.integrations.platform_policy import assert_platform_enabled, disabled_platforms
from postbridge.services.publication_target_executor import PublicationTargetExecutor
from tests.test_publication_target_executor import reset_db, _seed_chain_with_max_channel
from postbridge.db import SESSION_LOCAL
from postbridge.models.domain import ChannelOrm, ContentItemOrm, PublicationPlanOrm, PublicationTargetOrm, BridgeOrm


@pytest.mark.parametrize('flag,disabled,enabled', [
    ('facebook,instagram,x,bluesky', 'bluesky', 'max'),
    ('max', 'max', 'bluesky'),
])
def test_regional_factories_and_discovery(flag, disabled, enabled, monkeypatch):
    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', flag)
    for factory in [registry.get_publisher, registry.get_adapter]:
        with pytest.raises(ValidationError) as caught:
            factory(disabled)
        assert caught.value.code == 'PLATFORM_DISABLED'
        assert caught.value.retryable is False
    if disabled == 'max':
        with pytest.raises(ValidationError):
            registry.get_fetcher('max')
    assert disabled not in registry.platform_capabilities_public_map()
    assert enabled in registry.platform_capabilities_public_map()
    assert registry.get_publisher(enabled) is not None


def test_empty_default_and_normalized_policy(monkeypatch):
    monkeypatch.delenv('POSTBRIDGE_DISABLED_PLATFORMS', raising=False)
    assert disabled_platforms() == set()
    assert len(registry.platform_capabilities_public_map()) == len(registry.PLATFORM_BUNDLES)
    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', ' MAX ; bLuEsKy, Twitter ,')
    assert disabled_platforms() == {'max', 'bluesky', 'x'}
    assert_platform_enabled('telegram')
    with pytest.raises(ValidationError):
        assert_platform_enabled('twitter')


def test_disabled_channel_ensure_never_touches_database(monkeypatch):
    from postbridge.api.service_internal import ensure_channel, ChannelEnsureBody

    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')

    class NoDatabase:
        def __getattr__(self, name):
            pytest.fail('Disabled connection must not read/upsert a channel or credential')

    with pytest.raises(ValidationError) as caught:
        ensure_channel(ChannelEnsureBody(platform='max', title='Existing record'),
                       request=SimpleNamespace(), tenant_id='tenant', session=NoDatabase())
    assert caught.value.code == 'PLATFORM_DISABLED'


def test_injected_publisher_and_credentials_cannot_bypass_pre_dispatch_policy(monkeypatch):
    monkeypatch.delenv('POSTBRIDGE_DISABLED_PLATFORMS', raising=False)
    _, target_id = _seed_chain_with_max_channel()
    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')

    def forbidden(*args, **kwargs):
        pytest.fail('Disabled target must stop before credentials, dispatch and provider I/O')

    monkeypatch.setattr('postbridge.services.publication_target_executor.load_channel_credential_row', forbidden)
    monkeypatch.setattr('postbridge.services.publication_target_executor.begin_text_dispatch', forbidden)
    with SESSION_LOCAL() as session:
        with pytest.raises(ValidationError) as caught:
            PublicationTargetExecutor(session, publisher=SimpleNamespace(publish_post=forbidden)).run(target_id)
        assert caught.value.code == 'PLATFORM_DISABLED'
        target = session.get(PublicationTargetOrm, target_id)
        assert target.status == 'pending'
        from postbridge.services.publication_delivery_state import text_delivery_state
        assert not text_delivery_state(session, target_id)


def test_adapter_created_before_policy_change_cannot_publish(monkeypatch):
    monkeypatch.delenv('POSTBRIDGE_DISABLED_PLATFORMS', raising=False)
    adapter = registry.get_adapter('max')
    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')
    with pytest.raises(ValidationError) as caught:
        adapter.publish_post('chat', SimpleNamespace(), None)
    assert caught.value.code == 'PLATFORM_DISABLED'


def test_planning_never_creates_rows_for_disabled_canonical_channel(monkeypatch):
    from postbridge.services.publication_planning import create_content_with_plan_and_targets, create_plan_and_targets_for_content_item

    monkeypatch.delenv('POSTBRIDGE_DISABLED_PLATFORMS', raising=False)
    tenant_id, target_id = _seed_chain_with_max_channel()
    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')
    with SESSION_LOCAL() as session:
        target = session.get(PublicationTargetOrm, target_id)
        plan = session.get(PublicationPlanOrm, target.publication_plan_id)
        content = session.get(ContentItemOrm, plan.content_item_id)
        before = [session.query(model).count() for model in [ContentItemOrm, PublicationPlanOrm, PublicationTargetOrm]]
        for call in [
            lambda: create_content_with_plan_and_targets(session, tenant_id=tenant_id, channel_ids=[target.channel_id], body_markdown='Must not be created'),
            lambda: create_plan_and_targets_for_content_item(session, tenant_id=tenant_id, content_item=content, channel_ids=[target.channel_id]),
        ]:
            with pytest.raises(ValidationError) as caught:
                call()
            assert caught.value.code == 'PLATFORM_DISABLED'
            assert not session.new
        assert [session.query(model).count() for model in [ContentItemOrm, PublicationPlanOrm, PublicationTargetOrm]] == before


def test_spoofed_ingress_platform_cannot_claim_disabled_stored_target(monkeypatch):
    import postbridge.services.live_sync_publish_service as service

    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')

    def forbidden(*args, **kwargs):
        pytest.fail('Disabled canonical target must stop before claim or planning')

    monkeypatch.setattr(service, 'BatchImportRunStore', forbidden)
    session = SimpleNamespace(get=lambda model, identifier: SimpleNamespace(tenant_id='tenant', platform='max'))
    with pytest.raises(ValidationError) as caught:
        service.ingest_live_sync_publication(session, tenant_id='tenant', target_core_channel_id='target',
            source_channel='source', target_channel='chat', target_platform='vk',
            post={'source_post_id': 'post', 'text': 'Approved text'}, correlation_id='test')
    assert caught.value.code == 'PLATFORM_DISABLED'


@pytest.mark.parametrize('outcome', ['not_sent', 'unknown'])
def test_manual_worker_and_claim_preserve_existing_receipt_fence_and_retry(monkeypatch, outcome):
    from postbridge.services.publication_delivery_state import write_text_delivery, text_delivery_state, prepare_manual_dispatch
    from postbridge.storage.batch_import_run_store import BatchImportRunStore
    from postbridge.db import PublishedPostOrm
    import postbridge.workers.tasks as tasks

    monkeypatch.delenv('POSTBRIDGE_DISABLED_PLATFORMS', raising=False)
    tenant_id, target_id = _seed_chain_with_max_channel()
    with SESSION_LOCAL() as session:
        target = session.get(PublicationTargetOrm, target_id)
        target.status, target.error_code, target.error_message = 'failed', 'ORIGINAL', 'Original retained'
        write_text_delivery(session, target_id, outcome, attempt_id='original-attempt')
        assert BatchImportRunStore(session).claim_publish('pb/source', 'source-post', 'chat-42')
        session.commit()
        before = (target.status, target.retry_count, target.error_code, target.error_message, text_delivery_state(session, target_id))
        target_channel_id = target.channel_id
    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')

    def forbidden(*args, **kwargs):
        pytest.fail('Policy refusal must not retry, release any fence or enqueue')

    monkeypatch.setattr(tasks, 'schedule_publication_target_retry', forbidden)
    monkeypatch.setattr(tasks, '_schedule_process_target_retry_apply', forbidden)
    monkeypatch.setattr(tasks.BatchImportRunStore, 'release_claim', forbidden)
    with SESSION_LOCAL() as session:
        target = session.get(PublicationTargetOrm, target_id)
        with pytest.raises(ValidationError):
            prepare_manual_dispatch(session, target)
        with pytest.raises(ValidationError):
            from postbridge.services.publication_target_executor import claim_publication_target_pending
            claim_publication_target_pending(session, target_id)
    with pytest.raises(ValidationError) as caught:
        tasks.process_publication_target_task.run(target_id, correlation_id='test',
            live_sync_source_channel='pb/source', live_sync_source_post_id='source-post',
            live_sync_target_channel='chat-42', live_sync_target_platform='max',
            live_sync_tenant_id=tenant_id, live_sync_target_core_channel_id=target_channel_id,
            live_sync_post_json='{"text":"retained"}')
    assert caught.value.code == 'PLATFORM_DISABLED'
    with SESSION_LOCAL() as session:
        target = session.get(PublicationTargetOrm, target_id)
        assert (target.status, target.retry_count, target.error_code, target.error_message, text_delivery_state(session, target_id)) == before
        assert session.query(PublishedPostOrm).filter_by(source_channel='pb/source', source_post_id='source-post', target_channel='chat-42').count() == 1


def test_published_noop_is_preserved_when_platform_is_disabled(monkeypatch):
    monkeypatch.delenv('POSTBRIDGE_DISABLED_PLATFORMS', raising=False)
    _, target_id = _seed_chain_with_max_channel()
    with SESSION_LOCAL() as session:
        target = session.get(PublicationTargetOrm, target_id)
        target.status, target.external_post_id = 'published', 'original-mid'
        session.commit()
        monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')
        assert PublicationTargetExecutor(session, publisher=SimpleNamespace()).run(target_id) == 0
        assert target.external_post_id == 'original-mid'
        assert target.status == 'published'


def test_disabled_adaptation_stops_before_injected_ai(monkeypatch):
    from postbridge.services.bridge_adaptation import adapt_post_for_bridge

    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')

    def forbidden(**kwargs):
        pytest.fail('Disabled target must not reach AI generation or billing')

    with pytest.raises(ValidationError):
        registry.get_ai_adapter('max')
    with pytest.raises(ValidationError):
        adapt_post_for_bridge(None, tenant_id='tenant', post={'text': 'No AI'}, platform='max',
                             bridge_settings={'adaptation_mode': 'ai_auto'}, generator=forbidden)


def _mixed_bridge_graph(session, tenant_id, max_channel_id):
    source = ChannelOrm(id=str(uuid4()), tenant_id=tenant_id, platform='postbridge', kind='source',
                        title='Source', external_id='pb/source', status='connected')
    enabled = ChannelOrm(id=str(uuid4()), tenant_id=tenant_id, platform='bluesky', kind='destination',
                         title='Enabled', external_id='brand.test', status='connected')
    session.add_all([source, enabled])
    session.flush()
    for target in [max_channel_id, enabled.id]:
        session.add(BridgeOrm(id=uuid4().hex, tenant_id=tenant_id, saas_user_id='editor',
            source_channel_id=source.id, target_channel_id=target, mode='live_sync', status='active'))
    session.commit()
    return source.id, enabled.id


def test_core_bridge_discovery_and_reactivation_use_canonical_platform(monkeypatch):
    from postbridge.api.service_internal import service_list_live_sync_targets_by_source, service_patch_bridge, ServiceBridgePatchBody, service_create_bridge, ServiceBridgeCreateBody

    monkeypatch.delenv('POSTBRIDGE_DISABLED_PLATFORMS', raising=False)
    tenant_id, target_id = _seed_chain_with_max_channel()
    with SESSION_LOCAL() as session:
        max_id = session.get(PublicationTargetOrm, target_id).channel_id
        source_id, enabled_id = _mixed_bridge_graph(session, tenant_id, max_id)
        bridge = session.query(BridgeOrm).filter_by(target_channel_id=max_id).one()
        bridge.status = 'paused'
        session.commit()
        monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')
        with pytest.raises(ValidationError):
            service_patch_bridge(bridge.id, ServiceBridgePatchBody(status='active'),
                saas_user_id='editor', tenant_id=tenant_id, session=session)
        assert bridge.status == 'paused'
        with pytest.raises(ValidationError):
            service_create_bridge(ServiceBridgeCreateBody(saas_user_id='other-editor',
                source_channel_id=source_id, target_channel_id=max_id), tenant_id=tenant_id, session=session)
        assert session.query(BridgeOrm).count() == 2
        result = service_list_live_sync_targets_by_source(source_id, tenant_id=tenant_id, session=session)
        assert [item.target_channel_id for item in result['items']] == [enabled_id]


def test_scheduled_mixed_fanout_skips_disabled_before_adaptation(monkeypatch):
    from postbridge.services.postbridge_workspace_content import _dump_extra
    import postbridge.services.postbridge_scheduled_publish as scheduled

    monkeypatch.delenv('POSTBRIDGE_DISABLED_PLATFORMS', raising=False)
    tenant_id, target_id = _seed_chain_with_max_channel()
    with SESSION_LOCAL() as session:
        max_id = session.get(PublicationTargetOrm, target_id).channel_id
        source_id, enabled_id = _mixed_bridge_graph(session, tenant_id, max_id)
        content = ContentItemOrm(id=str(uuid4()), tenant_id=tenant_id, source_type='postbridge',
            title='Planned material', body_markdown='Approved text', status='draft',
            body_structured_json=_dump_extra({'scheduled_publish_at': (datetime.now(UTC)-timedelta(minutes=1)).isoformat(),
                'live_sync_source_core_channel_id': source_id, 'saas_workspace_id': 'workspace'}))
        session.add(content)
        session.commit()
        calls = []

        def adapt(*args, **kwargs):
            calls.append(kwargs['platform'])
            assert kwargs['platform'] == 'bluesky'
            return SimpleNamespace(text='Approved text', status='ready')

        monkeypatch.setattr(scheduled, 'adapt_post_for_bridge', adapt)
        monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')
        published, jobs = scheduled.try_publish_scheduled_postbridge_item(session, content_id=content.id, now_utc=datetime.now(UTC))
        assert published is True
        assert calls == ['bluesky']
        assert [job.target_core_channel_id for job in jobs] == [enabled_id]


def test_disabled_internal_edit_never_becomes_404_publish_fallback(monkeypatch):
    from fastapi.testclient import TestClient
    from postbridge.api.main import app
    from postbridge.services import live_sync_queue

    monkeypatch.delenv('POSTBRIDGE_DISABLED_PLATFORMS', raising=False)
    tenant_id, target_id = _seed_chain_with_max_channel()
    with SESSION_LOCAL() as session:
        channel_id = session.get(PublicationTargetOrm, target_id).channel_id
    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')
    client = TestClient(app)
    statuses = []

    def local_post(url, **kwargs):
        result = client.post('/internal/sync/edit-single', json=kwargs['json'])
        statuses.append(result.status_code)
        return result

    def forbidden(*args, **kwargs):
        pytest.fail('Disabled edit must not load credentials or publish as fallback')

    monkeypatch.setattr(live_sync_queue.httpx, 'post', local_post)
    monkeypatch.setattr(live_sync_queue, 'deliver_publish_to_core', forbidden)
    monkeypatch.setattr('postbridge.api.live_sync.load_channel_credential_row', forbidden)
    result = live_sync_queue.deliver_edit_to_core(source_channel='pb/source', target_channel='chat-42',
        post={'source_post_id': 'not-published', 'text': 'Keep as draft'},
        target_platform='vk', core_tenant_id=tenant_id, target_core_channel_id=channel_id)
    assert statuses == [422]
    assert result['status'] == 'failed'


@pytest.mark.parametrize('action', ['edit-single', 'delete-single', 'fetch-posts'])
def test_internal_policy_refuses_before_ledger_or_credentials(monkeypatch, action):
    from fastapi.testclient import TestClient
    from postbridge.api.main import app
    from postbridge.db import PublishedPostOrm
    from postbridge.storage.batch_import_run_store import BatchImportRunStore

    monkeypatch.delenv('POSTBRIDGE_DISABLED_PLATFORMS', raising=False)
    tenant_id, target_id = _seed_chain_with_max_channel()
    with SESSION_LOCAL() as session:
        channel_id = session.get(PublicationTargetOrm, target_id).channel_id
        store = BatchImportRunStore(session)
        assert store.claim_publish('pb/source', 'original', 'chat-42')
        store.update_max_message_id('pb/source', 'original', 'chat-42', 'original-mid')
        session.commit()
        row = session.query(PublishedPostOrm).one()
        before = tuple(getattr(row, column.name) for column in PublishedPostOrm.__table__.columns)
    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')

    def forbidden(*args, **kwargs):
        pytest.fail('Disabled route must stop before ledger, credentials and provider I/O')

    monkeypatch.setattr('postbridge.api.live_sync.BatchImportRunStore', forbidden)
    monkeypatch.setattr('postbridge.api.live_sync.load_channel_credential_row', forbidden)
    monkeypatch.setattr('postbridge.api.live_sync.resolve_fetch_credentials_for_core_channel', forbidden)
    monkeypatch.setattr('postbridge.api.live_sync.get_publisher', forbidden)
    monkeypatch.setattr('postbridge.api.live_sync.get_fetcher', forbidden)
    body = {'tenant_id': tenant_id, 'source_channel': 'pb/source'}
    if action == 'fetch-posts':
        body.update(source_platform='vk', source_core_channel_id=channel_id)
    else:
        body.update(target_platform='vk', target_core_channel_id=channel_id, target_channel='chat-42')
        body.update({'post': {'source_post_id': 'original', 'text': 'Retained'}} if action == 'edit-single'
                    else {'source_post_id': 'original'})
    response = TestClient(app).post(f'/internal/{"" if action == "fetch-posts" else "sync/"}{action}', json=body)
    assert response.status_code == 422
    assert 'PLATFORM_DISABLED' in response.text
    assert response.json()['retryable'] is False
    with SESSION_LOCAL() as session:
        row = session.query(PublishedPostOrm).one()
        assert tuple(getattr(row, column.name) for column in PublishedPostOrm.__table__.columns) == before
        assert session.get(ChannelOrm, channel_id).platform == 'max'


def test_wrong_tenant_cannot_learn_stored_disabled_platform(monkeypatch):
    from fastapi.testclient import TestClient
    from postbridge.api.main import app

    monkeypatch.delenv('POSTBRIDGE_DISABLED_PLATFORMS', raising=False)
    _, target_id = _seed_chain_with_max_channel()
    with SESSION_LOCAL() as session:
        channel_id = session.get(PublicationTargetOrm, target_id).channel_id
    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')
    response = TestClient(app).post('/internal/sync/edit-single', json={
        'tenant_id': str(uuid4()), 'target_core_channel_id': channel_id,
        'target_platform': 'vk', 'target_channel': 'chat-42', 'source_channel': 'pb/source',
        'post': {'source_post_id': 'original', 'text': 'Retain'},
    })
    assert response.status_code == 422
    assert response.json()['code'] == 'VALIDATION_CHANNEL_NOT_FOUND'
    assert response.json()['details'] == {}


@pytest.mark.parametrize('requested', ['vk', 'telegram'])
@pytest.mark.parametrize('foreign_tenant', [False, True])
def test_existing_disabled_channel_cannot_be_relabelled_by_enabled_request(monkeypatch, requested, foreign_tenant):
    from postbridge.api import service_internal as service

    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')
    tenant_id, channel_id = str(uuid4()), str(uuid4())
    row = ChannelOrm(id=channel_id, tenant_id=str(uuid4()) if foreign_tenant else tenant_id,
        platform='max', kind='target', title='Retained', external_id='original',
        status='active', config_json='{}', capabilities_json='{}')
    before = dict(vars(row))

    def forbidden(*args, **kwargs):
        pytest.fail('Canonical disabled row must stop before encrypt/add/flush/commit')

    session = SimpleNamespace(get=lambda model, key: row if model is ChannelOrm else SimpleNamespace(id=tenant_id),
        add=forbidden, flush=forbidden, commit=forbidden)
    monkeypatch.setattr(service, 'encrypt_credential_secret', forbidden)
    body = service.ChannelEnsureBody(channel_id=channel_id, platform=requested, title='Spoofed replacement',
        credential={'auth_type': 'api_key', 'encrypted_secret': 'synthetic-not-a-real-secret'})
    with pytest.raises(ValidationError) as caught:
        service.ensure_channel(body, request=SimpleNamespace(), tenant_id=tenant_id, session=session)
    assert caught.value.code == ('VALIDATION_CHANNEL_TENANT_MISMATCH' if foreign_tenant else 'PLATFORM_DISABLED')
    assert caught.value.retryable is False
    assert vars(row) == before


def test_default_empty_policy_preserves_existing_channel_upsert(monkeypatch):
    from postbridge.api import service_internal as service

    monkeypatch.delenv('POSTBRIDGE_DISABLED_PLATFORMS', raising=False)
    tenant_id, channel_id = str(uuid4()), str(uuid4())
    row = ChannelOrm(id=channel_id, tenant_id=tenant_id, platform='max', kind='target', title='Before')
    calls = []
    session = SimpleNamespace(get=lambda model, key: row if model is ChannelOrm else SimpleNamespace(id=tenant_id),
        flush=lambda: calls.append('flush'), commit=lambda: calls.append('commit'))
    result = service.ensure_channel(service.ChannelEnsureBody(channel_id=channel_id, platform='vk', title='Updated'),
        request=SimpleNamespace(), tenant_id=tenant_id, session=session)
    assert result == {'channel_id': channel_id, 'status': 'ok'}
    assert row.platform == 'vk' and row.title == 'Updated'
    assert calls == ['flush', 'commit']


def test_existing_disabled_channel_http_refusal_is_422_before_any_write(monkeypatch):
    from fastapi.testclient import TestClient
    from postbridge.api import service_internal as service
    from postbridge.api.main import app
    from postbridge.db import get_db_session

    monkeypatch.setenv('POSTBRIDGE_DISABLED_PLATFORMS', 'max')
    tenant_id, channel_id = str(uuid4()), str(uuid4())
    row = ChannelOrm(id=channel_id, tenant_id=tenant_id, platform='max', title='Retained', status='active')
    before = dict(vars(row))
    def forbidden(*args, **kwargs):
        pytest.fail('Disabled canonical row cannot encrypt, flush or commit')
    session = SimpleNamespace(get=lambda model, key: row if model is ChannelOrm else SimpleNamespace(id=tenant_id),
        add=forbidden, flush=forbidden, commit=forbidden)
    monkeypatch.setattr(service, 'encrypt_credential_secret', forbidden)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[service.require_service_tenant] = lambda: tenant_id
    try:
        response = TestClient(app).post('/internal/service/channels/ensure', json={
            'channel_id': channel_id, 'platform': 'vk', 'title': 'Spoofed',
            'credential': {'auth_type': 'api_key', 'encrypted_secret': 'synthetic-only'},
        })
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
    assert response.status_code == 422
    assert response.json()['code'] == 'PLATFORM_DISABLED'
    assert response.json()['retryable'] is False
    assert vars(row) == before
