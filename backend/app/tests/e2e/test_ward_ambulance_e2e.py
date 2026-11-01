from __future__ import annotations

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
from app.core.enums.medication_reconciliation_enums import (
    MedicationReconciliationEvent,
)
from app.core.enums.role_enums import Role
from app.modules.ambulance.models.ambulance_model import (
    AmbulanceTrip,
    AmbulanceVehicle,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.medication_reconciliation.services import (
    medication_reconciliation_service as med_recon_service,
)
from app.modules.ward.models.ward_model import (
    Admission,
    Bed,
    BedReservation,
    Ward,
)


def test_ward_ambulance_e2e(
    client,
    db,
    clinic,
    make_clinic,
    make_user,
    make_staff,
    e2e_login,
):
    admin_staff = make_staff(
        clinic=clinic,
        role=Role.ADMIN,
        user_overrides={
            "email": "e2e-ward-ambulance-admin@test.com",
        },
    )

    nurse_staff = make_staff(
        clinic=clinic,
        role=Role.NURSE,
        user_overrides={
            "email": "e2e-ward-ambulance-nurse@test.com",
        },
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "e2e-ward-ambulance-doctor@test.com",
        },
    )

    driver_staff = make_staff(
        clinic=clinic,
        role=Role.DRIVER,
        user_overrides={
            "email": "e2e-ward-ambulance-driver@test.com",
        },
    )

    receptionist = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        email="e2e-ward-ambulance-receptionist@test.com",
    )

    admin_login = e2e_login(
        "e2e-ward-ambulance-admin@test.com",
    )

    nurse_login = e2e_login(
        "e2e-ward-ambulance-nurse@test.com",
    )

    doctor_login = e2e_login(
        "e2e-ward-ambulance-doctor@test.com",
    )

    driver_login = e2e_login(
        "e2e-ward-ambulance-driver@test.com",
    )

    receptionist_login = e2e_login(
        "e2e-ward-ambulance-receptionist@test.com",
    )

    assert admin_login["user_id"] == admin_staff.user.id
    assert admin_login["role"] == Role.ADMIN.value

    assert nurse_login["user_id"] == nurse_staff.user.id
    assert nurse_login["role"] == Role.NURSE.value

    assert doctor_login["user_id"] == doctor_staff.user.id
    assert doctor_login["role"] == Role.DOCTOR.value

    assert driver_login["user_id"] == driver_staff.user.id
    assert driver_login["role"] == Role.DRIVER.value

    assert receptionist_login["user_id"] == receptionist.id
    assert receptionist_login["role"] == Role.RECEPTIONIST.value

    # =========================================================================
    # PATIENT
    # =========================================================================

    patient_response = client.post(
        "/api/v1/patients",
        json={
            "first_name": "Ward",
            "last_name": "Ambulance",
        },
        headers={
            "Authorization": (
                f"Bearer {receptionist_login['access_token']}"
            ),
        },
    )

    assert patient_response.status_code == 201, (
        patient_response.get_json()
    )

    patient_body = patient_response.get_json()

    assert patient_body["success"] is True
    assert patient_body["data"]["clinic_id"] == clinic.id

    patient_id = patient_body["data"]["id"]

    persisted_patient = db.session.get(
        Patient,
        patient_id,
    )

    assert persisted_patient is not None
    assert persisted_patient.clinic_id == clinic.id

    # =========================================================================
    # WARD
    # =========================================================================

    ward_response = client.post(
        "/api/v1/wards",
        json={
            "name": "E2E General Ward",
            "ward_type": "general",
            "capacity": 2,
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert ward_response.status_code == 201, (
        ward_response.get_json()
    )

    ward_body = ward_response.get_json()

    assert ward_body["message"] == "Ward created successfully"
    assert ward_body["ward"]["clinic_id"] == clinic.id
    assert ward_body["ward"]["name"] == "E2E General Ward"
    assert ward_body["ward"]["capacity"] == 2

    ward_id = ward_body["ward"]["id"]

    persisted_ward = db.session.get(
        Ward,
        ward_id,
    )

    assert persisted_ward is not None
    assert persisted_ward.clinic_id == clinic.id
    assert persisted_ward.capacity == 2

    bed_one_response = client.post(
        f"/api/v1/wards/{ward_id}/beds",
        json={
            "bed_number": "E2E-BED-01",
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert bed_one_response.status_code == 201, (
        bed_one_response.get_json()
    )

    bed_one_body = bed_one_response.get_json()

    assert bed_one_body["bed"]["ward_id"] == ward_id
    assert bed_one_body["bed"]["status"] == "available"

    bed_one_id = bed_one_body["bed"]["id"]

    bed_two_response = client.post(
        f"/api/v1/wards/{ward_id}/beds",
        json={
            "bed_number": "E2E-BED-02",
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert bed_two_response.status_code == 201, (
        bed_two_response.get_json()
    )

    bed_two_body = bed_two_response.get_json()

    assert bed_two_body["bed"]["ward_id"] == ward_id
    assert bed_two_body["bed"]["status"] == "available"

    bed_two_id = bed_two_body["bed"]["id"]

    persisted_bed_one = db.session.get(
        Bed,
        bed_one_id,
    )

    persisted_bed_two = db.session.get(
        Bed,
        bed_two_id,
    )

    assert persisted_bed_one is not None
    assert persisted_bed_two is not None
    assert persisted_bed_one.ward_id == ward_id
    assert persisted_bed_two.ward_id == ward_id

    # =========================================================================
    # RESERVATION
    # =========================================================================

    reservation_response = client.post(
        "/api/v1/wards/reservations",
        json={
            "patient_id": patient_id,
            "bed_id": bed_one_id,
            "reason": "Phase 8 ward admission",
        },
        headers={
            "Authorization": (
                f"Bearer {nurse_login['access_token']}"
            ),
        },
    )

    assert reservation_response.status_code == 201, (
        reservation_response.get_json()
    )

    reservation_body = reservation_response.get_json()

    assert (
        reservation_body["message"]
        == "Bed reserved successfully"
    )
    assert reservation_body["reservation"]["patient_id"] == patient_id
    assert reservation_body["reservation"]["bed_id"] == bed_one_id
    assert reservation_body["reservation"]["status"] == "pending"
    assert (
        reservation_body["reservation"]["reserved_by_id"]
        == nurse_staff.id
    )

    reservation_id = reservation_body["reservation"]["id"]

    persisted_reservation = db.session.get(
        BedReservation,
        reservation_id,
    )

    assert persisted_reservation is not None
    assert persisted_reservation.patient_id == patient_id
    assert persisted_reservation.bed_id == bed_one_id

    persisted_bed_one = db.session.get(
        Bed,
        bed_one_id,
    )

    assert persisted_bed_one.status.value == "reserved"

    # =========================================================================
    # ADMISSION FROM RESERVATION
    # =========================================================================

    admission_response = client.post(
        f"/api/v1/wards/reservations/{reservation_id}/admit",
        json={
            "reason": "Phase 8 inpatient admission",
        },
        headers={
            "Authorization": (
                f"Bearer {nurse_login['access_token']}"
            ),
        },
    )

    assert admission_response.status_code == 201, (
        admission_response.get_json()
    )

    admission_body = admission_response.get_json()

    assert (
        admission_body["message"]
        == "Patient admitted from reservation"
    )
    assert admission_body["admission"]["patient_id"] == patient_id
    assert admission_body["admission"]["bed_id"] == bed_one_id
    assert (
        admission_body["admission"]["admitted_by_id"]
        == nurse_staff.id
    )
    assert admission_body["admission"]["reservation_id"] == reservation_id
    assert admission_body["admission"]["status"] == "admitted"

    admission_id = admission_body["admission"]["id"]

    persisted_admission = db.session.get(
        Admission,
        admission_id,
    )

    assert persisted_admission is not None
    assert persisted_admission.patient_id == patient_id
    assert persisted_admission.bed_id == bed_one_id
    assert persisted_admission.reservation_id == reservation_id

    persisted_reservation = db.session.get(
        BedReservation,
        reservation_id,
    )

    assert persisted_reservation.status.value == "fulfilled"

    persisted_bed_one = db.session.get(
        Bed,
        bed_one_id,
    )

    assert persisted_bed_one.status.value == "occupied"

    # =========================================================================
    # TRANSFER
    # =========================================================================

    transfer_response = client.post(
        f"/api/v1/wards/admissions/{admission_id}/transfer",
        json={
            "to_bed_id": bed_two_id,
            "reason": "Phase 8 bed transfer",
        },
        headers={
            "Authorization": (
                f"Bearer {nurse_login['access_token']}"
            ),
        },
    )

    assert transfer_response.status_code == 201, (
        transfer_response.get_json()
    )

    transfer_body = transfer_response.get_json()

    assert transfer_body["message"] == "Patient transferred successfully"
    assert transfer_body["transfer"]["admission_id"] == admission_id
    assert transfer_body["transfer"]["from_bed_id"] == bed_one_id
    assert transfer_body["transfer"]["to_bed_id"] == bed_two_id

    persisted_admission = db.session.get(
        Admission,
        admission_id,
    )

    assert persisted_admission.bed_id == bed_two_id
    assert persisted_admission.status.value == "admitted"

    persisted_bed_one = db.session.get(
        Bed,
        bed_one_id,
    )

    persisted_bed_two = db.session.get(
        Bed,
        bed_two_id,
    )

    assert persisted_bed_one.status.value == "available"
    assert persisted_bed_two.status.value == "occupied"

    # =========================================================================
    # AMBULANCE VEHICLE
    # =========================================================================

    vehicle_response = client.post(
        "/api/v1/ambulance/vehicles",
        json={
            "plate_number": "E2E-AMB-001",
            "equipment_level": "bls",
            "capacity": 1,
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert vehicle_response.status_code == 201, (
        vehicle_response.get_json()
    )

    vehicle_body = vehicle_response.get_json()

    assert vehicle_body["success"] is True
    assert vehicle_body["data"]["clinic_id"] == clinic.id
    assert vehicle_body["data"]["plate_number"] == "E2E-AMB-001"
    assert vehicle_body["data"]["status"] == "available"

    vehicle_id = vehicle_body["data"]["id"]

    persisted_vehicle = db.session.get(
        AmbulanceVehicle,
        vehicle_id,
    )

    assert persisted_vehicle is not None
    assert persisted_vehicle.clinic_id == clinic.id

    # =========================================================================
    # AMBULANCE TRIP REQUEST
    # =========================================================================

    trip_response = client.post(
        "/api/v1/ambulance/trips",
        json={
            "trip_type": "inter_facility_transfer",
            "patient_id": patient_id,
            "admission_id": admission_id,
            "pickup_address": "E2E Ward",
            "destination_address": "E2E Receiving Hospital",
            "notes": "Phase 8 ambulance workflow",
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert trip_response.status_code == 201, (
        trip_response.get_json()
    )

    trip_body = trip_response.get_json()

    assert trip_body["success"] is True
    assert trip_body["data"]["clinic_id"] == clinic.id
    assert trip_body["data"]["patient_id"] == patient_id
    assert trip_body["data"]["admission_id"] == admission_id
    assert trip_body["data"]["trip_type"] == "inter_facility_transfer"
    assert trip_body["data"]["status"] == "requested"

    trip_id = trip_body["data"]["id"]

    persisted_trip = db.session.get(
        AmbulanceTrip,
        trip_id,
    )

    assert persisted_trip is not None
    assert persisted_trip.clinic_id == clinic.id
    assert persisted_trip.patient_id == patient_id
    assert persisted_trip.admission_id == admission_id

    # =========================================================================
    # DISPATCH
    # =========================================================================

    dispatch_response = client.post(
        f"/api/v1/ambulance/trips/{trip_id}/dispatch",
        json={
            "vehicle_id": vehicle_id,
            "driver_id": driver_staff.id,
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert dispatch_response.status_code == 200, (
        dispatch_response.get_json()
    )

    dispatch_body = dispatch_response.get_json()

    assert dispatch_body["success"] is True
    assert dispatch_body["data"]["status"] == "dispatched"
    assert dispatch_body["data"]["vehicle_id"] == vehicle_id
    assert dispatch_body["data"]["driver_id"] == driver_staff.id

    persisted_trip = db.session.get(
        AmbulanceTrip,
        trip_id,
    )

    assert persisted_trip.status.value == "dispatched"
    assert persisted_trip.vehicle_id == vehicle_id
    assert persisted_trip.driver_id == driver_staff.id

    persisted_vehicle = db.session.get(
        AmbulanceVehicle,
        vehicle_id,
    )

    assert persisted_vehicle.status.value == "on_trip"

    # =========================================================================
    # AMBULANCE STATUS LIFECYCLE
    # =========================================================================

    lifecycle = [
        ("en_route_to_pickup", "en_route_to_pickup"),
        ("at_pickup", "at_pickup"),
        ("patient_on_board", "patient_on_board"),
        ("en_route_to_destination", "en_route_to_destination"),
    ]

    for status_value, expected_status in lifecycle:
        status_response = client.patch(
            f"/api/v1/ambulance/trips/{trip_id}/status",
            json={
                "status": status_value,
            },
            headers={
                "Authorization": (
                    f"Bearer {driver_login['access_token']}"
                ),
            },
        )

        assert status_response.status_code == 200, (
            status_response.get_json()
        )

        status_body = status_response.get_json()

        assert status_body["success"] is True
        assert status_body["data"]["status"] == expected_status

    persisted_trip = db.session.get(
        AmbulanceTrip,
        trip_id,
    )

    assert persisted_trip.status.value == "en_route_to_destination"

    # =========================================================================
    # COMPLETE AMBULANCE TRIP
    # =========================================================================

    complete_response = client.post(
        f"/api/v1/ambulance/trips/{trip_id}/complete",
        headers={
            "Authorization": (
                f"Bearer {driver_login['access_token']}"
            ),
        },
    )

    assert complete_response.status_code == 200, (
        complete_response.get_json()
    )

    complete_body = complete_response.get_json()

    assert complete_body["success"] is True
    assert complete_body["data"]["status"] == "completed"
    assert complete_body["data"]["vehicle_id"] == vehicle_id
    assert complete_body["data"]["driver_id"] == driver_staff.id

    persisted_trip = db.session.get(
        AmbulanceTrip,
        trip_id,
    )

    assert persisted_trip.status.value == "completed"

    persisted_vehicle = db.session.get(
        AmbulanceVehicle,
        vehicle_id,
    )

    assert persisted_vehicle.status.value == "available"

    # =========================================================================
    # DISCHARGE
    # =========================================================================

    reconciliation = (
        med_recon_service.create_medication_reconciliation(
            clinic_id=clinic.id,
            admission_id=admission_id,
            event_type=MedicationReconciliationEvent.DISCHARGE,
            actor_staff_id=doctor_staff.id,
            no_medications_reported=True,
            notes="E2E discharge medication review",
            items=[],
        )
    )

    med_recon_service.finalize_medication_reconciliation(
        reconciliation.id,
        clinic_id=clinic.id,
        actor_staff_id=doctor_staff.id,
        expected_version=1,
    )

    discharge_response = client.post(
        f"/api/v1/wards/admissions/{admission_id}/discharge",
        json={
            "reason": "Transferred via ambulance",
            "primary_diagnosis": "Transfer completed",
            "secondary_diagnoses": [],
            "condition_at_discharge": "Stable for transfer",
            "instructions": (
                "Continue care at receiving facility"
            ),
            "follow_up_plan": (
                "Follow receiving facility care plan"
            ),
            "destination": "Receiving Hospital",
        },
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
            "Idempotency-Key": (
                "e2e-ward-ambulance-discharge-1"
            ),
        },
    )

    assert discharge_response.status_code == 200, (
        discharge_response.get_json()
    )

    discharge_body = discharge_response.get_json()

    assert (
        discharge_body["message"]
        == "Patient discharged successfully"
    )
    assert discharge_body["admission"]["id"] == admission_id
    assert discharge_body["admission"]["status"] == "discharged"

    persisted_admission = db.session.get(
        Admission,
        admission_id,
    )

    assert persisted_admission.status.value == "discharged"
    assert persisted_admission.discharged_at is not None

    persisted_bed_two = db.session.get(
        Bed,
        bed_two_id,
    )

    assert persisted_bed_two.status.value == "available"

    # =========================================================================
    # AUDIT TENANT CONTEXT
    # =========================================================================

    ward_entity_ids = [
        ward_id,
        bed_one_id,
        bed_two_id,
        reservation_id,
        admission_id,
    ]

    ward_audits = list(
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.entity_id.in_(ward_entity_ids)
            )
            .order_by(
                AuditLog.id.asc(),
            )
        ).scalars()
    )

    assert ward_audits

    ward_entity_types = {
        "ward",
        "bed",
        "bed_reservation",
        "admission",
    }

    ward_audits = [
        row
        for row in ward_audits
        if row.entity_type in ward_entity_types
    ]

    assert ward_audits
    assert all(
        row.clinic_id == clinic.id
        for row in ward_audits
    )

    assert any(
        row.entity_type == "ward"
        and row.entity_id == ward_id
        and row.action is AuditAction.CREATE
        for row in ward_audits
    )

    assert any(
        row.entity_type == "bed"
        and row.entity_id == bed_one_id
        and row.action is AuditAction.CREATE
        for row in ward_audits
    )

    assert any(
        row.entity_type == "bed"
        and row.entity_id == bed_two_id
        and row.action is AuditAction.CREATE
        for row in ward_audits
    )

    assert any(
        row.entity_type == "bed_reservation"
        and row.entity_id == reservation_id
        and row.action is AuditAction.CREATE
        for row in ward_audits
    )

    assert any(
        row.entity_type == "admission"
        and row.entity_id == admission_id
        and row.action is AuditAction.CREATE
        for row in ward_audits
    )

    ambulance_entity_ids = [
        vehicle_id,
        trip_id,
    ]

    ambulance_audits = list(
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.entity_id.in_(
                    ambulance_entity_ids
                )
            )
            .order_by(
                AuditLog.id.asc(),
            )
        ).scalars()
    )

    assert ambulance_audits

    ambulance_audits = [
        row
        for row in ambulance_audits
        if row.entity_type
        in {
            "AmbulanceVehicle",
            "AmbulanceTrip",
        }
    ]

    assert ambulance_audits
    assert all(
        row.clinic_id == clinic.id
        for row in ambulance_audits
    )

    assert any(
        row.entity_type == "AmbulanceVehicle"
        and row.entity_id == vehicle_id
        and row.action is AuditAction.CREATE
        for row in ambulance_audits
    )

    assert any(
        row.entity_type == "AmbulanceTrip"
        and row.entity_id == trip_id
        and row.action is AuditAction.CREATE
        for row in ambulance_audits
    )

    assert any(
        row.entity_type == "AmbulanceTrip"
        and row.entity_id == trip_id
        and row.action is AuditAction.STATUS_CHANGE
        for row in ambulance_audits
    )

    # =========================================================================
    # CROSS-TENANT DENIAL
    # =========================================================================

    second_clinic = make_clinic()

    second_admin = make_staff(
        clinic=second_clinic,
        role=Role.ADMIN,
        user_overrides={
            "email": "e2e-ward-ambulance-admin-clinic-2@test.com",
        },
    )

    second_admin_login = e2e_login(
        "e2e-ward-ambulance-admin-clinic-2@test.com",
    )

    assert (
        second_admin_login["user_id"]
        == second_admin.user.id
    )
    assert second_admin_login["role"] == Role.ADMIN.value

    foreign_ward_response = client.get(
        f"/api/v1/wards/{ward_id}",
        headers={
            "Authorization": (
                f"Bearer {second_admin_login['access_token']}"
            ),
        },
    )

    assert foreign_ward_response.status_code == 404

    foreign_vehicle_response = client.get(
        f"/api/v1/ambulance/vehicles/{vehicle_id}",
        headers={
            "Authorization": (
                f"Bearer {second_admin_login['access_token']}"
            ),
        },
    )

    assert foreign_vehicle_response.status_code in (404, 422)

    foreign_trip_response = client.get(
        f"/api/v1/ambulance/trips/{trip_id}",
        headers={
            "Authorization": (
                f"Bearer {second_admin_login['access_token']}"
            ),
        },
    )

    assert foreign_trip_response.status_code in (404, 422)

    print("PHASE8_E2E_WARD_AMBULANCE=PASS")
