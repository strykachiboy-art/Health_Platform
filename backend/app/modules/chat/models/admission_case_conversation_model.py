"""Canonical admission-to-existing-chat-conversation association.

CT2B persistence only: no conversation creation or membership mutation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AdmissionCaseConversation(db.Model):
    __tablename__ = "admission_case_conversations"

    __table_args__ = (
        db.UniqueConstraint(
            "admission_id",
            name="uq_admission_case_conversations_admission",
        ),
        db.UniqueConstraint(
            "conversation_id",
            name="uq_admission_case_conversations_conversation",
        ),
        db.Index(
            "ix_admission_case_conversations_clinic_patient",
            "clinic_id",
            "patient_id",
        ),
        db.Index(
            "ix_admission_case_conversations_clinic_conversation",
            "clinic_id",
            "conversation_id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(
        db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True,
    )
    patient_id = db.Column(
        db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True,
    )
    admission_id = db.Column(
        db.Integer, db.ForeignKey("admissions.id"), nullable=False, index=True,
    )
    conversation_id = db.Column(
        db.Integer, db.ForeignKey("chat_conversations.id"),
        nullable=False, index=True,
    )
    linked_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False,
    )
    linked_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow,
    )

    def __repr__(self) -> str:
        return (
            f"<AdmissionCaseConversation id={self.id} "
            f"admission={self.admission_id} "
            f"conversation={self.conversation_id}>"
        )
