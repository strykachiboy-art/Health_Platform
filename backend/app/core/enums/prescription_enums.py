from enum import Enum


class PrescriptionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DrugInteractionSeverity(str, Enum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


class MedicationOrderPriority(str, Enum):
    ROUTINE = "routine"
    URGENT = "urgent"
    STAT = "stat"


class MedicationOrderType(str, Enum):
    INPATIENT = "inpatient"
    OUTPATIENT = "outpatient"
    DISCHARGE = "discharge"


class MedicationIntervalUnit(str, Enum):
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
