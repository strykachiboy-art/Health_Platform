"""BB01 R02 independent lab evidence and clinical safety inbox."""
from alembic import op
import sqlalchemy as sa

revision = 'bb01a6c2b149'
down_revision = 'bb01c6e2b149'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('blood_lab_artifacts',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('clinic_id', sa.Integer, sa.ForeignKey('clinics.id'), nullable=False, index=True),
        sa.Column('reference', sa.String(255), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('marker', sa.String(80), nullable=False),
        sa.Column('specimen_identifier', sa.String(128), nullable=False),
        sa.Column('donation_id', sa.Integer, sa.ForeignKey('blood_donations.id'), nullable=True),
        sa.Column('blood_unit_id', sa.Integer, sa.ForeignKey('blood_units.id'), nullable=True),
        sa.Column('order_id', sa.Integer, sa.ForeignKey('blood_product_orders.id'), nullable=True),
        sa.Column('patient_id', sa.Integer, sa.ForeignKey('patients.id'), nullable=True),
        sa.Column('admission_id', sa.Integer, sa.ForeignKey('admissions.id'), nullable=True),
        sa.Column('result', sa.String(80), nullable=False),
        sa.Column('test_code', sa.String(80), nullable=False),
        sa.Column('source_reference', sa.String(255), nullable=False),
        sa.Column('lab_order_item_id', sa.Integer, sa.ForeignKey('lab_order_items.id'), nullable=True, unique=True),
        sa.Column('lab_result_snapshot', sa.JSON, nullable=True),
        sa.Column('policy_version', sa.String(80), nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('recorded_by_staff_id', sa.Integer, sa.ForeignKey('staff.id'), nullable=False),
        sa.UniqueConstraint('clinic_id', 'reference', name='uq_bbla_clinic_reference'),
        sa.CheckConstraint("kind IN ('screening','blood_group','compatibility','special_requirements','confirmatory','emergency_assessment')", name='ck_bbla_kind'),
        sa.CheckConstraint('(donation_id IS NOT NULL AND blood_unit_id IS NULL AND order_id IS NULL) OR (donation_id IS NULL AND blood_unit_id IS NOT NULL)', name='ck_bbla_subject'),
        sa.CheckConstraint("(kind IN ('compatibility','special_requirements','emergency_assessment') AND order_id IS NOT NULL) OR (kind NOT IN ('compatibility','special_requirements','emergency_assessment') AND order_id IS NULL)", name='ck_bbla_order_kind'),
        sa.CheckConstraint("(kind = 'compatibility' AND lab_order_item_id IS NOT NULL) OR (kind != 'compatibility' AND lab_order_item_id IS NULL)", name='ck_bbla_patient_lab_link'),
        sa.CheckConstraint('(order_id IS NULL AND patient_id IS NULL AND admission_id IS NULL) OR (order_id IS NOT NULL AND patient_id IS NOT NULL AND admission_id IS NOT NULL)', name='ck_bbla_order_context'),
    )
    op.create_table('blood_patient_releases',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('clinic_id', sa.Integer, sa.ForeignKey('clinics.id'), nullable=False, index=True),
        sa.Column('transfusion_id', sa.Integer, sa.ForeignKey('blood_transfusions.id'), nullable=False),
        sa.Column('patient_id', sa.Integer, sa.ForeignKey('patients.id'), nullable=False),
        sa.Column('recipient_user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('actor_staff_id', sa.Integer, sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('reference', sa.String(255), nullable=False),
        sa.Column('transfusion_version', sa.Integer, nullable=False),
        sa.Column('snapshot', sa.JSON, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('transfusion_id', 'transfusion_version', name='uq_bbpr_transfusion_version'),
    )
    op.create_table('blood_safety_inbox',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('clinic_id', sa.Integer, sa.ForeignKey('clinics.id'), nullable=False, index=True),
        sa.Column('event_key', sa.String(180), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('recipient_staff_id', sa.Integer, sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('recall_id', sa.Integer, sa.ForeignKey('blood_recall_evidence.id'), nullable=True),
        sa.Column('order_id', sa.Integer, sa.ForeignKey('blood_product_orders.id'), nullable=True),
        sa.Column('message', sa.String(500), nullable=False),
        sa.Column('notification_id', sa.Integer, sa.ForeignKey('notifications.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('clinic_id', 'event_key', 'recipient_staff_id', name='uq_bbsi_event_recipient'),
    )
    op.create_table('blood_investigation_events',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('clinic_id', sa.Integer, sa.ForeignKey('clinics.id'), nullable=False, index=True),
        sa.Column('donation_id', sa.Integer, sa.ForeignKey('blood_donations.id'), nullable=True),
        sa.Column('recall_id', sa.Integer, sa.ForeignKey('blood_recall_evidence.id'), nullable=True),
        sa.Column('artifact_id', sa.Integer, sa.ForeignKey('blood_lab_artifacts.id'), nullable=True),
        sa.Column('operation_identity', sa.String(128), nullable=False),
        sa.Column('outcome', sa.String(24), nullable=False),
        sa.Column('notes', sa.Text, nullable=False),
        sa.Column('actor_staff_id', sa.Integer, sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('(donation_id IS NOT NULL AND recall_id IS NULL) OR (donation_id IS NULL AND recall_id IS NOT NULL)', name='ck_bbie_subject'),
        sa.CheckConstraint("outcome IN ('opened','evidence_recorded','escalated','reviewed')", name='ck_bbie_outcome'),
        sa.UniqueConstraint('clinic_id', 'operation_identity', name='uq_bbie_operation'),
    )
    op.create_table('blood_lab_verifications',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('clinic_id', sa.Integer, sa.ForeignKey('clinics.id'), nullable=False, index=True),
        sa.Column('artifact_id', sa.Integer, sa.ForeignKey('blood_lab_artifacts.id'), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('verification_reference', sa.String(255), nullable=False),
        sa.Column('verified_by_staff_id', sa.Integer, sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('artifact_id', name='uq_bblv_artifact'),
    )
    op.add_column('blood_donations', sa.Column('blood_group_reference', sa.String(255), nullable=True))
    op.add_column('blood_release_evidence', sa.Column('blood_group_reference', sa.String(255), nullable=True))

def downgrade():
    op.drop_column('blood_release_evidence', 'blood_group_reference')
    op.drop_column('blood_donations', 'blood_group_reference')
    op.drop_table('blood_lab_verifications')
    op.drop_table('blood_investigation_events')
    op.drop_table('blood_safety_inbox')
    op.drop_table('blood_patient_releases')
    op.drop_table('blood_lab_artifacts')
