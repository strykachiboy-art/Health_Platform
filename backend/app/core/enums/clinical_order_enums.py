
from enum import Enum


class ClinicalOrderType(str, Enum):
    MEDICATION = "medication"
    LABORATORY = "laboratory"
    IMAGING = "imaging"
    PROCEDURE = "procedure"
    BLOOD_PRODUCT = "blood_product"
    DIET = "diet"
    NURSING = "nursing"


class ClinicalOrderPriority(str, Enum):
    ROUTINE = "routine"
    URGENT = "urgent"
    STAT = "stat"


class ClinicalOrderLifecycleStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    IN_PROGRESS = "in_progress"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
