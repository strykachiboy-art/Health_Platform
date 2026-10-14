from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app.core.enums.appointment_enums import AppointmentStatus
from app.core.enums.consultation_enums import (
    ConsultationStatus,
    ConsultationType,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)

import app.modules.consultation.services.consultation_service as consultation_service


@pytest.fixture(autouse=True)
def consultation_app_context(app):
    yield


@pytest.fixture()
def no_audit(monkeypatch):
    audit = Mock()

    monkeypatch.setattr(
        consultation_service,
        "create_audit_log",
        audit,
    )

    return audit


class TestGetConsultation:
    def test_returns_consultation_by_id(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
        )

        result = consultation_service.get_consultation(
            consultation.id,
        )

        assert result.id == consultation.id

    @pytest.mark.parametrize(
        "consultation_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_id(
        self,
        consultation_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Consultation ID must be greater than 0",
        ):
            consultation_service.get_consultation(
                consultation_id,
            )

    def test_missing_consultation_is_rejected(self):
        with pytest.raises(
            NotFoundError,
            match="Consultation 999999 not found",
        ):
            consultation_service.get_consultation(
                999999,
            )

    def test_clinic_scope_is_enforced(
        self,
        clinic,
        make_clinic,
        make_patient,
        make_staff,
        make_consultation,
    ):
        other_clinic = make_clinic(
            name="Other Consultation Clinic",
        )

        other_patient = make_patient(
            other_clinic,
        )

        other_staff = make_staff(
            other_clinic,
        )

        consultation = make_consultation(
            other_clinic,
            patient=other_patient,
            staff=other_staff,
        )

        with pytest.raises(
            NotFoundError,
            match=f"Consultation {consultation.id} not found",
        ):
            consultation_service.get_consultation(
                consultation.id,
                clinic_id=clinic.id,
            )

    @pytest.mark.parametrize(
        "clinic_id",
        [0, -1, -50],
    )
    def test_rejects_non_positive_clinic_id(
        self,
        clinic_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Clinic ID must be greater than 0",
        ):
            consultation_service.get_consultation(
                consultation_id=1,
                clinic_id=clinic_id,
            )

    def test_historical_consultation_is_readable_from_inactive_clinic(
        self,
        suspended_clinic,
        make_patient,
        make_staff,
        make_consultation,
    ):
        patient = make_patient(suspended_clinic)
        staff = make_staff(suspended_clinic)

        consultation = make_consultation(
            suspended_clinic,
            patient,
            staff,
        )

        result = consultation_service.get_consultation(
            consultation.id,
            clinic_id=suspended_clinic.id,
        )

        assert result.id == consultation.id


class TestGetConsultationTemplate:
    def test_returns_template(
        self,
        make_template,
    ):
        template = make_template()

        result = consultation_service.get_consultation_template(
            template.id,
        )

        assert result.id == template.id

    @pytest.mark.parametrize(
        "template_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_id(
        self,
        template_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Consultation template ID must be greater than 0",
        ):
            consultation_service.get_consultation_template(
                template_id,
            )

    def test_missing_template_is_rejected(self):
        with pytest.raises(
            NotFoundError,
            match="Consultation template 999999 not found",
        ):
            consultation_service.get_consultation_template(
                999999,
            )


class TestValidateConsultationParticipants:
    def test_returns_valid_participants(
        self,
        clinic,
        patient,
        staff,
    ):
        result = consultation_service._validate_consultation_participants(
            clinic_id=clinic.id,
            patient_id=patient.id,
            staff_id=staff.id,
        )

        assert result[0].id == clinic.id
        assert result[1].id == patient.id
        assert result[2].id == staff.id

    @pytest.mark.parametrize(
        "clinic_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_clinic_id(
        self,
        patient,
        staff,
        clinic_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Clinic ID must be greater than 0",
        ):
            consultation_service._validate_consultation_participants(
                clinic_id=clinic_id,
                patient_id=patient.id,
                staff_id=staff.id,
            )

    @pytest.mark.parametrize(
        "patient_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_patient_id(
        self,
        clinic,
        staff,
        patient_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Patient ID must be greater than 0",
        ):
            consultation_service._validate_consultation_participants(
                clinic_id=clinic.id,
                patient_id=patient_id,
                staff_id=staff.id,
            )

    @pytest.mark.parametrize(
        "staff_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_staff_id(
        self,
        clinic,
        patient,
        staff_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Staff ID must be greater than 0",
        ):
            consultation_service._validate_consultation_participants(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff_id,
            )

    def test_patient_from_other_clinic_is_rejected(
        self,
        clinic,
        make_clinic,
        make_patient,
        staff,
    ):
        other_clinic = make_clinic(
            name="Other Patient Clinic",
        )

        other_patient = make_patient(
            other_clinic,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Patient {other_patient.id} does not belong "
                f"to clinic {clinic.id}"
            ),
        ):
            consultation_service._validate_consultation_participants(
                clinic_id=clinic.id,
                patient_id=other_patient.id,
                staff_id=staff.id,
            )

    def test_staff_from_other_clinic_is_rejected(
        self,
        clinic,
        make_clinic,
        make_staff,
        patient,
    ):
        other_clinic = make_clinic(
            name="Other Staff Clinic",
        )

        other_staff = make_staff(
            other_clinic,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Staff {other_staff.id} does not belong "
                f"to clinic {clinic.id}"
            ),
        ):
            consultation_service._validate_consultation_participants(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=other_staff.id,
            )


class TestValidateTemplate:
    def test_none_template_is_allowed(
        self,
        clinic,
    ):
        assert (
            consultation_service._validate_template(
                clinic_id=clinic.id,
                template_id=None,
            )
            is None
        )

    def test_active_global_template_is_allowed(
        self,
        clinic,
        make_template,
    ):
        template = make_template(
            clinic=None,
            is_active=True,
        )

        result = consultation_service._validate_template(
            clinic_id=clinic.id,
            template_id=template.id,
        )

        assert result.id == template.id

    def test_active_clinic_template_is_allowed(
        self,
        clinic,
        make_template,
    ):
        template = make_template(
            clinic=clinic,
            is_active=True,
        )

        result = consultation_service._validate_template(
            clinic_id=clinic.id,
            template_id=template.id,
        )

        assert result.id == template.id

    def test_inactive_template_is_rejected(
        self,
        clinic,
        make_template,
    ):
        template = make_template(
            clinic=clinic,
            is_active=False,
        )

        with pytest.raises(
            ValidationError,
            match=(
                f"Consultation template {template.id} "
                "is not active"
            ),
        ):
            consultation_service._validate_template(
                clinic_id=clinic.id,
                template_id=template.id,
            )

    def test_template_from_other_clinic_is_rejected(
        self,
        clinic,
        make_clinic,
        make_template,
    ):
        other_clinic = make_clinic(
            name="Other Template Clinic",
        )

        template = make_template(
            clinic=other_clinic,
            is_active=True,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Consultation template {template.id} does not belong "
                f"to clinic {clinic.id}"
            ),
        ):
            consultation_service._validate_template(
                clinic_id=clinic.id,
                template_id=template.id,
            )

    def test_missing_template_is_rejected(
        self,
        clinic,
    ):
        with pytest.raises(
            NotFoundError,
            match="Consultation template 999999 not found",
        ):
            consultation_service._validate_template(
                clinic_id=clinic.id,
                template_id=999999,
            )


