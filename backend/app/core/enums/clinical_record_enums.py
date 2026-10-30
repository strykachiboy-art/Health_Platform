from __future__ import annotations

from enum import Enum


class ClinicalRecordStatus(str, Enum):
    DRAFT = "draft"
    FINAL = "final"
    AMENDED = "amended"


class ClinicalRecordType(str, Enum):
    CONSULTATION_NOTE = "consultation_note"
    NURSING_NOTE = "nursing_note"
    MEDICATION_ADMINISTRATION = "medication_administration"
    MEDICATION_RECONCILIATION = "medication_reconciliation"
    DISCHARGE_SUMMARY = "discharge_summary"
