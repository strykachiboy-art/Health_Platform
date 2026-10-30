from enum import Enum


class NursingCarePlanStatus(str, Enum):
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class NursingCareTaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class NursingCareTaskPriority(str, Enum):
    ROUTINE = "routine"
    HIGH = "high"
    URGENT = "urgent"


class NursingNoteType(str, Enum):
    ASSESSMENT = "assessment"
    PROGRESS = "progress"
    INTERVENTION = "intervention"
    EDUCATION = "education"
    SAFETY = "safety"
    OTHER = "other"


class NursingNoteStatus(str, Enum):
    DRAFT = "draft"
    SIGNED = "signed"


class NursingHandoffStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    ACKNOWLEDGED = "acknowledged"
    CANCELLED = "cancelled"