class TestValidateAppointment:
    def test_none_appointment_is_allowed(
        self,
        clinic,
        patient,
        staff,
    ):
        assert (
            consultation_service._validate_appointment(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                appointment_id=None,
            )
            is None
        )

    @pytest.mark.parametrize(
        "appointment_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_appointment_id(
        self,
        clinic,
        patient,
        staff,
        appointment_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Appointment ID must be greater than 0",
        ):
            consultation_service._validate_appointment(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                appointment_id=appointment_id,
            )

    def test_valid_scheduled_appointment_is_allowed(
        self,
        clinic,
        patient,
        staff,
        make_appointment,
    ):
        appointment = make_appointment(
            clinic,
            patient,
            staff,
            status=AppointmentStatus.SCHEDULED,
        )

        result = consultation_service._validate_appointment(
            clinic_id=clinic.id,
            patient_id=patient.id,
            staff_id=staff.id,
            appointment_id=appointment.id,
        )

        assert result.id == appointment.id

    def test_valid_confirmed_appointment_is_allowed(
        self,
        clinic,
        patient,
        staff,
        make_appointment,
    ):
        appointment = make_appointment(
            clinic,
            patient,
            staff,
            status=AppointmentStatus.CONFIRMED,
        )

        result = consultation_service._validate_appointment(
            clinic_id=clinic.id,
            patient_id=patient.id,
            staff_id=staff.id,
            appointment_id=appointment.id,
        )

        assert result.id == appointment.id

    def test_missing_appointment_is_rejected(
        self,
        clinic,
        patient,
        staff,
    ):
        with pytest.raises(
            NotFoundError,
            match="Appointment 999999 not found",
        ):
            consultation_service._validate_appointment(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                appointment_id=999999,
            )

    def test_appointment_from_other_clinic_is_rejected(
        self,
        clinic,
        make_clinic,
        make_patient,
        make_staff,
        patient,
        staff,
        make_appointment,
    ):
        other_clinic = make_clinic(
            name="Other Appointment Clinic",
        )

        other_patient = make_patient(
            other_clinic,
        )

        other_staff = make_staff(
            other_clinic,
        )

        appointment = make_appointment(
            other_clinic,
            other_patient,
            other_staff,
            status=AppointmentStatus.SCHEDULED,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Appointment {appointment.id} does not belong "
                f"to clinic {clinic.id}"
            ),
        ):
            consultation_service._validate_appointment(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                appointment_id=appointment.id,
            )

    def test_appointment_for_other_patient_is_rejected(
        self,
        clinic,
        patient,
        staff,
        make_patient,
        make_appointment,
    ):
        other_patient = make_patient(
            clinic,
        )

        appointment = make_appointment(
            clinic,
            other_patient,
            staff,
            status=AppointmentStatus.SCHEDULED,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Appointment {appointment.id} does not belong "
                f"to patient {patient.id}"
            ),
        ):
            consultation_service._validate_appointment(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                appointment_id=appointment.id,
            )

    def test_appointment_for_other_staff_is_rejected(
        self,
        clinic,
        patient,
        staff,
        make_staff,
        make_appointment,
    ):
        other_staff = make_staff(
            clinic,
        )

        appointment = make_appointment(
            clinic,
            patient,
            other_staff,
            status=AppointmentStatus.SCHEDULED,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Appointment {appointment.id} does not belong "
                f"to staff member {staff.id}"
            ),
        ):
            consultation_service._validate_appointment(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                appointment_id=appointment.id,
            )

    def test_non_schedulable_appointment_status_is_rejected(
        self,
        clinic,
        patient,
        staff,
        make_appointment,
    ):
        appointment = make_appointment(
            clinic,
            patient,
            staff,
            status=AppointmentStatus.COMPLETED,
        )

        with pytest.raises(
            ConflictError,
            match="cannot start a consultation",
        ):
            consultation_service._validate_appointment(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                appointment_id=appointment.id,
            )


class TestStartConsultation:
    def test_starts_consultation(
        self,
        clinic,
        patient,
        staff,
        no_audit,
    ):
        result = consultation_service.start_consultation(
            clinic_id=clinic.id,
            patient_id=patient.id,
            staff_id=staff.id,
        )

        assert result.id is not None
        assert result.clinic_id == clinic.id
        assert result.patient_id == patient.id
        assert result.staff_id == staff.id
        assert result.status == ConsultationStatus.IN_PROGRESS
        assert result.consultation_type == ConsultationType.GENERAL
        assert result.started_at is not None
        no_audit.assert_called_once()

    def test_inactive_patient_cannot_start_consultation(
        self,
        clinic,
        patient,
        staff,
    ):
        patient.is_active = False

        with pytest.raises(ValidationError, match="inactive"):
            consultation_service.start_consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
            )


    def test_starts_with_requested_consultation_type(
        self,
        clinic,
        patient,
        staff,
        no_audit,
    ):
        result = consultation_service.start_consultation(
            clinic_id=clinic.id,
            patient_id=patient.id,
            staff_id=staff.id,
            consultation_type=ConsultationType.SPECIALIST,
        )

        assert result.consultation_type == ConsultationType.SPECIALIST

    def test_starts_with_global_template(
        self,
        clinic,
        patient,
        staff,
        make_template,
        no_audit,
    ):
        template = make_template(
            clinic=None,
            is_active=True,
        )

        result = consultation_service.start_consultation(
            clinic_id=clinic.id,
            patient_id=patient.id,
            staff_id=staff.id,
            template_id=template.id,
        )

        assert result.template_id == template.id

    def test_starts_with_clinic_template(
        self,
        clinic,
        patient,
        staff,
        make_template,
        no_audit,
    ):
        template = make_template(
            clinic=clinic,
            is_active=True,
        )

        result = consultation_service.start_consultation(
            clinic_id=clinic.id,
            patient_id=patient.id,
            staff_id=staff.id,
            template_id=template.id,
        )

        assert result.template_id == template.id

    def test_starts_with_valid_appointment(
        self,
        clinic,
        patient,
        staff,
        make_appointment,
        no_audit,
    ):
        appointment = make_appointment(
            clinic,
            patient,
            staff,
            status=AppointmentStatus.SCHEDULED,
        )

        result = consultation_service.start_consultation(
            clinic_id=clinic.id,
            patient_id=patient.id,
            staff_id=staff.id,
            appointment_id=appointment.id,
        )

        assert result.appointment_id == appointment.id

    def test_cross_clinic_patient_is_rejected(
        self,
        clinic,
        make_clinic,
        make_patient,
        staff,
        no_audit,
    ):
        other_clinic = make_clinic(
            name="Other Start Patient Clinic",
        )

        other_patient = make_patient(
            other_clinic,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Patient {other_patient.id} does not belong "
                f"to clinic {clinic.id}"
            ),
        ):
            consultation_service.start_consultation(
                clinic_id=clinic.id,
                patient_id=other_patient.id,
                staff_id=staff.id,
            )

    def test_cross_clinic_staff_is_rejected(
        self,
        clinic,
        make_clinic,
        make_staff,
        patient,
        no_audit,
    ):
        other_clinic = make_clinic(
            name="Other Start Staff Clinic",
        )

        other_staff = make_staff(
            other_clinic,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Staff {other_staff.id} does not belong "
                f"to clinic {clinic.id}"
            ),
        ):
            consultation_service.start_consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=other_staff.id,
            )

    def test_inactive_template_is_rejected(
        self,
        clinic,
        patient,
        staff,
        make_template,
        no_audit,
    ):
        template = make_template(
            clinic=clinic,
            is_active=False,
        )

        with pytest.raises(
            ValidationError,
            match=(
                f"Consultation template {template.id} is not active"
            ),
        ):
            consultation_service.start_consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                template_id=template.id,
            )

    def test_wrong_clinic_template_is_rejected(
        self,
        clinic,
        make_clinic,
        make_template,
        patient,
        staff,
        no_audit,
    ):
        other_clinic = make_clinic(
            name="Other Start Template Clinic",
        )

        template = make_template(
            clinic=other_clinic,
            is_active=True,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Consultation template {template.id} does not belong "
                f"to clinic {clinic.id}"
            ),
        ):
            consultation_service.start_consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                template_id=template.id,
            )

    def test_invalid_appointment_is_rejected(
        self,
        clinic,
        patient,
        staff,
        no_audit,
    ):
        with pytest.raises(
            NotFoundError,
            match="Appointment 999999 not found",
        ):
            consultation_service.start_consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                appointment_id=999999,
            )

    def test_appointment_for_wrong_patient_is_rejected(
        self,
        clinic,
        patient,
        staff,
        make_patient,
        make_appointment,
        no_audit,
    ):
        other_patient = make_patient(
            clinic,
        )

        appointment = make_appointment(
            clinic,
            other_patient,
            staff,
            status=AppointmentStatus.SCHEDULED,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Appointment {appointment.id} does not belong "
                f"to patient {patient.id}"
            ),
        ):
            consultation_service.start_consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                appointment_id=appointment.id,
            )


