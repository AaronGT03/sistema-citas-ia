"""add prestadores

Revision ID: 98eb17bab316
Revises:
Create Date: 2026-07-17 01:33:23.246463

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '98eb17bab316'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "prestadores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column("telefono", sa.String(), nullable=True),
        sa.Column("correo", sa.String(), nullable=True),
        sa.Column("descripcion", sa.String(), nullable=True),
        sa.Column("hora_inicio", sa.String(), nullable=True),
        sa.Column("hora_fin", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_prestadores_empresa_id"), "prestadores", ["empresa_id"], unique=False
    )
    op.create_index(op.f("ix_prestadores_id"), "prestadores", ["id"], unique=False)

    op.create_table(
        "prestadores_servicios",
        sa.Column("prestador_id", sa.Integer(), nullable=False),
        sa.Column("servicio_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["prestador_id"], ["prestadores.id"]),
        sa.ForeignKeyConstraint(["servicio_id"], ["servicios.id"]),
        sa.PrimaryKeyConstraint("prestador_id", "servicio_id"),
    )

    # batch_alter_table: en SQLite las tablas existentes se alteran
    # recreando la tabla (no soporta ALTER ... ADD CONSTRAINT); en
    # Postgres/otros dialectos ejecuta los ALTER directamente.
    with op.batch_alter_table("citas") as batch_op:
        batch_op.add_column(sa.Column("prestador_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            op.f("ix_citas_prestador_id"), ["prestador_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_citas_prestador_id", "prestadores", ["prestador_id"], ["id"]
        )

    with op.batch_alter_table("conversaciones") as batch_op:
        batch_op.add_column(sa.Column("prestador_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "asignacion_automatica",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_foreign_key(
            "fk_conversaciones_prestador_id",
            "prestadores",
            ["prestador_id"],
            ["id"],
        )

    with op.batch_alter_table("empresas") as batch_op:
        batch_op.add_column(
            sa.Column(
                "usa_prestadores",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("empresas") as batch_op:
        batch_op.drop_column("usa_prestadores")

    with op.batch_alter_table("conversaciones") as batch_op:
        batch_op.drop_constraint("fk_conversaciones_prestador_id", type_="foreignkey")
        batch_op.drop_column("asignacion_automatica")
        batch_op.drop_column("prestador_id")

    with op.batch_alter_table("citas") as batch_op:
        batch_op.drop_constraint("fk_citas_prestador_id", type_="foreignkey")
        batch_op.drop_index(op.f("ix_citas_prestador_id"))
        batch_op.drop_column("prestador_id")

    op.drop_table("prestadores_servicios")
    op.drop_index(op.f("ix_prestadores_id"), table_name="prestadores")
    op.drop_index(op.f("ix_prestadores_empresa_id"), table_name="prestadores")
    op.drop_table("prestadores")
