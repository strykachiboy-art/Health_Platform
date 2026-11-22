"""Coalesced durable delivery marker for admission case-chat synchronization.

An assignment is clinical authority; its write must not be rolled back merely
because case-chat policy or delivery fails after commit. One row per admission
records a monotonic synchronization generation for safe at-least-once retry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AdmissionCaseChatSyncRequest(db.Model):
    __tablename__ = "admission_case_chat_sync_requests"
    __table_args__ = (
        db.UniqueConstraint(
            "clinic_id", "admission_id", name="uq_admission_case_chat_sync_clinic_admission"
        ),
        db.CheckConstraint(
            "request_sequence >= 1 AND processed_sequence >= 0 "
            "AND request_sequence >= processed_sequence",
            name="ck_admission_case_chat_sync_sequence",
        ),
        db.Index(
            "ix_admission_case_chat_sync_pending",
            "clinic_id", "processed_sequence", "request_sequence",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=False, index=True)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    authorization_handoff_id = db.Column(
        db.Integer, db.ForeignKey("nursing_handoffs.id"), nullable=True, index=True,
    )
    request_sequence = db.Column(db.Integer, nullable=False, default=1)
    processed_sequence = db.Column(db.Integer, nullable=False, default=0)
    requested_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    processed_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<AdmissionCaseChatSyncRequest id={self.id}>"
