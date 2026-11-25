"""GAP-A: source-contract negative tests. Real PostgreSQL evidence is separate."""
from pathlib import Path
from uuid import uuid4
import pytest

from app.modules.clinic.services.tenant_boundary import (
    TenantAccessDenied,
    _parse_organization_id,
    require_tenant_clinic_access,
    list_authorized_clinics_for_user,
)


def test_invalid_identity_never_queries_database():
    for kwargs in (
        dict(user_id=0, clinic_id=1, organization_id=uuid4()),
        dict(user_id=1, clinic_id=True, organization_id=uuid4()),
        dict(user_id=1, clinic_id=1, organization_id='not-a-uuid'),
    ):
        with pytest.raises(TenantAccessDenied):
            require_tenant_clinic_access(**kwargs)


def test_untrusted_organization_identifier_rejected():
    with pytest.raises(TenantAccessDenied):
        _parse_organization_id(None)
    with pytest.raises(TenantAccessDenied):
        _parse_organization_id(True)


def test_bad_user_cannot_list_all_clinics():
    with pytest.raises(TenantAccessDenied):
        list_authorized_clinics_for_user(user_id=0)


def test_route_contract_contains_no_admin_bypass():
    path = Path(__file__).resolve().parents[2] / 'modules/clinic/routes/clinic_route.py'
    code = path.read_text(encoding='utf-8')
    assert 'require_tenant_clinic_access(' in code
    assert 'list_authorized_clinics_for_user(' in code
    assert 'if _is_admin():\n        return None' not in code
    # The old blanket provisioning hold is superseded by platform-only root creation.
    # Runtime positive/negative tests also verify server-owned organization and zero grants.
    assert '@clinic_bp.post("")\n@role_required(Role.SUPER_ADMIN)\ndef create_clinic_route' in code
    assert 'platform_provisioner_id=_get_current_user().id' in code


def test_clinic_service_explicit_organization_ownership_and_parent_guard():
    path = Path(__file__).resolve().parents[2] / 'modules/clinic/services/clinic_service.py'
    code = path.read_text(encoding='utf-8')
    assert 'organization_id=parent.organization_id' in code
    assert 'organization_id=owner_id' in code
    assert 'GAPA_CROSS_ORGANIZATION_REPARENT_DENIED' in code
