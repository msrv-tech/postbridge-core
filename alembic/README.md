# Alembic Migrations

The public repository starts from a squashed baseline migration.

Current root revision: `20260516_public_baseline` in `versions/20260516_public_baseline.py`.

New databases should run:

```bash
alembic upgrade head
```

Existing private or hosted databases should be stamped only after their schema has been verified against the baseline.
