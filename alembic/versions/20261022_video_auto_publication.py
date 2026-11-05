"""Retain the result of explicitly consented scheduled publication."""
from alembic import op
import sqlalchemy as sa
revision = '20261022_video_auto_pub'
down_revision = '20261022_video_batches'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    approval_columns = {column['name'] for column in inspector.get_columns('video_publication_approvals')}
    has_reference = any(fk['constrained_columns'] == ['auto_agent_run_id'] and fk['referred_table'] == 'agent_runs'
                        for fk in inspector.get_foreign_keys('video_publication_approvals'))
    with op.batch_alter_table('video_publication_approvals') as batch:
        if 'auto_agent_run_id' not in approval_columns:
            batch.add_column(sa.Column('auto_agent_run_id', sa.String(36)))
        if not has_reference:
            batch.create_foreign_key('fk_video_auto_publication_run', 'agent_runs', ['auto_agent_run_id'], ['id'], ondelete='RESTRICT')
    if 'publication_json' not in {column['name'] for column in inspector.get_columns('video_agent_executions')}:
        op.add_column('video_agent_executions', sa.Column('publication_json', sa.JSON(), nullable=False, server_default='{}'))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM video_publication_approvals WHERE auto_agent_run_id IS NOT NULL LIMIT 1")).first():
        raise RuntimeError('Automatic publication consent receipts must be retained')
    with op.batch_alter_table('video_publication_approvals') as batch:
        batch.drop_constraint('fk_video_auto_publication_run', type_='foreignkey')
        batch.drop_column('auto_agent_run_id')
    op.drop_column('video_agent_executions', 'publication_json')