class TestUpdateConsultationNote:
    def test_updates_supported_fields_and_audits(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
        )

        result = consultation_service.update_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            icd10_code="J20.9",
            chief_complaint="  Cough  ",
            symptoms="  Fever  ",
            diagnosis="  Acute bronchitis  ",
            treatment_plan="  Rest  ",
            notes="  Follow up  ",
            voice_note_url="  https://example.com/audio  ",
            transcribed_text="  Patient reports cough  ",
        )

        assert result.icd10_code == "J20.9"
        assert result.chief_complaint == "Cough"
        assert result.symptoms == "Fever"
        assert result.diagnosis == "Acute bronchitis"
        assert result.treatment_plan == "Rest"
        assert result.notes == "Follow up"
        assert result.voice_note_url == "https://example.com/audio"
        assert result.transcribed_text == "Patient reports cough"

        no_audit.assert_called_once()

    def test_none_values_are_ignored(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            diagnosis="Existing diagnosis",
        )

        result = consultation_service.update_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            diagnosis=None,
        )

        assert result.diagnosis == "Existing diagnosis"
        no_audit.assert_not_called()

    def test_no_changes_do_not_create_audit(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            diagnosis="Existing diagnosis",
        )

        result = consultation_service.update_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            diagnosis="Existing diagnosis",
        )

        assert result.diagnosis == "Existing diagnosis"
        no_audit.assert_not_called()

    def test_unknown_field_is_rejected(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
        )

        with pytest.raises(
            ValidationError,
            match="Unknown consultation field",
        ):
            consultation_service.update_consultation_note(
                consultation_id=consultation.id,
                clinic_id=clinic.id,
                unsupported_field="value",
            )

        no_audit.assert_not_called()

    def test_cancelled_consultation_cannot_be_updated(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.CANCELLED,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Consultation {consultation.id} is cancelled "
                "and cannot be updated"
            ),
        ):
            consultation_service.update_consultation_note(
                consultation_id=consultation.id,
                clinic_id=clinic.id,
                diagnosis="New diagnosis",
            )

        no_audit.assert_not_called()

    def test_completed_consultation_can_be_amended(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.COMPLETED,
            diagnosis="Original diagnosis",
        )

        result = consultation_service.update_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            diagnosis="Amended diagnosis",
        )

        assert result.diagnosis == "Amended diagnosis"
        no_audit.assert_called_once()

    def test_wrong_clinic_is_rejected(
        self,
        clinic,
        make_clinic,
        make_patient,
        make_staff,
        make_consultation,
        no_audit,
    ):
        other_clinic = make_clinic(
            name="Other Update Clinic",
        )

        other_patient = make_patient(
            other_clinic,
        )

        other_staff = make_staff(
            other_clinic,
        )

        consultation = make_consultation(
            other_clinic,
            other_patient,
            other_staff,
        )

        with pytest.raises(
            NotFoundError,
            match=f"Consultation {consultation.id} not found",
        ):
            consultation_service.update_consultation_note(
                consultation_id=consultation.id,
                clinic_id=clinic.id,
                diagnosis="Unauthorized",
            )

        no_audit.assert_not_called()


class TestCompleteConsultation:
    def test_completes_in_progress_consultation(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.IN_PROGRESS,
        )

        before = datetime.now(timezone.utc).replace(
            tzinfo=None
        )

        result = consultation_service.complete_consultation(
            consultation.id,
            clinic.id,
            diagnosis="Acute bronchitis",
            treatment_plan="Hydration and rest",
            notes="Return if symptoms worsen",
        )

        assert result.status == ConsultationStatus.COMPLETED
        assert result.diagnosis == "Acute bronchitis"
        assert result.treatment_plan == "Hydration and rest"
        assert result.notes == "Return if symptoms worsen"
        assert result.ended_at is not None

        ended_at = result.ended_at

        if ended_at.tzinfo is not None:
            ended_at = ended_at.replace(tzinfo=None)

        assert ended_at >= before
        no_audit.assert_called_once()

    def test_strips_diagnosis(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
        )

        result = consultation_service.complete_consultation(
            consultation.id,
            clinic.id,
            diagnosis="  Acute bronchitis  ",
        )

        assert result.diagnosis == "Acute bronchitis"

    def test_diagnosis_is_required(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
        )

        with pytest.raises(
            ValidationError,
            match="Diagnosis is required to complete a consultation",
        ):
            consultation_service.complete_consultation(
                consultation.id,
                clinic.id,
                diagnosis="   ",
            )

        no_audit.assert_not_called()

    def test_already_completed_consultation_is_rejected(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.COMPLETED,
        )

        with pytest.raises(
            ConflictError,
            match=f"Consultation {consultation.id} is already completed",
        ):
            consultation_service.complete_consultation(
                consultation.id,
                clinic.id,
                diagnosis="Another diagnosis",
            )

        no_audit.assert_not_called()

    def test_cancelled_consultation_cannot_be_completed(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.CANCELLED,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Consultation {consultation.id} is cancelled "
                "and cannot be completed"
            ),
        ):
            consultation_service.complete_consultation(
                consultation.id,
                clinic.id,
                diagnosis="Another diagnosis",
            )

        no_audit.assert_not_called()

    def test_wrong_clinic_is_rejected(
        self,
        clinic,
        make_clinic,
        make_patient,
        make_staff,
        make_consultation,
        no_audit,
    ):
        other_clinic = make_clinic(
            name="Other Complete Clinic",
        )

        other_patient = make_patient(
            other_clinic,
        )

        other_staff = make_staff(
            other_clinic,
        )

        consultation = make_consultation(
            other_clinic,
            other_patient,
            other_staff,
        )

        with pytest.raises(
            NotFoundError,
            match=f"Consultation {consultation.id} not found",
        ):
            consultation_service.complete_consultation(
                consultation.id,
                clinic.id,
                diagnosis="Unauthorized",
            )

        no_audit.assert_not_called()


