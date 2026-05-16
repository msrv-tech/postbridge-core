# Telegram Proxy

Postbridge Core can reach Telegram in two ways:

- Telethon for channel history import through MTProto.
- Bot API for publishing and editing messages through `api.telegram.org`.

`TELEGRAM_PROXY_URL` configures one outbound proxy for both paths. An empty value means direct connections.

## URL Formats

| Scheme | Behavior |
| --- | --- |
| `socks5://host:port` | SOCKS5 with local DNS resolution. |
| `socks5h://host:port` | SOCKS5 with proxy-side DNS resolution. |
| `http://host:port` | HTTP CONNECT for Bot API HTTPS requests; HTTP proxy for Telethon through PySocks. |

Proxy credentials are supported, for example `socks5://user:pass@host:port`.

Unsupported schemes, such as `https://`, raise a configuration error.

## Host Proxy

If a proxy runs on the same host as Core:

```env
TELEGRAM_PROXY_URL=socks5://127.0.0.1:10808
```

Use `socks5h://` when local DNS cannot resolve Telegram endpoints reliably.

## Docker And Host Proxy

Inside a container, `127.0.0.1` is the container itself, not the host. Use the host gateway:

```env
TELEGRAM_PROXY_URL=socks5h://host.docker.internal:10808
```

Add this to the `api` and `worker` services when needed:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

The proxy on the host must listen on an interface reachable from Docker, not only on host-local `127.0.0.1`.

## Optional Direct Fallback

`TELEGRAM_PROXY_FALLBACK_DIRECT=false` is the default. When a proxy is configured, traffic goes only through the proxy and transport failures fail fast.

If `TELEGRAM_PROXY_FALLBACK_DIRECT=true`, Core makes one direct retry after proxy or transport errors such as connection failures, proxy errors, or gateway timeouts. Telegram logical responses such as `401`, `403`, or `429` do not trigger fallback.

The log marker is `telegram_proxy_fallback_used`.

## Benchmark

[`tools/benchmark_telegram_bot_api_proxy.py`](../tools/benchmark_telegram_bot_api_proxy.py) calls Bot API `getMe` with and without `TELEGRAM_PROXY_URL`. Set `TELEGRAM_BOT_TOKEN` in the environment before running it.
