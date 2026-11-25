"""GAP-A pure hierarchy/identity negatives; database integration gate separate."""
from uuid import uuid4
from pathlib import Path
import importlib.util
import pytest

# Avoid importing the Flask application just to test the backfill's pure resolver.
_source = Path(__file__).resolve().parents[3] / 'migrations' / 'versions' / 'a6b71c2d9e40_gap_a_hospital_tenant_ownership.py'
_spec = importlib.util.spec_from_file_location('gap_a_migration', _source)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_preserves_hq_and_branch_root():
    parents = {1: None, 2: 1, 3: 2, 4: None, 5: 4}
    assert [_mod._root_for(k, parents) for k in parents] == [1, 1, 1, 4, 4]


@pytest.mark.parametrize('parents,start', [({1: 2, 2: 1}, 1), ({1: 9}, 1), ({1: 1}, 1)])
def test_refuses_cycle_or_orphan(parents, start):
    with pytest.raises(RuntimeError):
        _mod._root_for(start, parents)


def test_rejects_wrong_tenant_user_and_clinic_context():
    from app.modules.clinic.services.tenant_boundary import TenantAccessDenied, require_tenant_clinic_access
    with pytest.raises(TenantAccessDenied):
        require_tenant_clinic_access(user_id=0, organization_id=uuid4(), clinic_id=1)
    with pytest.raises(TenantAccessDenied):
        require_tenant_clinic_access(user_id=1, organization_id='wrong', clinic_id=1)
    with pytest.raises(TenantAccessDenied):
        require_tenant_clinic_access(user_id=1, organization_id=uuid4(), clinic_id=True)
