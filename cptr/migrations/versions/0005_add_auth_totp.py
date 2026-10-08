"""add two-step sign-in (TOTP) and session revocation to auths

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Base32 TOTP secret. Set but not enabled while the user is confirming setup.
    op.add_column("auths", sa.Column("totp_secret", sa.Text(), nullable=True))
    op.add_column("auths", sa.Column("totp_enabled_at", sa.BigInteger(), nullable=True))
    # Last accepted time step, so a code can't be replayed.
    op.add_column("auths", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    # JSON list of sha256 hashes of unused recovery codes.
    op.add_column("auths", sa.Column("recovery_codes", sa.Text(), nullable=True))
    # Epoch seconds. Session tokens issued before this are refused.
    op.add_column("auths", sa.Column("sessions_valid_after", sa.Float(), nullable=True))


def downgrade() -> None:
    for column in (
        "sessions_valid_after",
        "recovery_codes",
        "totp_last_step",
        "totp_enabled_at",
        "totp_secret",
    ):
        op.drop_column("auths", column)
