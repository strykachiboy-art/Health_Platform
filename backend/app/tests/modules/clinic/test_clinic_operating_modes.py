"""Clinic Hours 01: focused contract cases; synthetic data only."""
from datetime import time

import pytest

from app.core.enums.role_enums import Role
from app.modules.clinic.services.clinic_operating_modes import (
    describe_operating_mode, normalize_explicit_operating_mode,
)
from app.modules.clinic.services.clinic_service import create_clinic, update_clinic
from app.core.exceptions import ValidationError


@pytest.mark.parametrize("opening,closing,expected", [
    (None, None, "unspecified"),
    (time(8, 0), None, "unspecified"),
    (time(8, 0), time(17, 0), "scheduled"),
    (time(20, 0), time(6, 0), "scheduled"),
    (time(0, 0), time(0, 0), "always_open"),
    (time(8, 0), time(8, 0), "unspecified"),
])
def test_derived_mode_is_not_access_control(opening, closing, expected):
    assert describe_operating_mode(opening, closing) == expected


def test_explicit_modes_accept_overnight_and_247():
    assert normalize_explicit_operating_mode(None, None, "always_open") == (time(0), time(0))
    assert normalize_explicit_operating_mode(time(20), time(6), "scheduled") == (time(20), time(6))
    assert normalize_explicit_operating_mode(None, None, "unspecified") == (None, None)


@pytest.mark.parametrize("opening,closing,mode", [
    (time(8), None, "scheduled"),
    (time(8), time(8), "scheduled"),
    (time(8), time(17), "always_open"),
    (None, time(17), "unspecified"),
    (None, None, "unknown"),
])
def test_invalid_explicit_hours_fail_closed(opening, closing, mode):
    with pytest.raises(ValueError):
        normalize_explicit_operating_mode(opening, closing, mode)


def test_unapproved_extended_hours_write_fails_before_database(app):
    app.config["CLINIC_EXTENDED_OPERATING_HOURS_ENABLED"] = False
    with pytest.raises(ValidationError, match="not enabled"):
        create_clinic(name="Blocked 24h Clinic", operating_mode="always_open")


def test_247_create_roundtrip_uses_existing_columns(app):
    app.config["CLINIC_EXTENDED_OPERATING_HOURS_ENABLED"] = True
    clinic = create_clinic(name="Clinic Hours 24h", operating_mode="always_open")
    assert (clinic.opening_time, clinic.closing_time) == (time(0), time(0))
    assert describe_operating_mode(clinic.opening_time, clinic.closing_time) == "always_open"


def test_overnight_create_and_update_roundtrip(app):
    app.config["CLINIC_EXTENDED_OPERATING_HOURS_ENABLED"] = True
    clinic = create_clinic(name="Clinic Hours Overnight", operating_mode="scheduled",
                           opening_time=time(20), closing_time=time(6))
    assert describe_operating_mode(clinic.opening_time, clinic.closing_time) == "scheduled"
    assert clinic.opening_time > clinic.closing_time
    updated = update_clinic(clinic.id, operating_mode="always_open")
    assert describe_operating_mode(updated.opening_time, updated.closing_time) == "always_open"
    updated = update_clinic(clinic.id, operating_mode="unspecified")
    assert (updated.opening_time, updated.closing_time) == (None, None)


def test_schema_modes_preserve_existing_api_and_reject_inconsistent_payload():
    from app.modules.clinic.schemas.clinic_schema import ClinicCreateSchema, ClinicBranchCreateSchema, ClinicUpdateSchema
    from pydantic import ValidationError as SchemaError
    assert ClinicCreateSchema(name="x").operating_mode is None
    assert ClinicCreateSchema(name="x", operating_mode="always_open").operating_mode == "always_open"
    assert ClinicBranchCreateSchema(name="x", operating_mode="scheduled",
                                    opening_time="22:00", closing_time="05:00").operating_mode == "scheduled"
    with pytest.raises(SchemaError):
        ClinicCreateSchema(name="x", operating_mode="scheduled", opening_time="08:00")
    with pytest.raises(SchemaError):
        ClinicUpdateSchema(operating_mode="always_open", opening_time="08:00")


def test_root_creation_remains_super_admin_only(app, client, make_user, auth_headers_for):
    app.config["CLINIC_EXTENDED_OPERATING_HOURS_ENABLED"] = True
    admin = make_user(role=Role.ADMIN)
    denied = client.post("/api/v1/clinics", headers=auth_headers_for(admin),
                         json={"name":"Unauthorized Hours Clinic", "operating_mode":"always_open"})
    assert denied.status_code == 403
    owner = make_user(role=Role.SUPER_ADMIN)
    allowed = client.post("/api/v1/clinics", headers=auth_headers_for(owner),
                          json={"name":"Authorized Hours Clinic", "operating_mode":"always_open"})
    assert allowed.status_code == 201, allowed.get_data(as_text=True)
    body = allowed.get_json()["data"]
    assert body["operating_mode"] == "always_open"
    assert body["opening_time"].startswith("00:00")


def test_existing_access_does_not_check_opening_hours(app, client, make_user, auth_headers_for):
    app.config["CLINIC_EXTENDED_OPERATING_HOURS_ENABLED"] = True
    owner = make_user(role=Role.SUPER_ADMIN)
    clinic = create_clinic(name="Hours Unrelated to Access", operating_mode="scheduled",
                           opening_time=time(8), closing_time=time(17))
    read = client.get(f"/api/v1/clinics/{clinic.id}", headers=auth_headers_for(owner))
    assert read.status_code == 200
    assert read.get_json()["data"]["operating_mode"] == "scheduled"


def test_branch_schedule_reuses_parent_clinic_existing_hierarchy(app):
    from app.modules.clinic.services.clinic_service import create_branch
    app.config["CLINIC_EXTENDED_OPERATING_HOURS_ENABLED"] = True
    parent = create_clinic(name="Hours Parent Facility")
    branch = create_branch(parent.id, name="Hours Overnight Branch",
                           operating_mode="scheduled", opening_time=time(19), closing_time=time(7))
    assert branch.parent_clinic_id == parent.id
    assert describe_operating_mode(branch.opening_time, branch.closing_time) == "scheduled"


def test_explicit_mode_disabled_even_for_regular_scheduled_hours(app):
    app.config["CLINIC_EXTENDED_OPERATING_HOURS_ENABLED"] = False
    with pytest.raises(ValidationError, match="not enabled"):
        create_clinic(name="Blocked Extended Mode", operating_mode="scheduled",
                      opening_time=time(8), closing_time=time(17))
    # Legacy path remains usable before deployment/migration approval.
    clinic = create_clinic(name="Old Contract Still Works", opening_time=time(8), closing_time=time(17))
    assert describe_operating_mode(clinic.opening_time, clinic.closing_time) == "scheduled"
