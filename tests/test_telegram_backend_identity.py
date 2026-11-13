"""Exercise the actual thin bot adapter and HTTP DTOs, without a live transport."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from postbridge.botkit import saas_http
from postbridge.botkit.platforms.telegram.backend import CoreDbBackend, SaasBackend


@pytest.fixture
def transport(monkeypatch):
    calls = []
    state = {'identity': ('email:canonical', 'ws-email-canonical'),
             'row': {'id': 'pending-1', 'user_id': 'email:canonical',
                     'title': 'Own target', 'platform_channel_id': '-100123', 'status': 'pending'}}
    async def handler(request):
        calls.append(request)
        path = request.url.path
        if path == '/internal/bot/ensure-user':
            if 'error' in state:
                if state['error'] == 'timeout':
                    raise httpx.ReadTimeout('synthetic timeout', request=request)
                return httpx.Response(state['error'], json={'code': 'synthetic_identity_conflict'})
            identity = state['identity']
            if callable(identity):
                identity = await identity(request)
            return httpx.Response(200, json={'user_id': identity[0], 'workspace_id': identity[1]})
        if path.endswith('/has-telegram-channel'):
            assert path == '/internal/bot/user/' + state['identity'][0] + '/has-telegram-channel'
            return httpx.Response(200, json={'has': True})
        if path == '/internal/bot/pending-channel':
            if callable(state.get('pending')):
                row = state['pending'](request)
            else:
                assert request.url.params['user_id'] == state['identity'][0]
                row = state['row']
            return httpx.Response(200, json={'channel': row})
        if path == '/internal/bot/register-telegram-channel':
            return httpx.Response(200, json=state['row'])
        pytest.fail('Unexpected fallback HTTP request: ' + path)
    original = httpx.AsyncClient
    monkeypatch.setattr(saas_http, 'get_settings', lambda: SimpleNamespace(
        saas_base_url='https://saas.invalid', saas_bot_secret='synthetic-bot-secret'))
    monkeypatch.setattr(saas_http.httpx, 'AsyncClient', lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))
    return state, calls


@pytest.mark.parametrize('identity', [('email:canonical', 'ws-email-canonical'), ('telegram:42', 'ws-tg-42')])
@pytest.mark.parametrize('operation', ['has_attached_channel', 'pending_channel', 'resolve_workspace_id'])
def test_each_operation_uses_one_returned_canonical_pair(transport, identity, operation):
    state, calls = transport
    state['identity'] = identity
    state['row']['user_id'] = identity[0]
    result = asyncio.run(getattr(SaasBackend(), operation)(42, 'display-name'))
    assert sum(r.url.path.endswith('/ensure-user') for r in calls) == 1
    assert json.loads(calls[0].content) == {'telegram_user_id': 42, 'telegram_username': 'display-name'}
    if operation == 'resolve_workspace_id':
        assert result == identity[1] and len(calls) == 1
    elif operation == 'pending_channel':
        assert result.workspace_id == identity[1] and result.chat_id == -100123
        assert calls[1].url.params['user_id'] == identity[0] and len(calls) == 2
    else:
        assert result is True and len(calls) == 2


@pytest.mark.parametrize('error', [409, 422, 503, 'timeout'])
@pytest.mark.parametrize('operation', ['has_attached_channel', 'pending_channel', 'resolve_workspace_id'])
def test_resolution_error_never_issues_legacy_or_workspace_fallback(transport, error, operation):
    state, calls = transport
    state['error'] = error
    result = asyncio.run(getattr(SaasBackend(), operation)(42, None))
    assert result is (False if operation == 'has_attached_channel' else None)
    assert [r.url.path for r in calls] == ['/internal/bot/ensure-user']


@pytest.mark.parametrize('identity', [(None, 'ws'), ('', 'ws'), ('email:canonical', ''),
    ('email:canonical', None), (17, 'ws'), ('email:canonical', False), (' canonical ', 'ws')])
@pytest.mark.parametrize('operation', ['has_attached_channel', 'pending_channel', 'resolve_workspace_id'])
def test_malformed_canonical_dto_is_not_an_identity(transport, identity, operation):
    state, calls = transport
    state['identity'] = identity
    result = asyncio.run(getattr(SaasBackend(), operation)(42, None))
    assert result is (False if operation == 'has_attached_channel' else None)
    assert len(calls) == 1


@pytest.mark.parametrize('change', [
    {'user_id': 'telegram:42'}, {'user_id': None},
    {'workspace_id': 'ws-other'}, {'workspace_id': None}])
def test_inconsistent_pending_owner_or_workspace_is_not_returned(transport, change):
    state, calls = transport
    state['row'].update(change)
    assert asyncio.run(SaasBackend().pending_channel(42, None)) is None
    assert len(calls) == 2
    assert calls[1].url.params['user_id'] == 'email:canonical'


def test_no_pending_row_keeps_resolved_scope_without_workspace_lookup(transport):
    state, calls = transport
    state['row'] = None
    assert asyncio.run(SaasBackend().pending_channel(42, None)) is None
    assert len(calls) == 2


def test_delayed_resolution_is_not_repeated_or_replaced(transport):
    state, calls = transport
    async def identity(request):
        await asyncio.sleep(0)
        assert len(calls) == 1, 'A second ensure could choose another actor'
        return 'email:canonical', 'ws-email-canonical'
    state['identity'] = identity
    state['pending'] = lambda request: state['row'] if request.url.params['user_id'] == 'email:canonical' else None
    result = asyncio.run(SaasBackend().pending_channel(42, None))
    assert result.workspace_id == 'ws-email-canonical' and len(calls) == 2


def test_concurrent_actor_resolutions_do_not_share_cached_identity(transport):
    state, calls = transport
    async def run():
        count = 0
        ready = asyncio.Event()
        async def identity(request):
            nonlocal count
            actor = json.loads(request.content)['telegram_user_id']
            count += 1
            if count == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 3)
            return 'email:' + str(actor), 'ws-email-' + str(actor)
        state['identity'] = identity
        state['pending'] = lambda request: {**state['row'], 'user_id': request.url.params['user_id']}
        backend = SaasBackend()
        return await asyncio.gather(backend.pending_channel(42, None), backend.pending_channel(43, None))
    a, b = asyncio.run(run())
    assert a.workspace_id == 'ws-email-42' and b.workspace_id == 'ws-email-43'
    assert len(calls) == 4
    assert sorted(r.url.params['user_id'] for r in calls if r.method == 'GET') == ['email:42', 'email:43']


def register():
    return SaasBackend().register_channel(telegram_user_id=42, telegram_username=None,
        telegram_chat_id=-100123, title='Own target', added_by_telegram_user_id=42)


def test_register_keeps_canonical_membership_pair(transport):
    state, calls = transport
    result = asyncio.run(register())
    assert result.workspace_id == 'ws-email-canonical'
    assert len(calls) == 2
    assert [r.url.path for r in calls] == ['/internal/bot/ensure-user', '/internal/bot/register-telegram-channel']


def test_register_resolution_failure_has_no_write_or_derived_principal(transport):
    state, calls = transport
    state['error'] = 409
    with pytest.raises(RuntimeError, match='Canonical Telegram identity is unavailable'):
        asyncio.run(register())
    assert len(calls) == 1


@pytest.mark.parametrize('change', [{'user_id': 'other-owner'}, {'workspace_id': 'other-workspace'}])
def test_register_changed_identity_is_refused_without_retry(transport, change):
    state, calls = transport
    state['row'].update(change)
    with pytest.raises(RuntimeError, match='Telegram registration identity changed'):
        asyncio.run(register())
    assert len(calls) == 2


def test_selfhost_never_uses_saas_identity_transport(transport):
    _, calls = transport
    backend = CoreDbBackend()
    assert asyncio.run(backend.resolve_workspace_id(42, None)) is None
    assert asyncio.run(backend.has_attached_channel(42, None)) is False
    assert asyncio.run(backend.pending_channel(42, None)) is None
    assert calls == []