class TestCancelConsultation:
    def test_cancels_in_progress_consultation(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            notes="Existing note",
        )

        result = consultation_service.cancel_consultation(
            consultation.id,
            clinic.id,
            reason="Patient requested cancellation",
        )

        assert result.status == ConsultationStatus.CANCELLED
        assert result.ended_at is not None
        assert result.notes == (
            "Existing note\n"
            "[Cancelled: Patient requested cancellation]"
        )
        no_audit.assert_called_once()

    def test_cancels_without_reason(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            notes="Existing note",
        )

        result = consultation_service.cancel_consultation(
            consultation.id,
            clinic.id,
        )

        assert result.status == ConsultationStatus.CANCELLED
        assert result.notes == "Existing note"
        no_audit.assert_called_once()

    def test_rejects_blank_reason(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
        )

        result = consultation_service.cancel_consultation(
            consultation.id,
            clinic.id,
            reason="   ",
        )

        assert result.status == ConsultationStatus.CANCELLED
        assert result.ended_at is not None
        no_audit.assert_called_once()

    def test_repeated_cancel_is_rejected(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.CANCELLED,
        )

        with pytest.raises(
            ConflictError,
            match=f"Consultation {consultation.id} is already cancelled",
        ):
            consultation_service.cancel_consultation(
                consultation.id,
                clinic.id,
                reason="Again",
            )

        no_audit.assert_not_called()

    def test_completed_consultation_cannot_be_cancelled(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
        no_audit,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.COMPLETED,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Consultation {consultation.id} is already completed "
                "and cannot be cancelled"
            ),
        ):
            consultation_service.cancel_consultation(
                consultation.id,
                clinic.id,
                reason="Too late",
            )

        no_audit.assert_not_called()

    def test_wrong_clinic_is_rejected(
        self,
        clinic,
        make_clinic,
        make_patient,
        make_staff,
        make_consultation,
        no_audit,
    ):
        other_clinic = make_clinic(
            name="Other Cancel Clinic",
        )

        other_patient = make_patient(
            other_clinic,
        )

        other_staff = make_staff(
            other_clinic,
        )

        consultation = make_consultation(
            other_clinic,
            other_patient,
            other_staff,
        )

        with pytest.raises(
            NotFoundError,
            match=f"Consultation {consultation.id} not found",
        ):
            consultation_service.cancel_consultation(
                consultation.id,
                clinic.id,
                reason="Unauthorized",
            )

        no_audit.assert_not_called()


