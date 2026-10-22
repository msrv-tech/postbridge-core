"""Retain the result of explicitly consented scheduled publication."""
from alembic import op
import sqlalchemy as sa
revision = '20261022_video_auto_pub'
down_revision = '20261022_video_batches'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('video_publication_approvals') as batch:
        batch.add_column(sa.Column('auto_agent_run_id', sa.String(36)))
        batch.create_foreign_key('fk_video_auto_publication_run', 'agent_runs', ['auto_agent_run_id'], ['id'], ondelete='RESTRICT')
    op.add_column('video_agent_executions', sa.Column('publication_json', sa.JSON(), nullable=False, server_default='{}'))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM video_publication_approvals WHERE auto_agent_run_id IS NOT NULL LIMIT 1")).first():
        raise RuntimeError('Automatic publication consent receipts must be retained')
    with op.batch_alter_table('video_publication_approvals') as batch:
        batch.drop_constraint('fk_video_auto_publication_run', type_='foreignkey')
        batch.drop_column('auto_agent_run_id')
    op.drop_column('video_agent_executions', 'publication_json')
