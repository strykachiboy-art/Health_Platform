from __future__ import annotations

from enum import Enum


class MedicationReconciliationEvent(str, Enum):
    ADMISSION = "admission"
    TRANSFER = "transfer"
    DISCHARGE = "discharge"


class MedicationReconciliationDecision(str, Enum):
    CONTINUE = "continue"
    HOLD = "hold"
    STOP = "stop"
    REPLACE = "replace"
    NEW = "new"
    VERIFY = "verify"
    UNKNOWN = "unknown"
