"""Cache acoustic word alignment for verified generated speech."""
from alembic import op
import sqlalchemy as sa
revision = '20261023_video_speech_align'
down_revision = '20261022_video_auto_pub'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('video_speech_assets', sa.Column('alignment_json', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('video_speech_assets', 'alignment_json')
