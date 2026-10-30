from enum import Enum


class MedicationAdministrationStatus(str, Enum):
    SCHEDULED = "scheduled"
    DUE = "due"
    ADMINISTERED = "administered"
    HELD = "held"
    REFUSED = "refused"
    MISSED = "missed"
    CANCELLED = "cancelled"
