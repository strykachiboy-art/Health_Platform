import pytest

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.modules.clinic.models.clinic_model import Clinic


@pytest.fixture(autouse=True)
def audit_fk_reference_rows(db_session, make_clinic):
    for clinic_id in (1, 2):
        clinic = db_session.get(Clinic, clinic_id)

        if clinic is None:
            # Preserve the historical fixed IDs, but use the existing
            # organization-owning clinic factory required by GAP-A/B.
            make_clinic(
                id=clinic_id,
                name=f"Audit Test Clinic {clinic_id}",
            )

    db_session.flush()

    for user_id in (1, 10, 20):
        user = db_session.get(User, user_id)

        if user is None:
            db_session.add(
                User(
                    id=user_id,
                    email=f"audit-user-{user_id}@test.com",
                    role=Role.ADMIN,
                    is_active=True,
                    clinic_id=1,
                )
            )

    db_session.flush()