class TestGetConsultationsForPatient:
    def test_returns_patient_consultations_in_descending_order(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        older = make_consultation(
            clinic,
            patient,
            staff,
            started_at=(
                datetime.now(timezone.utc)
                - timedelta(days=2)
            ),
        )

        newer = make_consultation(
            clinic,
            patient,
            staff,
            started_at=(
                datetime.now(timezone.utc)
                - timedelta(days=1)
            ),
        )

        results, pagination = (
            consultation_service.get_consultations_for_patient(
                patient.id,
                clinic.id,
            )
        )

        ids = [item.id for item in results]

        assert ids.index(newer.id) < ids.index(older.id)

        assert pagination["page"] == 1
        assert pagination["per_page"] == 50
        assert pagination["total"] == 2
        assert pagination["pages"] == 1
        assert pagination["has_next"] is False
        assert pagination["has_prev"] is False
        assert pagination["next_page"] is None
        assert pagination["prev_page"] is None

    def test_excludes_other_patient(
        self,
        clinic,
        patient,
        staff,
        make_patient,
        make_consultation,
    ):
        other_patient = make_patient(
            clinic,
        )

        target = make_consultation(
            clinic,
            patient,
            staff,
        )

        other = make_consultation(
            clinic,
            other_patient,
            staff,
        )

        results, _ = (
            consultation_service.get_consultations_for_patient(
                patient.id,
                clinic.id,
            )
        )

        ids = [item.id for item in results]

        assert target.id in ids
        assert other.id not in ids

    def test_filters_by_consultation_type(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        general = make_consultation(
            clinic,
            patient,
            staff,
            consultation_type=ConsultationType.GENERAL,
        )

        specialist = make_consultation(
            clinic,
            patient,
            staff,
            consultation_type=ConsultationType.SPECIALIST,
        )

        results, pagination = (
            consultation_service.get_consultations_for_patient(
                patient.id,
                clinic.id,
                consultation_type=ConsultationType.SPECIALIST,
            )
        )

        ids = [item.id for item in results]

        assert specialist.id in ids
        assert general.id not in ids
        assert pagination["total"] == 1

    def test_filters_by_consultation_type_with_pagination(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        specialist = [
            make_consultation(
                clinic,
                patient,
                staff,
                consultation_type=ConsultationType.SPECIALIST,
                started_at=(
                    datetime.now(timezone.utc)
                    - timedelta(days=index)
                ),
            )
            for index in range(3)
        ]

        make_consultation(
            clinic,
            patient,
            staff,
            consultation_type=ConsultationType.GENERAL,
        )

        results, pagination = (
            consultation_service.get_consultations_for_patient(
                patient.id,
                clinic.id,
                consultation_type=ConsultationType.SPECIALIST,
                page=1,
                per_page=2,
            )
        )

        assert [item.id for item in results] == [
            specialist[0].id,
            specialist[1].id,
        ]

        assert pagination["page"] == 1
        assert pagination["per_page"] == 2
        assert pagination["total"] == 3
        assert pagination["pages"] == 2
        assert pagination["has_next"] is True
        assert pagination["has_prev"] is False
        assert pagination["next_page"] == 2
        assert pagination["prev_page"] is None

    def test_returns_empty_page_for_non_matching_consultation_type(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        make_consultation(
            clinic,
            patient,
            staff,
            consultation_type=ConsultationType.GENERAL,
        )

        results, pagination = (
            consultation_service.get_consultations_for_patient(
                patient.id,
                clinic.id,
                consultation_type=ConsultationType.EMERGENCY,
            )
        )

        assert results == []
        assert pagination["total"] == 0
        assert pagination["pages"] == 0
        assert pagination["has_next"] is False
        assert pagination["has_prev"] is False
        assert pagination["next_page"] is None
        assert pagination["prev_page"] is None

    def test_wrong_clinic_patient_is_hidden(
        self,
        clinic,
        make_clinic,
        make_patient,
    ):
        other_clinic = make_clinic(
            name="Other Patient History Clinic",
        )

        other_patient = make_patient(
            other_clinic,
        )

        with pytest.raises(
            NotFoundError,
            match=f"Patient {other_patient.id} not found",
        ):
            consultation_service.get_consultations_for_patient(
                other_patient.id,
                clinic.id,
            )

    @pytest.mark.parametrize(
        "patient_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_patient_id(
        self,
        clinic,
        patient_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Patient ID must be greater than 0",
        ):
            consultation_service.get_consultations_for_patient(
                patient_id,
                clinic.id,
            )

    @pytest.mark.parametrize(
        "clinic_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_clinic_id(
        self,
        patient,
        clinic_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Clinic ID must be greater than 0",
        ):
            consultation_service.get_consultations_for_patient(
                patient.id,
                clinic_id,
            )

    @pytest.mark.parametrize(
        ("page", "per_page"),
        [
            (0, 50),
            (-1, 50),
            (1, 0),
            (1, -1),
            (1, 501),
        ],
    )
    def test_rejects_invalid_pagination(
        self,
        clinic,
        patient,
        page,
        per_page,
    ):
        with pytest.raises(
            ValidationError,
        ):
            consultation_service.get_consultations_for_patient(
                patient.id,
                clinic.id,
                page=page,
                per_page=per_page,
            )

    def test_paginates_results(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        consultations = [
            make_consultation(
                clinic,
                patient,
                staff,
                started_at=(
                    datetime.now(timezone.utc)
                    - timedelta(days=index)
                ),
            )
            for index in range(3)
        ]

        results, pagination = (
            consultation_service.get_consultations_for_patient(
                patient.id,
                clinic.id,
                page=1,
                per_page=2,
            )
        )

        assert len(results) == 2
        assert pagination["page"] == 1
        assert pagination["per_page"] == 2
        assert pagination["total"] == 3
        assert pagination["pages"] == 2
        assert pagination["has_next"] is True
        assert pagination["has_prev"] is False
        assert pagination["next_page"] == 2
        assert pagination["prev_page"] is None

        assert results[0].id == consultations[0].id
        assert results[1].id == consultations[1].id

    def test_returns_second_page(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        consultations = [
            make_consultation(
                clinic,
                patient,
                staff,
                started_at=(
                    datetime.now(timezone.utc)
                    - timedelta(days=index)
                ),
            )
            for index in range(3)
        ]

        results, pagination = (
            consultation_service.get_consultations_for_patient(
                patient.id,
                clinic.id,
                page=2,
                per_page=2,
            )
        )

        assert [item.id for item in results] == [
            consultations[2].id,
        ]

        assert pagination["page"] == 2
        assert pagination["per_page"] == 2
        assert pagination["total"] == 3
        assert pagination["pages"] == 2
        assert pagination["has_next"] is False
        assert pagination["has_prev"] is True
        assert pagination["next_page"] is None
        assert pagination["prev_page"] == 1

    def test_empty_last_page_is_returned_without_error(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        make_consultation(
            clinic,
            patient,
            staff,
        )

        results, pagination = (
            consultation_service.get_consultations_for_patient(
                patient.id,
                clinic.id,
                page=2,
                per_page=1,
            )
        )

        assert results == []
        assert pagination["page"] == 2
        assert pagination["per_page"] == 1
        assert pagination["total"] == 1
        assert pagination["pages"] == 1
        assert pagination["has_next"] is False
        assert pagination["has_prev"] is True
        assert pagination["next_page"] is None
        assert pagination["prev_page"] == 1

    def test_historical_consultations_remain_readable_for_inactive_clinic(
        self,
        suspended_clinic,
        make_patient,
        make_staff,
        make_consultation,
    ):
        patient = make_patient(suspended_clinic)
        staff = make_staff(suspended_clinic)

        consultation = make_consultation(
            suspended_clinic,
            patient,
            staff,
        )

        results, pagination = (
            consultation_service.get_consultations_for_patient(
                patient.id,
                suspended_clinic.id,
            )
        )

        assert consultation.id in [
            item.id
            for item in results
        ]
        assert pagination["total"] == 1


class TestGetPatientsSeenByStaff:
    def test_returns_distinct_patients(
        self,
        clinic,
        make_patient,
        make_staff,
        make_consultation,
    ):
        staff = make_staff(
            clinic=clinic,
        )

        patient_one = make_patient(
            clinic=clinic,
        )

        patient_two = make_patient(
            clinic=clinic,
        )

        make_consultation(
            clinic=clinic,
            patient=patient_one,
            staff=staff,
        )

        make_consultation(
            clinic=clinic,
            patient=patient_one,
            staff=staff,
        )

        make_consultation(
            clinic=clinic,
            patient=patient_two,
            staff=staff,
        )

        patients, pagination = (
            consultation_service.get_patients_seen_by_staff(
                staff_id=staff.id,
                clinic_id=clinic.id,
            )
        )

        assert {
            patient.id
            for patient in patients
        } == {
            patient_one.id,
            patient_two.id,
        }

        assert len(patients) == 2
        assert pagination["page"] == 1
        assert pagination["per_page"] == 50
        assert pagination["total"] == 2
        assert pagination["pages"] == 1
        assert pagination["has_next"] is False
        assert pagination["has_prev"] is False
        assert pagination["next_page"] is None
        assert pagination["prev_page"] is None

    def test_excludes_patients_seen_only_by_other_staff(
        self,
        clinic,
        make_patient,
        make_staff,
        make_consultation,
    ):
        staff_one = make_staff(
            clinic=clinic,
        )

        staff_two = make_staff(
            clinic=clinic,
        )

        patient_one = make_patient(
            clinic=clinic,
        )

        patient_two = make_patient(
            clinic=clinic,
        )

        make_consultation(
            clinic=clinic,
            patient=patient_one,
            staff=staff_one,
        )

        make_consultation(
            clinic=clinic,
            patient=patient_two,
            staff=staff_two,
        )

        patients, pagination = (
            consultation_service.get_patients_seen_by_staff(
                staff_id=staff_one.id,
                clinic_id=clinic.id,
            )
        )

        assert [
            patient.id
            for patient in patients
        ] == [
            patient_one.id,
        ]

        assert pagination["total"] == 1

    def test_rejects_staff_from_other_clinic(
        self,
        clinic,
        make_clinic,
        make_staff,
    ):
        other_clinic = make_clinic(
            name="Other Staff Seen Clinic",
        )

        staff = make_staff(
            clinic=other_clinic,
        )

        with pytest.raises(
            NotFoundError,
            match=f"Staff member {staff.id} not found",
        ):
            consultation_service.get_patients_seen_by_staff(
                staff_id=staff.id,
                clinic_id=clinic.id,
            )

    @pytest.mark.parametrize(
        "staff_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_staff_id(
        self,
        clinic,
        staff_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Staff ID must be greater than 0",
        ):
            consultation_service.get_patients_seen_by_staff(
                staff_id=staff_id,
                clinic_id=clinic.id,
            )

    @pytest.mark.parametrize(
        "clinic_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_clinic_id(
        self,
        staff,
        clinic_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Clinic ID must be greater than 0",
        ):
            consultation_service.get_patients_seen_by_staff(
                staff_id=staff.id,
                clinic_id=clinic_id,
            )

    @pytest.mark.parametrize(
        ("page", "per_page"),
        [
            (0, 50),
            (-1, 50),
            (1, 0),
            (1, -1),
            (1, 501),
        ],
    )
    def test_rejects_invalid_pagination(
        self,
        clinic,
        staff,
        page,
        per_page,
    ):
        with pytest.raises(
            ValidationError,
        ):
            consultation_service.get_patients_seen_by_staff(
                staff_id=staff.id,
                clinic_id=clinic.id,
                page=page,
                per_page=per_page,
            )

    def test_excludes_cross_clinic_consultations(
        self,
        clinic,
        make_clinic,
        make_patient,
        make_staff,
        make_consultation,
    ):
        other_clinic = make_clinic(
            name="Cross Clinic Seen Clinic",
        )

        clinic_staff = make_staff(
            clinic=clinic,
        )

        clinic_patient = make_patient(
            clinic,
        )

        other_staff = make_staff(
            other_clinic,
        )

        other_patient = make_patient(
            other_clinic,
        )

        make_consultation(
            clinic=clinic,
            patient=clinic_patient,
            staff=clinic_staff,
        )

        make_consultation(
            clinic=other_clinic,
            patient=other_patient,
            staff=other_staff,
        )

        patients, pagination = (
            consultation_service.get_patients_seen_by_staff(
                staff_id=clinic_staff.id,
                clinic_id=clinic.id,
            )
        )

        assert [
            patient.id
            for patient in patients
        ] == [
            clinic_patient.id,
        ]

        assert other_patient.id not in {
            patient.id
            for patient in patients
        }

        assert pagination["total"] == 1

    def test_orders_patients_deterministically(
        self,
        clinic,
        make_patient,
        make_staff,
        make_consultation,
    ):
        staff = make_staff(
            clinic=clinic,
        )

        patient_zulu = make_patient(
            clinic=clinic,
            first_name="Zara",
            last_name="Zulu",
        )

        patient_alpha = make_patient(
            clinic=clinic,
            first_name="Alice",
            last_name="Alpha",
        )

        patient_same_last = make_patient(
            clinic=clinic,
            first_name="Brian",
            last_name="Alpha",
        )

        for patient in (
            patient_zulu,
            patient_alpha,
            patient_same_last,
        ):
            make_consultation(
                clinic=clinic,
                patient=patient,
                staff=staff,
            )

        patients, _ = (
            consultation_service.get_patients_seen_by_staff(
                staff_id=staff.id,
                clinic_id=clinic.id,
            )
        )

        assert [
            patient.id
            for patient in patients
        ] == [
            patient_alpha.id,
            patient_same_last.id,
            patient_zulu.id,
        ]

    def test_paginates_results(
        self,
        clinic,
        make_patient,
        make_staff,
        make_consultation,
    ):
        staff = make_staff(
            clinic=clinic,
        )

        patients = [
            make_patient(
                clinic=clinic,
                first_name=f"Patient{index}",
                last_name="Seen",
            )
            for index in range(5)
        ]

        for patient in patients:
            make_consultation(
                clinic=clinic,
                patient=patient,
                staff=staff,
            )

        first_page, first_meta = (
            consultation_service.get_patients_seen_by_staff(
                staff_id=staff.id,
                clinic_id=clinic.id,
                page=1,
                per_page=2,
            )
        )

        second_page, second_meta = (
            consultation_service.get_patients_seen_by_staff(
                staff_id=staff.id,
                clinic_id=clinic.id,
                page=2,
                per_page=2,
            )
        )

        assert len(first_page) == 2
        assert len(second_page) == 2

        assert first_meta["page"] == 1
        assert first_meta["per_page"] == 2
        assert first_meta["total"] == 5
        assert first_meta["pages"] == 3
        assert first_meta["has_next"] is True
        assert first_meta["has_prev"] is False
        assert first_meta["next_page"] == 2
        assert first_meta["prev_page"] is None

        assert second_meta["page"] == 2
        assert second_meta["per_page"] == 2
        assert second_meta["total"] == 5
        assert second_meta["pages"] == 3
        assert second_meta["has_next"] is True
        assert second_meta["has_prev"] is True
        assert second_meta["next_page"] == 3
        assert second_meta["prev_page"] == 1

        assert {
            patient.id
            for patient in first_page
        }.isdisjoint(
            {
                patient.id
                for patient in second_page
            }
        )

    def test_returns_second_page_correctly(
        self,
        clinic,
        make_patient,
        make_staff,
        make_consultation,
    ):
        staff = make_staff(
            clinic=clinic,
        )

        patients = [
            make_patient(
                clinic=clinic,
                first_name=f"Patient{index}",
                last_name="Seen",
            )
            for index in range(3)
        ]

        for patient in patients:
            make_consultation(
                clinic=clinic,
                patient=patient,
                staff=staff,
            )

        results, pagination = (
            consultation_service.get_patients_seen_by_staff(
                staff_id=staff.id,
                clinic_id=clinic.id,
                page=2,
                per_page=2,
            )
        )

        assert len(results) == 1
        assert results[0].id == patients[2].id

        assert pagination["page"] == 2
        assert pagination["per_page"] == 2
        assert pagination["total"] == 3
        assert pagination["pages"] == 2
        assert pagination["has_next"] is False
        assert pagination["has_prev"] is True
        assert pagination["next_page"] is None
        assert pagination["prev_page"] == 1

    def test_empty_result_returns_empty_list_and_metadata(
        self,
        clinic,
        make_staff,
    ):
        staff = make_staff(
            clinic=clinic,
        )

        patients, pagination = (
            consultation_service.get_patients_seen_by_staff(
                staff_id=staff.id,
                clinic_id=clinic.id,
            )
        )

        assert patients == []
        assert pagination["page"] == 1
        assert pagination["per_page"] == 50
        assert pagination["total"] == 0
        assert pagination["pages"] == 0
        assert pagination["has_next"] is False
        assert pagination["has_prev"] is False
        assert pagination["next_page"] is None
        assert pagination["prev_page"] is None

    def test_empty_last_page_is_returned_without_error(
        self,
        clinic,
        make_patient,
        make_staff,
        make_consultation,
    ):
        staff = make_staff(
            clinic=clinic,
        )

        patient = make_patient(
            clinic=clinic,
        )

        make_consultation(
            clinic=clinic,
            patient=patient,
            staff=staff,
        )

        results, pagination = (
            consultation_service.get_patients_seen_by_staff(
                staff_id=staff.id,
                clinic_id=clinic.id,
                page=2,
                per_page=1,
            )
        )

        assert results == []
        assert pagination["page"] == 2
        assert pagination["per_page"] == 1
        assert pagination["total"] == 1
        assert pagination["pages"] == 1
        assert pagination["has_next"] is False
        assert pagination["has_prev"] is True
        assert pagination["next_page"] is None
        assert pagination["prev_page"] == 1


class TestGetConsultationsForStaff:
    def test_returns_staff_consultations(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
        )

        results, pagination = (
            consultation_service.get_consultations_for_staff(
                staff.id,
                clinic.id,
            )
        )

        assert consultation.id in [
            item.id
            for item in results
        ]
        assert pagination["total"] == 1

    def test_filters_by_status(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        in_progress = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.IN_PROGRESS,
        )

        completed = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.COMPLETED,
        )

        results, pagination = (
            consultation_service.get_consultations_for_staff(
                staff.id,
                clinic.id,
                status=ConsultationStatus.IN_PROGRESS,
            )
        )

        ids = [item.id for item in results]

        assert in_progress.id in ids
        assert completed.id not in ids
        assert pagination["total"] == 1

    def test_filters_by_consultation_type(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        general = make_consultation(
            clinic,
            patient,
            staff,
            consultation_type=ConsultationType.GENERAL,
        )

        specialist = make_consultation(
            clinic,
            patient,
            staff,
            consultation_type=ConsultationType.SPECIALIST,
        )

        results, pagination = (
            consultation_service.get_consultations_for_staff(
                staff.id,
                clinic.id,
                consultation_type=ConsultationType.SPECIALIST,
            )
        )

        ids = [item.id for item in results]

        assert specialist.id in ids
        assert general.id not in ids
        assert pagination["total"] == 1

    def test_filters_by_status_and_consultation_type(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        matching = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.IN_PROGRESS,
            consultation_type=ConsultationType.SPECIALIST,
        )

        wrong_type = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.IN_PROGRESS,
            consultation_type=ConsultationType.GENERAL,
        )

        wrong_status = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.COMPLETED,
            consultation_type=ConsultationType.SPECIALIST,
        )

        results, pagination = (
            consultation_service.get_consultations_for_staff(
                staff.id,
                clinic.id,
                status=ConsultationStatus.IN_PROGRESS,
                consultation_type=ConsultationType.SPECIALIST,
            )
        )

        ids = [item.id for item in results]

        assert matching.id in ids
        assert wrong_type.id not in ids
        assert wrong_status.id not in ids
        assert pagination["total"] == 1

    def test_filters_by_consultation_type_with_pagination(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        specialist = [
            make_consultation(
                clinic,
                patient,
                staff,
                consultation_type=ConsultationType.SPECIALIST,
                started_at=(
                    datetime.now(timezone.utc)
                    - timedelta(days=index)
                ),
            )
            for index in range(3)
        ]

        make_consultation(
            clinic,
            patient,
            staff,
            consultation_type=ConsultationType.GENERAL,
        )

        results, pagination = (
            consultation_service.get_consultations_for_staff(
                staff.id,
                clinic.id,
                consultation_type=ConsultationType.SPECIALIST,
                page=1,
                per_page=2,
            )
        )

        assert [item.id for item in results] == [
            specialist[0].id,
            specialist[1].id,
        ]

        assert pagination["page"] == 1
        assert pagination["per_page"] == 2
        assert pagination["total"] == 3
        assert pagination["pages"] == 2
        assert pagination["has_next"] is True
        assert pagination["has_prev"] is False
        assert pagination["next_page"] == 2
        assert pagination["prev_page"] is None

    def test_returns_empty_results_for_non_matching_consultation_type(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        make_consultation(
            clinic,
            patient,
            staff,
            consultation_type=ConsultationType.GENERAL,
        )

        results, pagination = (
            consultation_service.get_consultations_for_staff(
                staff.id,
                clinic.id,
                consultation_type=ConsultationType.EMERGENCY,
            )
        )

        assert results == []
        assert pagination["total"] == 0
        assert pagination["pages"] == 0
        assert pagination["has_next"] is False
        assert pagination["has_prev"] is False
        assert pagination["next_page"] is None
        assert pagination["prev_page"] is None

    def test_wrong_clinic_staff_is_hidden(
        self,
        clinic,
        make_clinic,
        make_staff,
    ):
        other_clinic = make_clinic(
            name="Other Staff History Clinic",
        )

        other_staff = make_staff(
            other_clinic,
        )

        with pytest.raises(
            NotFoundError,
            match=f"Staff member {other_staff.id} not found",
        ):
            consultation_service.get_consultations_for_staff(
                other_staff.id,
                clinic.id,
            )

    @pytest.mark.parametrize(
        "staff_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_staff_id(
        self,
        clinic,
        staff_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Staff ID must be greater than 0",
        ):
            consultation_service.get_consultations_for_staff(
                staff_id,
                clinic.id,
            )

    @pytest.mark.parametrize(
        "clinic_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_clinic_id(
        self,
        staff,
        clinic_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Clinic ID must be greater than 0",
        ):
            consultation_service.get_consultations_for_staff(
                staff.id,
                clinic_id,
            )

    @pytest.mark.parametrize(
        ("page", "per_page"),
        [
            (0, 50),
            (-1, 50),
            (1, 0),
            (1, -1),
            (1, 501),
        ],
    )
    def test_rejects_invalid_pagination(
        self,
        clinic,
        staff,
        page,
        per_page,
    ):
        with pytest.raises(
            ValidationError,
        ):
            consultation_service.get_consultations_for_staff(
                staff.id,
                clinic.id,
                page=page,
                per_page=per_page,
            )

    def test_paginates_results(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        consultations = [
            make_consultation(
                clinic,
                patient,
                staff,
                started_at=(
                    datetime.now(timezone.utc)
                    - timedelta(days=index)
                ),
            )
            for index in range(3)
        ]

        results, pagination = (
            consultation_service.get_consultations_for_staff(
                staff.id,
                clinic.id,
                page=1,
                per_page=2,
            )
        )

        assert [item.id for item in results] == [
            consultations[0].id,
            consultations[1].id,
        ]

        assert pagination["page"] == 1
        assert pagination["per_page"] == 2
        assert pagination["total"] == 3
        assert pagination["pages"] == 2
        assert pagination["has_next"] is True
        assert pagination["has_prev"] is False
        assert pagination["next_page"] == 2
        assert pagination["prev_page"] is None

    def test_paginates_with_status_filter(
        self,
        clinic,
        patient,
        staff,
        make_consultation,
    ):
        matching = [
            make_consultation(
                clinic,
                patient,
                staff,
                status=ConsultationStatus.IN_PROGRESS,
                started_at=(
                    datetime.now(timezone.utc)
                    - timedelta(days=index)
                ),
            )
            for index in range(3)
        ]

        make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.COMPLETED,
        )

        results, pagination = (
            consultation_service.get_consultations_for_staff(
                staff.id,
                clinic.id,
                status=ConsultationStatus.IN_PROGRESS,
                page=1,
                per_page=2,
            )
        )

        assert [item.id for item in results] == [
            matching[0].id,
            matching[1].id,
        ]

        assert pagination["total"] == 3
        assert pagination["pages"] == 2
        assert pagination["has_next"] is True
        assert pagination["next_page"] == 2


class TestCreateConsultationTemplate:
    def test_creates_global_template(
        self,
        no_audit,
    ):
        template = consultation_service.create_consultation_template(
            name="Global Template",
            structure={"sections": ["diagnosis"]},
            clinic_id=None,
        )

        assert template.id is not None
        assert template.clinic_id is None
        assert template.name == "Global Template"
        assert template.structure == {
            "sections": ["diagnosis"],
        }
        assert template.is_active is True
        no_audit.assert_called_once()

    def test_creates_clinic_template(
        self,
        clinic,
        no_audit,
    ):
        template = consultation_service.create_consultation_template(
            name="Clinic Template",
            structure={"sections": ["history"]},
            clinic_id=clinic.id,
        )

        assert template.clinic_id == clinic.id
        no_audit.assert_called_once()

    def test_strips_name(
        self,
        no_audit,
    ):
        template = consultation_service.create_consultation_template(
            name="  General Template  ",
            structure={"sections": ["diagnosis"]},
        )

        assert template.name == "General Template"

    def test_strips_specialty(
        self,
        no_audit,
    ):
        template = consultation_service.create_consultation_template(
            name="Specialty Template",
            structure={"sections": ["diagnosis"]},
            specialty="  Cardiology  ",
        )

        assert template.specialty == "Cardiology"

    def test_requires_name(
        self,
        no_audit,
    ):
        with pytest.raises(
            ValidationError,
            match="Template name is required",
        ):
            consultation_service.create_consultation_template(
                name="   ",
                structure={"sections": ["diagnosis"]},
            )

        no_audit.assert_not_called()

    @pytest.mark.parametrize(
        "structure",
        [None, [], "not-an-object"],
    )
    def test_requires_structure_object(
        self,
        structure,
        no_audit,
    ):
        with pytest.raises(
            ValidationError,
            match="Template structure must be an object",
        ):
            consultation_service.create_consultation_template(
                name="Invalid Structure",
                structure=structure,
            )

        no_audit.assert_not_called()

    def test_rejects_empty_structure(
        self,
        no_audit,
    ):
        with pytest.raises(
            ValidationError,
            match="Template structure cannot be empty",
        ):
            consultation_service.create_consultation_template(
                name="Empty Structure",
                structure={},
            )

        no_audit.assert_not_called()

    @pytest.mark.parametrize(
        "clinic_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_clinic_id(
        self,
        clinic_id,
        no_audit,
    ):
        with pytest.raises(
            ValidationError,
            match="Clinic ID must be greater than 0",
        ):
            consultation_service.create_consultation_template(
                name="Invalid Clinic",
                structure={"sections": ["diagnosis"]},
                clinic_id=clinic_id,
            )

        no_audit.assert_not_called()

    def test_inactive_clinic_cannot_create_template(
        self,
        suspended_clinic,
        no_audit,
    ):
        with pytest.raises(
            Exception,
        ):
            consultation_service.create_consultation_template(
                name="Suspended Clinic Template",
                structure={"sections": ["diagnosis"]},
                clinic_id=suspended_clinic.id,
            )

        no_audit.assert_not_called()


class TestGetActiveTemplates:
    def test_returns_active_global_and_clinic_templates(
        self,
        clinic,
        make_clinic,
        make_template,
    ):
        global_template = make_template(
            clinic=None,
            is_active=True,
            name="A Global",
        )

        clinic_template = make_template(
            clinic=clinic,
            is_active=True,
            name="B Clinic",
        )

        other_clinic = make_clinic(
            name="Template Other Clinic",
        )

        other_template = make_template(
            clinic=other_clinic,
            is_active=True,
            name="C Other",
        )

        inactive = make_template(
            clinic=clinic,
            is_active=False,
            name="D Inactive",
        )

        results, pagination = (
            consultation_service.get_active_templates(
                clinic.id,
            )
        )

        ids = [item.id for item in results]

        assert global_template.id in ids
        assert clinic_template.id in ids
        assert other_template.id not in ids
        assert inactive.id not in ids
        assert pagination["total"] == 2

    def test_rejects_missing_clinic_scope(self):
        with pytest.raises(
            ValidationError,
            match="Clinic ID is required for consultation template lookup",
        ):
            consultation_service.get_active_templates()

    def test_returns_templates_in_name_order(
        self,
        clinic,
        make_template,
    ):
        first = make_template(
            clinic=clinic,
            is_active=True,
            name="Alpha",
        )

        second = make_template(
            clinic=clinic,
            is_active=True,
            name="Zulu",
        )

        results, _ = (
            consultation_service.get_active_templates(
                clinic.id,
            )
        )

        relevant = [
            item.id
            for item in results
            if item.id in {
                first.id,
                second.id,
            }
        ]

        assert relevant == [
            first.id,
            second.id,
        ]

    @pytest.mark.parametrize(
        "clinic_id",
        [0, -1, -100],
    )
    def test_rejects_non_positive_clinic_id(
        self,
        clinic_id,
    ):
        with pytest.raises(
            ValidationError,
            match="Clinic ID must be greater than 0",
        ):
            consultation_service.get_active_templates(
                clinic_id,
            )

    @pytest.mark.parametrize(
        ("page", "per_page"),
        [
            (0, 50),
            (-1, 50),
            (1, 0),
            (1, -1),
            (1, 501),
        ],
    )
    def test_rejects_invalid_pagination(
        self,
        clinic,
        page,
        per_page,
    ):
        with pytest.raises(
            ValidationError,
        ):
            consultation_service.get_active_templates(
                clinic.id,
                page=page,
                per_page=per_page,
            )

    def test_paginates_templates(
        self,
        clinic,
        make_template,
    ):
        templates = [
            make_template(
                clinic=clinic,
                is_active=True,
                name=f"Template {index}",
            )
            for index in range(3)
        ]

        results, pagination = (
            consultation_service.get_active_templates(
                clinic.id,
                page=1,
                per_page=2,
            )
        )

        assert [item.id for item in results] == [
            templates[0].id,
            templates[1].id,
        ]

        assert pagination["page"] == 1
        assert pagination["per_page"] == 2
        assert pagination["total"] == 3
        assert pagination["pages"] == 2
        assert pagination["has_next"] is True
        assert pagination["has_prev"] is False
        assert pagination["next_page"] == 2
        assert pagination["prev_page"] is None

    def test_returns_second_template_page(
        self,
        clinic,
        make_template,
    ):
        templates = [
            make_template(
                clinic=clinic,
                is_active=True,
                name=f"Template {index}",
            )
            for index in range(3)
        ]

        results, pagination = (
            consultation_service.get_active_templates(
                clinic.id,
                page=2,
                per_page=2,
            )
        )

        assert [item.id for item in results] == [
            templates[2].id,
        ]

        assert pagination["page"] == 2
        assert pagination["per_page"] == 2
        assert pagination["total"] == 3
        assert pagination["pages"] == 2
        assert pagination["has_next"] is False
        assert pagination["has_prev"] is True
        assert pagination["next_page"] is None
        assert pagination["prev_page"] == 1

    def test_empty_template_page_is_returned_without_error(
        self,
        clinic,
        make_template,
    ):
        make_template(
            clinic=clinic,
            is_active=True,
            name="Only Template",
        )

        results, pagination = (
            consultation_service.get_active_templates(
                clinic.id,
                page=2,
                per_page=1,
            )
        )

        assert results == []
        assert pagination["page"] == 2
        assert pagination["per_page"] == 1
        assert pagination["total"] == 1
        assert pagination["pages"] == 1
        assert pagination["has_next"] is False
        assert pagination["has_prev"] is True
        assert pagination["next_page"] is None
        assert pagination["prev_page"] == 1


class TestConsultationLifecycleValidators:
    def test_completed_cannot_be_completed_again(
        self,
        make_consultation,
        clinic,
        patient,
        staff,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.COMPLETED,
        )

        with pytest.raises(
            ConflictError,
            match=f"Consultation {consultation.id} is already completed",
        ):
            consultation_service._validate_consultation_can_be_completed(
                consultation,
            )

    def test_cancelled_cannot_be_completed(
        self,
        make_consultation,
        clinic,
        patient,
        staff,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.CANCELLED,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Consultation {consultation.id} is cancelled "
                "and cannot be completed"
            ),
        ):
            consultation_service._validate_consultation_can_be_completed(
                consultation,
            )

    def test_non_in_progress_cannot_be_completed(
        self,
        make_consultation,
        clinic,
        patient,
        staff,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.COMPLETED,
        )

        with pytest.raises(
            ConflictError,
        ):
            consultation_service._validate_consultation_can_be_completed(
                consultation,
            )

    def test_completed_cannot_be_cancelled(
        self,
        make_consultation,
        clinic,
        patient,
        staff,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.COMPLETED,
        )

        with pytest.raises(
            ConflictError,
            match=(
                f"Consultation {consultation.id} is already completed "
                "and cannot be cancelled"
            ),
        ):
            consultation_service._validate_consultation_can_be_cancelled(
                consultation,
            )

    def test_cancelled_cannot_be_cancelled_again(
        self,
        make_consultation,
        clinic,
        patient,
        staff,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.CANCELLED,
        )

        with pytest.raises(
            ConflictError,
            match=f"Consultation {consultation.id} is already cancelled",
        ):
            consultation_service._validate_consultation_can_be_cancelled(
                consultation,
            )

    def test_non_in_progress_cannot_be_cancelled(
        self,
        make_consultation,
        clinic,
        patient,
        staff,
    ):
        consultation = make_consultation(
            clinic,
            patient,
            staff,
            status=ConsultationStatus.COMPLETED,
        )

        with pytest.raises(
            ConflictError,
        ):
            consultation_service._validate_consultation_can_be_cancelled(
                consultation,
            )
