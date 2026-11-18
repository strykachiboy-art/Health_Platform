"""GAP-A organization ownership; root-family backfill, no production authorization.

Migration must be applied only to an approved isolated disposable database.
Existing clinic ids, parent ids and headquarters flags are never updated.
"""
from uuid import uuid4
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'a6b71c2d9e40'
down_revision = 'f7a9e20bd613'
branch_labels = None
depends_on = None


def _root_for(clinic_id, parents, seen=None):
    if seen is None:
        seen = set()
    current = clinic_id
    while True:
        if current in seen:
            raise RuntimeError('GAP-A: clinic hierarchy contains a cycle')
        seen.add(current)
        if current not in parents:
            raise RuntimeError('GAP-A: clinic parent is missing')
        parent = parents[current]
        if parent is None:
            return current
        current = parent


def upgrade():
    conn = op.get_bind()
    # Fail before touching schema if hierarchy is inconsistent.
    existing = conn.execute(sa.text('SELECT id, parent_clinic_id, name, is_headquarters FROM clinics ORDER BY id')).mappings().all()
    parents = {r['id']: r['parent_clinic_id'] for r in existing}
    roots = {cid: _root_for(cid, parents) for cid in parents}
    names = {r['id']: r['name'] for r in existing}
    original = {(r['id'], r['parent_clinic_id'], r['is_headquarters']) for r in existing}

    op.create_table('hospital_organizations',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.add_column('clinics', sa.Column('organization_id', UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_clinics_organization_id', 'clinics', 'hospital_organizations', ['organization_id'], ['id'])

    organizations = {}
    for root in sorted(set(roots.values())):
        oid = uuid4()
        organizations[root] = oid
        conn.execute(sa.text('INSERT INTO hospital_organizations (id, name) VALUES (:id, :name)'), {'id': oid, 'name': names[root]})
    for cid, root in roots.items():
        conn.execute(sa.text('UPDATE clinics SET organization_id = :oid WHERE id = :id'), {'oid': organizations[root], 'id': cid})

    post = conn.execute(sa.text('SELECT id, parent_clinic_id, is_headquarters, organization_id FROM clinics')).mappings().all()
    post_original = {(r['id'], r['parent_clinic_id'], r['is_headquarters']) for r in post}
    if original != post_original or len(existing) != len(post):
        raise RuntimeError('GAP-A: clinic count/hierarchy changed')
    post_org = {r['id']: r['organization_id'] for r in post}
    if any(post_org[cid] is None or post_org[cid] != post_org[root] for cid, root in roots.items()):
        raise RuntimeError('GAP-A: missing or inconsistent organization ownership')
    op.alter_column('clinics', 'organization_id', existing_type=UUID(as_uuid=True), nullable=False)
    op.create_index('ix_clinics_organization_id', 'clinics', ['organization_id'])
    # Enforce that branch parents belong to the same organization at DB level.
    op.create_unique_constraint('uq_clinics_id_organization_id', 'clinics', ['id', 'organization_id'])
    op.create_foreign_key('fk_clinics_parent_same_organization', 'clinics', 'clinics',
        ['parent_clinic_id', 'organization_id'], ['id', 'organization_id'])
    op.create_table('tenant_memberships',
        sa.Column('id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('hospital_organizations.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('organization_id', 'user_id', name='uq_tenant_memberships_org_user'),
    )
    op.create_index('ix_tenant_memberships_user_id', 'tenant_memberships', ['user_id'])
    op.create_index('ix_tenant_memberships_organization_id', 'tenant_memberships', ['organization_id'])


def downgrade():
    conn = op.get_bind()
    # Reversal is intentionally blocked if operators have created membership rows.
    if conn.execute(sa.text('SELECT COUNT(*) FROM tenant_memberships')).scalar_one() != 0:
        raise RuntimeError('GAP-A: remove/archive memberships under explicit authorization before downgrade')
    op.drop_index('ix_tenant_memberships_organization_id', table_name='tenant_memberships')
    op.drop_index('ix_tenant_memberships_user_id', table_name='tenant_memberships')
    op.drop_table('tenant_memberships')
    op.drop_constraint('fk_clinics_parent_same_organization', 'clinics', type_='foreignkey')
    op.drop_constraint('uq_clinics_id_organization_id', 'clinics', type_='unique')
    op.drop_index('ix_clinics_organization_id', table_name='clinics')
    op.drop_constraint('fk_clinics_organization_id', 'clinics', type_='foreignkey')
    op.drop_column('clinics', 'organization_id')
    op.drop_table('hospital_organizations')
