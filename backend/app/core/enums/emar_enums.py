from enum import Enum


class MedicationAdministrationStatus(str, Enum):
    SCHEDULED = "scheduled"
    DUE = "due"
    ADMINISTERED = "administered"
    HELD = "held"
    REFUSED = "refused"
    MISSED = "missed"
    CANCELLED = "cancelled"


class BarcodeVerificationResult(str, Enum):
    BARCODE_VERIFIED = "barcode_verified"
    NOT_BARCODE_VERIFIED = "not_barcode_verified"


class BarcodeVerificationFailureCode(str, Enum):
    UNKNOWN_PATIENT_BARCODE = "unknown_patient_barcode"
    UNKNOWN_MEDICATION_BARCODE = "unknown_medication_barcode"
    MALFORMED_PATIENT_BARCODE = "malformed_patient_barcode"
    MALFORMED_MEDICATION_BARCODE = "malformed_medication_barcode"
    CROSS_CLINIC_PATIENT_BARCODE = "cross_clinic_patient_barcode"
    CROSS_CLINIC_MEDICATION_BARCODE = "cross_clinic_medication_barcode"
    WRONG_PATIENT = "wrong_patient"
    WRONG_MEDICATION = "wrong_medication"
    DOSE_MISMATCH = "dose_mismatch"
    ROUTE_MISMATCH = "route_mismatch"
    OUTSIDE_TIME_WINDOW = "outside_time_window"
    INACTIVE_ADMISSION = "inactive_admission"
    INACTIVE_ORDER = "inactive_order"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_EXPIRED = "order_expired"
    UNAUTHORIZED_ACTOR = "unauthorized_actor"
    STALE_VERIFICATION = "stale_verification"
    NO_SCHEDULED_ADMINISTRATION = "no_scheduled_administration"
    AMBIGUOUS_SCHEDULED_ADMINISTRATION = "ambiguous_scheduled_administration"
