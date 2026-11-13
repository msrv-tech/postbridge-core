"""Local HTTP(S) link facets for the final Bluesky text; no URL requests."""
from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import quote, unquote, urlsplit, urlunsplit


_URL = re.compile(r'(?<![\w@:/])https?://[^\s<>"`]+', re.IGNORECASE)
_TRAILING = ".,!?;:…'’”"
_CLOSING = {')': '(', ']': '[', '}': '{'}
_HOST_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', re.IGNORECASE)


def _link_end(token: str) -> int:
    """Exclude prose punctuation and unmatched enclosing brackets, not URL data."""
    end = len(token)
    while end:
        value = token[:end]
        if value.endswith(("...", "…")):
            return 0  # Already abbreviated upstream: never invent its target.
        last = value[-1]
        if last in _TRAILING or (
            last in _CLOSING and value.count(last) > value.count(_CLOSING[last])
        ):
            end -= 1
        else:
            break
    return end


def _destination(value: str) -> str | None:
    if '\\' in value or re.search(r'%(?![0-9a-fA-F]{2})', value):
        return None
    if any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} for c in unquote(value)):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if (parsed.scheme not in {'http', 'https'} or not host or parsed.netloc.endswith(':')
                or parsed.username is not None or parsed.password is not None):
            return None
        try:
            ip = ipaddress.ip_address(host)
            netloc = '[' + host + ']' if ip.version == 6 else host
        except ValueError:
            ascii_host = host.encode('idna').decode('ascii')
            labels = ascii_host.split('.')
            # Conservative public-link discovery: no incomplete single-label
            # host, empty label, malformed numeric IPv4 or invalid DNS label.
            if (len(labels) < 2 or all(label.isdigit() for label in labels)
                    or any(not _HOST_LABEL.fullmatch(label) for label in labels)):
                return None
            netloc = ascii_host
        if port is not None:
            netloc += ':' + str(port)
        if value.isascii():
            return value
        # Preserve the visible IRI; its destination is an ASCII URI.
        return urlunsplit((parsed.scheme, netloc,
            quote(parsed.path, safe="/%:@!$&'()*+,;=-._~"),
            quote(parsed.query, safe="/?%:@!$&'()*+,;=-._~"),
            quote(parsed.fragment, safe="/?%:@!$&'()*+,;=-._~")))
    except (ValueError, UnicodeError):
        return None


def link_facets(text: str, *, source_text: str) -> list[dict[str, object]]:
    """Annotate only complete source URLs which survived the publisher's trim.

    `text` is the exact submitted record text. `source_text` is its stripped
    untruncated source, used only to avoid recognizing a cut URL as a new URL.
    """
    if text == source_text:
        retained = len(text)
    elif text.endswith('...') and source_text.startswith(text[:-3]):
        retained = len(text) - 3
    else:
        return []
    facets: list[dict[str, object]] = []
    for match in _URL.finditer(source_text):
        start = match.start()
        token_prefix = re.split(r'[\s<>"`]', source_text[:start])[-1]
        if re.search(r'[a-z][a-z0-9+.-]*:', token_prefix, re.IGNORECASE):
            continue  # Do not link an embedded HTTP substring of another URI.
        end = start + _link_end(match.group())
        if end > retained or end <= start:
            continue
        visible = source_text[start:end]
        if text[start:end] != visible:
            continue
        uri = _destination(visible)
        if uri is None:
            continue
        facets.append({'index': {
            'byteStart': len(text[:start].encode('utf-8')),
            'byteEnd': len(text[:end].encode('utf-8'))},
            'features': [{'$type': 'app.bsky.richtext.facet#link', 'uri': uri}]})
    return facets
