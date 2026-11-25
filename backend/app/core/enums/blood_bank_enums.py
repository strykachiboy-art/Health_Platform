from enum import Enum


class BloodUnitSourceType(str, Enum):
    IN_HOUSE_DONATION = "in_house_donation"
    EXTERNAL_BLOOD_BANK = "external_blood_bank"


class BloodUnitStatus(str, Enum):
    QUARANTINED = "quarantined"
    AVAILABLE = "available"
    RESERVED = "reserved"
    ISSUED = "issued"
    ADMINISTRATION_STARTED = "administration_started"
    ADMINISTRATION_STOPPED = "administration_stopped"
    RETURNED_QUARANTINE = "returned_quarantine"
    RECALLED = "recalled"
    TRANSFUSED = "transfused"
    DISCARDED = "discarded"
    EXPIRED = "expired"


class TtiScreeningStatus(str, Enum):
    PENDING = "pending"
    CLEARED = "cleared"
    REACTIVE = "reactive"
    INDETERMINATE = "indeterminate"


class DonorDeferralStatus(str, Enum):
    ELIGIBLE = "eligible"
    TEMPORARY_DEFERRAL = "temporary_deferral"
    PERMANENT_DEFERRAL = "permanent_deferral"
    REVIEW_REQUIRED = "review_required"


class DonationStatus(str, Enum):
    COLLECTED = "collected"
    SCREENING_PENDING = "screening_pending"
    CLEARED = "cleared"
    REACTIVELY_DISCARDED = "reactively_discarded"


class TransfusionStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    STOPPED_REACTION = "stopped_reaction"
    STOPPED_OTHER = "stopped_other"


class TransfusionReactionType(str, Enum):
    NONE = "none"
    FEBRILE_NON_HEMOLYTIC = "febrile_non_hemolytic"
    ALLERGIC = "allergic"
    ACUTE_HEMOLYTIC = "acute_hemolytic"
    DELAYED_HEMOLYTIC = "delayed_hemolytic"
    TRALI = "trali"
    TACO = "taco"
    SEPSIS_CONTAMINATION = "sepsis_contamination"
    OTHER = "other"


class TransfusionReactionSeverity(str, Enum):
    NONE = "none"
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    LIFE_THREATENING = "life_threatening"


class BloodBarcodeFailureCode(str, Enum):
    UNKNOWN_PATIENT_BARCODE = "unknown_patient_barcode"
    UNKNOWN_UNIT_BARCODE = "unknown_unit_barcode"
    MALFORMED_PATIENT_BARCODE = "malformed_patient_barcode"
    MALFORMED_UNIT_BARCODE = "malformed_unit_barcode"
    CROSS_CLINIC_PATIENT_BARCODE = "cross_clinic_patient_barcode"
    CROSS_CLINIC_UNIT_BARCODE = "cross_clinic_unit_barcode"
    WRONG_PATIENT = "wrong_patient"
    UNIT_NOT_ISSUED_TO_ORDER = "unit_not_issued_to_order"
    UNIT_EXPIRED = "unit_expired"
    UNIT_NOT_CLEARED = "unit_not_cleared"
    INCOMPATIBLE_BLOOD_TYPE = "incompatible_blood_type"
    INACTIVE_ADMISSION = "inactive_admission"
    INACTIVE_ORDER = "inactive_order"
    ORDER_NOT_VERIFIED = "order_not_verified"
    UNAUTHORIZED_ACTOR = "unauthorized_actor"
    STALE_VERIFICATION = "stale_verification"
