# Core
from app.core.auth.user.models.user_model import User
from app.core.audit.models.audit_model import AuditLog
from app.core.idempotency.models.idempotency_model import IdempotencyRecord
from app.core.offline_operations.models.offline_operation_disposition_model import (
    OfflineOperationDisposition,
)


# Clinic
from app.modules.clinic.models.clinic_model import Clinic

# Patient
from app.modules.patient.models.patient_model import (
    Patient,
    PatientFamilyMember,
    PatientInsurance,
    PatientVitals,
)

# Staff
from app.modules.staff.models.staff_model import (
    Staff,
    PayrollRecord,
    LeaveRequest,
)

from app.modules.staff.models.excuse_model import Excuse

from app.modules.staff.models.staff_department_model import (
    StaffDepartment,
)

# Appointment
from app.modules.appointment.models.appointment_model import Appointment

# Consultation
from app.modules.consultation.models.consultation_model import (
    Consultation,
    ConsultationTemplate,
)

# Prescription
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
    DrugInteraction,
)

# Lab
from app.modules.lab.models.lab_model import (
    LabTest,
    LabOrder,
    LabOrderItem,
)

# Pharmacy
from app.modules.pharmacy.models.pharmacy_model import (
    Drug,
    DrugBatch,
    DispenseRecord,
    DispenseItem,
)

# Billing
from app.modules.billing.models.billing_model import (
    Invoice,
    InvoiceItem,
    Payment,
    PaymentWebhookEvent,
)

# Ward
from app.modules.ward.models.ward_model import (
    Ward,
    Bed,
    Admission,
    WardTransfer,
)

# eMAR
from app.modules.emar.models.emar_model import (
    MedicationAdministration,
    MedicationAdministrationEvent,
)


# Nursing
from app.modules.nursing.models.nursing_model import (
    NursingCarePlan,
    NursingCareTask,
    NursingNote,
    NursingHandoff,
)

# Clinical Orders
from app.modules.clinical_orders.models.imaging_order_model import (
    ImagingOrder,
)
from app.modules.clinical_orders.models.procedure_order_model import (
    ProcedureOrder,
)
from app.modules.clinical_orders.models.blood_product_order_model import (
    BloodProductOrder,
)
from app.modules.clinical_orders.models.diet_order_model import (
    DietOrder,
)

# Inventory
from app.modules.inventory.models.inventory_model import (
    InventoryItem,
    InventorySupplier,
    StockMovement,
)

# Reports
from app.modules.reports.models.reports_model import GeneratedReport

# AI
from app.modules.ai.models.ai_model import AILog

# Ambulance
from app.modules.ambulance.models.ambulance_model import (
    AmbulanceVehicle,
    AmbulanceTrip,
)

# HIE
from app.modules.hie.models.hie_model import (
    HIEIntegration,
    HIESubmission,
)

# Notifications
from app.core.notifications.models.notification_models import Notification

# Clinic Settings
from app.modules.settings.models.clinic_settings import ClinicSettings

# Integration
from app.modules.settings.models.integration_config import IntegrationConfig

# Google Auth
from app.core.auth.user.models.user_auth_identity_model import UserAuthIdentity

# User Device
from app.core.auth.user.models.user_device_model import UserDevice

# Asset Control
from app.modules.asset_control.models.asset_model import Asset
from app.modules.asset_control.models.asset_history_model import AssetHistory
from app.modules.asset_control.models.asset_assignment_model import AssetAssignment
from app.modules.asset_control.models.asset_maintenance_model import AssetMaintenance

# Chat
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.conversation_participant_model import (
    ConversationParticipant,
)

# Emergency Access
from app.core.emergency_access.models.emergency_access_model import (
    EmergencyAccessGrant,
)
from app.core.emergency_access.models.consent_guard_model import (
    ConsentGuardEvaluation,
)

# Clinical Safety
from app.core.clinical_safety.models.clinical_rule_model import (
    ClinicalRule,
)
from app.core.clinical_safety.models.clinical_alert_model import (
    ClinicalAlert,
)
from app.core.clinical_safety.models.alert_acknowledgement_model import (
    AlertAcknowledgement,
)

from app.modules.chat.models.message_model import Message
from app.modules.chat.models.message_attachment_model import MessageAttachment
from app.modules.chat.models.message_mention_model import MessageMention
from app.modules.chat.models.message_pin_model import MessagePin
from app.modules.chat.models.message_read_receipt_model import MessageReadReceipt
from app.modules.chat.models.message_reaction_model import MessageReaction
from app.modules.chat.models.message_revision_model import MessageRevision
from app.modules.chat.models.chat_outbox_model import ChatOutbox
from app.modules.chat.models.chat_usage_model import ChatUsage

# Feedback
from app.modules.feedback.models.feedback_model import Feedback
from app.modules.feedback.models.feedback_comment_model import FeedbackComment
from app.modules.feedback.models.feedback_reaction_model import FeedbackReaction

# Department
from app.modules.department.models.department_model import Department

__all__ = [
    # Core
    "User",
    "AuditLog",
    "IdempotencyRecord",
    "OfflineOperationDisposition",


    # Clinic
    "Clinic",

    # Patient
    "Patient",
    "PatientFamilyMember",
    "PatientInsurance",
    "PatientVitals",

    # Staff
    "Staff",
    "StaffDepartment",
    "PayrollRecord",
    "LeaveRequest",
    "Excuse",

    # Appointment
    "Appointment",

    # Consultation
    "Consultation",
    "ConsultationTemplate",

    # Prescription
    "Prescription",
    "PrescriptionItem",
    "DrugInteraction",

    # Lab
    "LabTest",
    "LabOrder",
    "LabOrderItem",

    # Pharmacy
    "Drug",
    "DrugBatch",
    "DispenseRecord",
    "DispenseItem",

    # Billing
    "Invoice",
    "InvoiceItem",
    "Payment",
    "PaymentWebhookEvent",

    # Ward
    "Ward",
    "Bed",
    "Admission",
    "WardTransfer",

    # eMAR
    "MedicationAdministration",
    "MedicationAdministrationEvent",


    # Nursing
    "NursingCarePlan",
    "NursingCareTask",
    "NursingNote",
    "NursingHandoff",

    # Clinical Orders
    "ImagingOrder",
    "ProcedureOrder",
    "BloodProductOrder",
    "DietOrder",

    # Inventory
    "InventoryItem",
    "InventorySupplier",
    "StockMovement",

    # Reports
    "GeneratedReport",

    # AI
    "AILog",

    # Ambulance
    "AmbulanceVehicle",
    "AmbulanceTrip",

    # HIE
    "HIEIntegration",
    "HIESubmission",

    # Notifications
    "Notification",

    # Settings
    "ClinicSettings",
    "IntegrationConfig",

    # Authentication
    "UserAuthIdentity",

    # User Devices
    "UserDevice",

    # Asset Control
    "Asset",
    "AssetHistory",
    "AssetAssignment",
    "AssetMaintenance",
    
    # Emergency Access
    "EmergencyAccessGrant",
    "ConsentGuardEvaluation",
    
    # Clinical Safety
    "ClinicalRule",
    "ClinicalAlert",
    "AlertAcknowledgement",

    # Chat
    "Conversation",
    "ConversationParticipant",
    "Message",
    "MessageAttachment",
    "MessageMention",
    "MessagePin",
    "MessageReadReceipt",
    "MessageReaction",
    "MessageRevision",
    "ChatOutbox",
    "ChatUsage",
    
    # Feedback
    "Feedback",
    "FeedbackComment",
    "FeedbackReaction",
    
    # Department
    "Department",
]
