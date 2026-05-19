# Alembic Migrations

The public repository starts from a squashed baseline migration.

Current root revision: `20260421_tenant_image_style` in `versions/20260421_tenant_image_style.py`.
It is a no-op bridge for hosted databases that already reached the final
pre-public Core migration. The first schema-building public revision is
`20260516_public_baseline` in `versions/20260516_public_baseline.py`.

New databases should run:

```bash
alembic upgrade head
```

Existing private or hosted databases should be stamped only after their schema has been verified against the baseline.
