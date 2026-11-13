"""Inspect actual publisher createRecord JSON through a closed HTTP transport."""
import json
from types import SimpleNamespace

import httpx
import pytest

from postbridge.api.schemas import BlueskyCredentials
from postbridge.domain.errors import ExternalApiError
from postbridge.domain.models import PostPayload
from postbridge.integrations.bluesky.publisher import BlueskyPublisher


@pytest.fixture
def publish(monkeypatch):
    calls = []
    records = []
    state = {'status': 200, 'media_type': 'image/png'}
    def transport(request):
        calls.append(request)
        path = request.url.path
        if path.endswith('/com.atproto.server.createSession'):
            return httpx.Response(200, json={'did': 'did:plc:synthetic', 'accessJwt': 'synthetic-jwt'})
        if str(request.url) == 'https://cdn.invalid/image.png':
            return httpx.Response(200, content=b'png-bytes', headers={'content-type': state['media_type']})
        if path.endswith('/com.atproto.repo.uploadBlob'):
            assert request.content == b'png-bytes'
            return httpx.Response(200, json={'blob': {'ref': {'$link': 'synthetic-blob'}, 'mimeType': 'image/png', 'size': 9}})
        if path.endswith('/com.atproto.repo.createRecord'):
            payload = json.loads(request.content)
            assert payload['repo'] == 'did:plc:synthetic' and payload['collection'] == 'app.bsky.feed.post'
            assert request.headers['Authorization'] == 'Bearer synthetic-jwt'
            records.append(payload['record'])
            return httpx.Response(state['status'], json={'uri': 'at://did:plc:synthetic/app.bsky.feed.post/one'})
        pytest.fail('Unexpected URL metadata/provider request: ' + str(request.url))
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(transport)))
    def perform(text, *, media=False):
        result = BlueskyPublisher(settings=SimpleNamespace(bluesky_service_url='https://bsky.invalid')).publish_post(
            '', PostPayload(source_post_id='synthetic', text=text,
                media_url='https://cdn.invalid/image.png' if media else None),
            credentials=BlueskyCredentials(identifier='synthetic.invalid', app_password='synthetic'))
        assert result == 'at://did:plc:synthetic/app.bsky.feed.post/one'
        return records[-1]
    return perform, calls, state


def links(record):
    result = []
    previous_end = 0
    data = record['text'].encode('utf-8')
    for facet in record.get('facets', []):
        index = facet['index']
        assert previous_end <= index['byteStart'] < index['byteEnd'] <= len(data)
        visible = data[index['byteStart']:index['byteEnd']].decode('utf-8')
        assert facet['features'][0]['$type'] == 'app.bsky.richtext.facet#link'
        assert len(facet['features']) == 1
        result.append((visible, facet['features'][0]['uri'], index))
        previous_end = index['byteEnd']
    return result


@pytest.mark.parametrize('prefix', ['', 'ASCII ', 'Текст 🐈 ', '👨‍👩‍👧‍👦 e\u0301 ✨ '])
def test_link_ranges_use_final_utf8_bytes_not_characters(publish, prefix):
    perform, calls, _ = publish
    uri = 'https://postbridge.io/news/complete-article'
    record = perform('  ' + prefix + uri + '  ')
    assert record['text'] == prefix + uri
    assert links(record) == [(uri, uri, {'byteStart': len(prefix.encode('utf-8')),
        'byteEnd': len((prefix + uri).encode('utf-8'))})]
    assert len(calls) == 2 and all(r.method == 'POST' for r in calls)


def test_multiple_and_repeated_urls_have_distinct_exact_ranges(publish):
    perform, _, _ = publish
    a, b = 'https://example.com/a', 'http://two.example/b?x=1&y=2#section'
    text = '🚀 ' + a + '\n' + b + ' again ' + a
    record = perform(text)
    assert record['text'] == text
    found = links(record)
    assert [(v, uri) for v, uri, _ in found] == [(a, a), (b, b), (a, a)]
    assert found[0][2]['byteStart'] != found[2][2]['byteStart']


@pytest.mark.parametrize('text,visible', [
    ('Read https://example.com/article.', 'https://example.com/article'),
    ('(https://example.com/article),', 'https://example.com/article'),
    ('[https://example.com/a_(b)].', 'https://example.com/a_(b)'),
    ('“https://example.com/a?x=a,b&y=1”;', 'https://example.com/a?x=a,b&y=1'),
    ("'https://example.com/o'connor'", "https://example.com/o'connor"),
    ('See https://example.com/a_(b))).', 'https://example.com/a_(b)'),
    ('Use https://example.com/%28ok%29!', 'https://example.com/%28ok%29'),
])
def test_prose_punctuation_is_outside_link_and_balanced_url_data_survives(publish, text, visible):
    perform, _, _ = publish
    record = perform(text)
    assert record['text'] == text
    assert [(v, uri) for v, uri, _ in links(record)] == [(visible, visible)]


