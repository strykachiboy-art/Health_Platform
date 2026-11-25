"""Patient problems, dependent on both published clinical and SaaS histories.

No clinical content is inferred or backfilled. Only disposable DB validation
is authorized. This revision is a merge point with new feature DDL.
"""
from alembic import op
import sqlalchemy as sa
revision = 'c8261124a010'
down_revision = ('c8261124a009', 'b4f6e8c20d51')
branch_labels = None
depends_on = None
OLD = "source_record_type IN ('consultation_note','nursing_note','medication_administration','medication_reconciliation','discharge_summary','lab_report','staged_radiology_report')"
NEW = OLD[:-1] + ",'patient_problem')"
CHECK = 'ck_clinical_record_amendments_type'


def upgrade():
    op.create_unique_constraint('uq_patients_id_clinic', 'patients', ['id', 'clinic_id'])
    op.drop_constraint(CHECK, 'clinical_record_amendments', type_='check')
    op.create_check_constraint(CHECK, 'clinical_record_amendments', NEW)
    op.create_table('patient_problems',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('clinic_id', sa.Integer(), nullable=False),
        sa.Column('patient_id', sa.Integer(), nullable=False),
        sa.Column('clinical_status', sa.String(24), nullable=False),
        sa.Column('verification_status', sa.String(24), nullable=False),
        sa.Column('content', sa.JSON(), nullable=False),
        sa.Column('original_snapshot', sa.JSON(), nullable=False),
        sa.Column('consultation_ids', sa.JSON(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('recorded_by_staff_id', sa.Integer(), nullable=False),
        sa.Column('reviewed_by_staff_id', sa.Integer(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('confirmed_by_staff_id', sa.Integer(), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['clinic_id','organization_id'], ['clinics.id','clinics.organization_id'], name='fk_patient_problems_clinic_org'),
        sa.ForeignKeyConstraint(['patient_id','clinic_id'], ['patients.id','patients.clinic_id'], name='fk_patient_problems_patient_clinic'),
        sa.ForeignKeyConstraint(['recorded_by_staff_id','clinic_id'], ['staff.id','staff.clinic_id'], name='fk_patient_problems_actor_clinic'),
        sa.ForeignKeyConstraint(['reviewed_by_staff_id','clinic_id'], ['staff.id','staff.clinic_id'], name='fk_patient_problems_review_clinic'),
        sa.ForeignKeyConstraint(['confirmed_by_staff_id','clinic_id'], ['staff.id','staff.clinic_id'], name='fk_patient_problems_confirm_clinic'),
        sa.CheckConstraint('(confirmed_by_staff_id IS NULL) = (confirmed_at IS NULL)', name='ck_patient_problems_confirmation_pair'),
        sa.CheckConstraint('version >= 1', name='ck_patient_problems_version'),
        sa.CheckConstraint("clinical_status IN ('active','inactive','resolved','remission','recurrence','relapse')", name='ck_patient_problems_clinical_status'),
        sa.CheckConstraint("verification_status IN ('unconfirmed','provisional','differential','confirmed','refuted','entered_in_error')", name='ck_patient_problems_verification_status'),
    )
    op.create_index('ix_patient_problems_scope_status','patient_problems',['organization_id','clinic_id','patient_id','clinical_status','id'])


def downgrade():
    conn = op.get_bind()
    if conn.execute(sa.text('SELECT COUNT(*) FROM patient_problems')).scalar_one() or conn.execute(sa.text("SELECT COUNT(*) FROM clinical_record_amendments WHERE source_record_type='patient_problem'")).scalar_one():
        raise RuntimeError('Problem-list clinical evidence exists; destructive downgrade denied')
    op.drop_table('patient_problems')
    op.drop_constraint(CHECK, 'clinical_record_amendments', type_='check')
    op.create_check_constraint(CHECK, 'clinical_record_amendments', OLD)
    op.drop_constraint('uq_patients_id_clinic', 'patients', type_='unique')
