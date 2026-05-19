"""legacy hosted core bridge

Revision ID: 20260421_tenant_image_style
Revises:

This no-op revision lets hosted databases that already reached the final
pre-public Core migration move onto the public squashed migration chain.
Fresh self-host databases also pass through it before the public baseline.
"""

from __future__ import annotations

from typing import Sequence, Union

revision: str = "20260421_tenant_image_style"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