@pytest.mark.parametrize('text', [
    'javascript:alert(1)', 'javascript:https://example.com/unsafe',
    'data:text/html,https://example.com/unsafe', 'file:///tmp/a',
    'https://', 'https://user:password@example.com/a',
    'https://example.com:bad/a', 'https://example.com:99999/a', 'https://example.com:/a',
    'https://example..com/a', 'https://-example.com/a', 'https://example.com/%ZZ',
    'https://example.com/%0Ainjected', 'https://example.com/a\\b',
    'https://example.com/a\u200bhidden', 'https://exa\x00mple.com/a',
])
def test_unsafe_or_malformed_uri_is_plain_text_without_remote_fetch(publish, text):
    perform, calls, _ = publish
    record = perform(text)
    assert record['text'] == text
    assert 'facets' not in record and len(calls) == 2


def test_unicode_url_keeps_visible_iri_and_uses_ascii_uri_destination(publish):
    perform, _, _ = publish
    visible = 'https://bücher.example/статья?q=да#тема'
    destination = 'https://xn--bcher-kva.example/%D1%81%D1%82%D0%B0%D1%82%D1%8C%D1%8F?q=%D0%B4%D0%B0#%D1%82%D0%B5%D0%BC%D0%B0'
    record = perform('📚 ' + visible)
    assert [(v, uri) for v, uri, _ in links(record)] == [(visible, destination)]


@pytest.mark.parametrize('url', ['https://example.com:8443/a', 'HTTPS://EXAMPLE.COM/a', 'http://[2001:db8::1]/a'])
def test_valid_http_forms_keep_original_ascii_destination(publish, url):
    perform, _, _ = publish
    assert [(v, uri) for v, uri, _ in links(perform(url))] == [(url, url)]


def test_url_truncated_mid_token_has_no_fabricated_shortened_destination(publish):
    perform, _, _ = publish
    source = 'x' * 275 + ' https://example.com/long-complete-article-token'
    record = perform(source)
    # Existing publisher trim is preserved, including its ellipsis behavior.
    assert record['text'] == source[:299].rstrip() + '...'
    assert 'facets' not in record


@pytest.mark.parametrize('text', ['Read https://example.com/part...',
    'Read https://example.com/part…', '(https://example.com/part...).'])
def test_already_abbreviated_source_url_is_plain_text(publish, text):
    perform, calls, _ = publish
    record = perform(text)
    assert record['text'] == text and 'facets' not in record and len(calls) == 2


def test_only_complete_links_before_cutoff_survive(publish):
    perform, _, _ = publish
    first = 'https://example.com/first'
    source = '😺 ' + first + ' ' + 'x' * 250 + ' https://example.com/not-in-final-text'
    record = perform(source)
    assert record['text'] == source[:299].rstrip() + '...'
    assert [(v, uri) for v, uri, _ in links(record)] == [(first, first)]


def test_url_completed_at_trim_boundary_can_still_be_linked(publish):
    perform, _, _ = publish
    url = 'https://example.com/complete'
    source = 'x' * (298 - len(url)) + ' ' + url + ' more text omitted'
    record = perform(source)
    assert record['text'].endswith(url + '...')
    assert [(v, uri) for v, uri, _ in links(record)] == [(url, url)]


@pytest.mark.parametrize('text', ['No links; #tag @person', '', '   ', 'Plain text with emoji 🐈'])
def test_without_links_record_shape_is_preserved(publish, text):
    perform, calls, _ = publish
    record = perform(text)
    assert record['text'] == (text.strip() or ' ')
    assert 'facets' not in record and 'embed' not in record and len(calls) == 2


@pytest.mark.parametrize('text', ['Image without URL', 'Image https://postbridge.io/news/article'])
def test_link_facets_do_not_replace_or_fetch_image_embed(publish, text):
    perform, calls, _ = publish
    record = perform(text, media=True)
    assert record['embed']['$type'] == 'app.bsky.embed.images'
    assert record['embed']['images'][0]['image']['ref']['$link'] == 'synthetic-blob'
    assert len(record.get('facets', [])) == (1 if 'https://' in text else 0)
    assert [r.method for r in calls] == ['POST', 'GET', 'POST', 'POST']
    assert [str(r.url) for r in calls if r.method == 'GET'] == ['https://cdn.invalid/image.png']


def test_original_error_and_no_retry_behavior_is_preserved(publish):
    perform, calls, state = publish
    state['status'] = 503
    with pytest.raises(ExternalApiError) as error:
        perform('https://example.com/a')
    assert error.value.code == 'EXTERNAL_API_BLUESKY_HTTP_ERROR' and error.value.retryable
    assert len(calls) == 2
