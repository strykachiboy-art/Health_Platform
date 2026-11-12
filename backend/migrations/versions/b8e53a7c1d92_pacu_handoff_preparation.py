"""CE1-S08 PACU preparation and content-review evidence (source-only).

Revision ID: b8e53a7c1d92
Revises: a7c41f9e2d60
Do NOT apply to the application database during S08 focused test gates.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b8e53a7c1d92'
down_revision = 'a7c41f9e2d60'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('surgical_pacu_preparations',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('clinic_id',sa.Integer(),sa.ForeignKey('clinics.id'),nullable=False),
        sa.Column('patient_id',sa.Integer(),sa.ForeignKey('patients.id'),nullable=False),
        sa.Column('admission_id',sa.Integer(),sa.ForeignKey('admissions.id'),nullable=False),
        sa.Column('surgical_case_id',sa.Integer(),sa.ForeignKey('surgical_cases.id'),nullable=False),
        sa.Column('journal_id',sa.Integer(),sa.ForeignKey('surgical_evidence_journals.id'),nullable=False),
        sa.Column('journal_seal_sha256',sa.String(64),nullable=False),
        sa.Column('case_version',sa.Integer(),nullable=False),
        sa.Column('booking_version',sa.Integer(),nullable=False),
        sa.Column('outgoing_nurse_staff_id',sa.Integer(),sa.ForeignKey('staff.id'),nullable=False),
        sa.Column('receiving_nurse_staff_id',sa.Integer(),sa.ForeignKey('staff.id'),nullable=False),
        sa.Column('created_at',sa.DateTime(),nullable=False),
        sa.Column('created_by_staff_id',sa.Integer(),sa.ForeignKey('staff.id'),nullable=False),
        sa.Column('situation',sa.String(2000),nullable=False),
        sa.Column('background',sa.String(2000),nullable=False),
        sa.Column('assessment',sa.String(2000),nullable=False),
        sa.Column('recommendation',sa.String(2000),nullable=False),
        sa.Column('safety_concerns',sa.String(2000),nullable=False),
        sa.Column('pending_actions',sa.String(2000),nullable=False),
        sa.Column('airway_plan',sa.String(1200),nullable=False),
        sa.Column('analgesia_plan',sa.String(1200),nullable=False),
        sa.Column('escalation_plan',sa.String(1200),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('source_sha256',sa.String(64),nullable=False),
        sa.Column('content_sha256',sa.String(64),nullable=False),
        sa.Column('prepared_at',sa.DateTime(),nullable=True),
        sa.Column('prepared_by_staff_id',sa.Integer(),sa.ForeignKey('staff.id'),nullable=True),
        sa.Column('prepared_version',sa.Integer(),nullable=True),
        sa.Column('prepared_sha256',sa.String(64),nullable=True),
        sa.Column('reviewed_at',sa.DateTime(),nullable=True),
        sa.Column('reviewed_by_staff_id',sa.Integer(),sa.ForeignKey('staff.id'),nullable=True),
        sa.Column('reviewed_version',sa.Integer(),nullable=True),
        sa.Column('review_sha256',sa.String(64),nullable=True),
        sa.UniqueConstraint('surgical_case_id',name='uq_surgical_pacu_preparation_case'),
        sa.UniqueConstraint('journal_id',name='uq_surgical_pacu_preparation_journal'),
        sa.CheckConstraint("status IN ('draft','prepared','reviewed')",name='ck_surgical_pacu_preparation_status'),
        sa.CheckConstraint('version >= 1',name='ck_surgical_pacu_preparation_version'),
        sa.CheckConstraint('outgoing_nurse_staff_id != receiving_nurse_staff_id',name='ck_surgical_pacu_distinct_nurses'),
        sa.CheckConstraint('length(source_sha256) = 64 AND length(content_sha256) = 64',name='ck_surgical_pacu_preparation_digests'),
        sa.CheckConstraint("status = 'draft' OR (prepared_at IS NOT NULL AND prepared_by_staff_id IS NOT NULL "
            "AND prepared_version IS NOT NULL AND prepared_sha256 IS NOT NULL "
            "AND length(prepared_sha256) = 64)",name='ck_surgical_pacu_preparation_prepared'),
        sa.CheckConstraint("status != 'reviewed' OR (reviewed_at IS NOT NULL AND reviewed_by_staff_id IS NOT NULL "
            "AND reviewed_version IS NOT NULL AND review_sha256 IS NOT NULL "
            "AND length(review_sha256) = 64)",name='ck_surgical_pacu_preparation_reviewed'),
    )
    op.create_index('ix_surgical_pacu_preparations_clinic_id','surgical_pacu_preparations',['clinic_id'])
    op.create_index('ix_surgical_pacu_clinic_admission_status','surgical_pacu_preparations',['clinic_id','admission_id','status'])
    op.create_table('surgical_pacu_observations',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('clinic_id',sa.Integer(),sa.ForeignKey('clinics.id'),nullable=False),
        sa.Column('preparation_id',sa.Integer(),sa.ForeignKey('surgical_pacu_preparations.id'),nullable=False),
        sa.Column('sequence',sa.Integer(),nullable=False),
        sa.Column('observed_by_staff_id',sa.Integer(),sa.ForeignKey('staff.id'),nullable=False),
        sa.Column('observed_at',sa.DateTime(),nullable=False),
        sa.Column('heart_rate_bpm',sa.Integer(),nullable=False),
        sa.Column('systolic_bp',sa.Integer(),nullable=False),
        sa.Column('diastolic_bp',sa.Integer(),nullable=False),
        sa.Column('spo2_percent',sa.Integer(),nullable=False),
        sa.Column('respiratory_rate',sa.Integer(),nullable=False),
        sa.Column('temperature_tenths_c',sa.Integer(),nullable=False),
        sa.Column('pain_score',sa.Integer(),nullable=False),
        sa.Column('consciousness',sa.String(16),nullable=False),
        sa.Column('airway_patent',sa.Boolean(),nullable=False),
        sa.Column('bleeding_concern',sa.Boolean(),nullable=False),
        sa.Column('escalation_communicated',sa.Boolean(),nullable=False),
        sa.Column('note',sa.String(1200),nullable=False),
        sa.Column('evidence_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('preparation_id','sequence',name='uq_surgical_pacu_observation_sequence'),
        sa.CheckConstraint('sequence >= 1',name='ck_surgical_pacu_observation_sequence'),
        sa.CheckConstraint('heart_rate_bpm BETWEEN 20 AND 250',name='ck_surgical_pacu_heart_rate'),
        sa.CheckConstraint('systolic_bp BETWEEN 40 AND 300',name='ck_surgical_pacu_systolic'),
        sa.CheckConstraint('diastolic_bp BETWEEN 20 AND 200 AND diastolic_bp < systolic_bp',name='ck_surgical_pacu_diastolic'),
        sa.CheckConstraint('spo2_percent BETWEEN 0 AND 100',name='ck_surgical_pacu_spo2'),
        sa.CheckConstraint('respiratory_rate BETWEEN 1 AND 70',name='ck_surgical_pacu_respiration'),
        sa.CheckConstraint('temperature_tenths_c BETWEEN 300 AND 450',name='ck_surgical_pacu_temperature'),
        sa.CheckConstraint('pain_score BETWEEN 0 AND 10',name='ck_surgical_pacu_pain'),
        sa.CheckConstraint("consciousness IN ('alert','voice','pain','unresponsive')",name='ck_surgical_pacu_consciousness'),
        sa.CheckConstraint('length(evidence_sha256) = 64',name='ck_surgical_pacu_observation_digest'),
    )
    op.create_index('ix_surgical_pacu_observations_clinic_id','surgical_pacu_observations',['clinic_id'])
    op.create_index('ix_surgical_pacu_observations_preparation_id','surgical_pacu_observations',['preparation_id'])
    op.create_index('ix_surgical_pacu_observation_clinic_preparation','surgical_pacu_observations',['clinic_id','preparation_id'])


def downgrade():
    op.drop_index('ix_surgical_pacu_observation_clinic_preparation',table_name='surgical_pacu_observations')
    op.drop_index('ix_surgical_pacu_observations_preparation_id',table_name='surgical_pacu_observations')
    op.drop_index('ix_surgical_pacu_observations_clinic_id',table_name='surgical_pacu_observations')
    op.drop_table('surgical_pacu_observations')
    op.drop_index('ix_surgical_pacu_clinic_admission_status',table_name='surgical_pacu_preparations')
    op.drop_index('ix_surgical_pacu_preparations_clinic_id',table_name='surgical_pacu_preparations')
    op.drop_table('surgical_pacu_preparations')
