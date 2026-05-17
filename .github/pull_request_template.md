## Summary

-

## Testing

- [ ] `docker compose --progress=plain -f ci/docker-compose.yml build`
- [ ] `docker compose -f ci/docker-compose.yml run --rm test`

## Checklist

- [ ] I did not commit secrets, `.env` files, local databases, or generated build artifacts.
- [ ] Browser-facing changes do not expose service tokens or internal credentials.
- [ ] Public-facing text and documentation are in English.

