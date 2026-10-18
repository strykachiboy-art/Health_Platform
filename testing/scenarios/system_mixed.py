from __future__ import annotations

import json
import os

from locust import HttpUser, between, task

from common.auth import get_required_env, login
from common.benchmark import get_load_test_id


class SystemMixedUser(HttpUser):
    wait_time = between(
        float(os.getenv("LOCUST_WAIT_MIN", "2")),
        float(os.getenv("LOCUST_WAIT_MAX", "4")),
    )

    load_test_id = get_load_test_id()

    def on_start(self) -> None:
        ca_bundle = os.getenv("LOCUST_TLS_CA_BUNDLE")
        if ca_bundle:
            if not os.path.isfile(ca_bundle):
                raise RuntimeError(
                    "LOCUST_TLS_CA_BUNDLE must point to an existing certificate file"
                )
            self.client.trust_env = False
            self.client.verify = ca_bundle

        tokens_file = os.getenv("LOCUST_SYSTEM_MIXED_TOKENS_FILE")
        preauthenticated_tokens = None
        if tokens_file:
            if not os.path.isfile(tokens_file):
                raise RuntimeError("LOCUST_SYSTEM_MIXED_TOKENS_FILE must point to an existing JSON file")
            try:
                with open(tokens_file, "r", encoding="utf-8") as tokens_stream:
                    preauthenticated_tokens = json.load(tokens_stream)
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("Unable to load pre-authenticated Locust tokens") from exc
            if not isinstance(preauthenticated_tokens, dict):
                raise RuntimeError("Pre-authenticated Locust tokens must be a JSON object")
            for token_name in ("access_token", "clinical_token"):
                token_value = preauthenticated_tokens.get(token_name)
                if not isinstance(token_value, str) or not token_value.strip():
                    raise RuntimeError(f"Pre-authenticated token is missing or invalid: {token_name}")
            result = {"access_token": preauthenticated_tokens["access_token"]}
        else:
            result = login(
                self,
                self.load_test_id,
            )

        if not result:
            raise RuntimeError(
                "System mixed workload authentication failed"
            )

        self.access_token = str(
            result["access_token"]
        )

        self.access_control_token: str | None = preauthenticated_tokens.get("access_control_token") if preauthenticated_tokens else None
        self.clinical_token: str | None = preauthenticated_tokens["clinical_token"] if preauthenticated_tokens else None

        self.staff_id = self._positive_int_env(
            "LOCUST_STAFF_ID",
            20,
        )

        self.lab_patient_id = self._positive_int_env(
            "LOCUST_LAB_PATIENT_ID",
            505,
        )

        self.prescription_patient_id = (
            self._positive_int_env(
                "LOCUST_PRESCRIPTION_PATIENT_ID",
                505,
            )
        )

        self.ai_patient_id = self._positive_int_env(
            "LOCUST_PATIENT_ID",
            4,
        )

        self.include_ai = (
            os.getenv(
                "LOCUST_INCLUDE_AI",
                "false",
            )
            .strip()
            .lower()
            in {
                "1",
                "true",
                "yes",
                "on",
            }
        )

        raw_drugs = os.getenv(
            "LOCUST_DRUGS",
            "aspirin,ibuprofen",
        )

        self.drug_names = [
            drug.strip()
            for drug in raw_drugs.split(",")
            if drug.strip()
        ]

        if not self.drug_names:
            raise RuntimeError(
                "LOCUST_DRUGS must contain at least one drug name"
            )

    @staticmethod
    def _positive_int_env(
        name: str,
        default: int,
    ) -> int:
        raw = os.getenv(
            name,
            str(default),
        )

        try:
            value = int(raw)
        except ValueError as exc:
            raise RuntimeError(
                f"{name} must be an integer"
            ) from exc

        if value <= 0:
            raise RuntimeError(
                f"{name} must be greater than zero"
            )

        return value

    def _headers(
        self,
        token: str,
        *,
        json_body: bool = False,
    ) -> dict[str, str]:
        headers = {
            "Authorization": (
                f"Bearer {token}"
            ),
            "Accept": "application/json",
            "X-Load-Test-ID": self.load_test_id,
        }

        if json_body:
            headers["Content-Type"] = (
                "application/json"
            )

        return headers

    def _validate_json_body(
        self,
        response,
    ) -> dict[str, object] | None:
        try:
            body = response.json()
        except ValueError:
            response.failure(
                "HTTP 200 but response was not JSON"
            )
            return None

        if not isinstance(body, dict):
            response.failure(
                "Response body is not an object"
            )
            return None

        if "success" in body and body.get(
            "success"
        ) is not True:
            response.failure(
                "Response success != true"
            )
            return None

        return body

    def _get_json(
        self,
        url: str,
        name: str,
        *,
        token: str | None = None,
    ) -> bool:
        access_token = (
            token
            or self.access_token
        )

        with self.client.get(
            url,
            headers=self._headers(
                access_token
            ),
            name=name,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"Unexpected HTTP "
                    f"{response.status_code}: "
                    f"{response.text[:200]}"
                )
                return False

            body = self._validate_json_body(
                response
            )

            if body is None:
                return False

            if "data" not in body:
                response.failure(
                    "HTTP 200 but response has no data"
                )
                return False

            response.success()
            return True

    def _get_clinics(self) -> bool:
        with self.client.get(
            "/api/v1/clinics",
            headers=self._headers(
                self.access_token
            ),
            name="GET /api/v1/clinics",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"Unexpected HTTP "
                    f"{response.status_code}: "
                    f"{response.text[:200]}"
                )
                return False

            body = self._validate_json_body(
                response
            )

            if body is None:
                return False

            data = body.get("data")

            if not isinstance(data, list):
                response.failure(
                    "Clinic list data is missing or invalid"
                )
                return False

            for clinic in data:
                if not isinstance(clinic, dict):
                    response.failure(
                        "Clinic item is not an object"
                    )
                    return False

                for field in (
                    "id",
                    "name",
                    "status",
                ):
                    if field not in clinic:
                        response.failure(
                            f"Clinic item is missing {field}"
                        )
                        return False

            response.success()
            return True

    def _get_staff(self) -> bool:
        with self.client.get(
            "/api/v1/staff?page=1&per_page=50",
            headers=self._headers(
                self.access_token
            ),
            name=(
                "GET /api/v1/staff "
                "[page=1,per_page=50]"
            ),
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"Unexpected HTTP "
                    f"{response.status_code}: "
                    f"{response.text[:200]}"
                )
                return False

            body = self._validate_json_body(
                response
            )

            if body is None:
                return False

            data = body.get("data")

            if not isinstance(data, dict):
                response.failure(
                    "Staff list data is missing or invalid"
                )
                return False

            items = data.get("items")
            total = data.get("total")
            page = data.get("page")
            per_page = data.get("per_page")

            if not isinstance(items, list):
                response.failure(
                    "Staff items is not a list"
                )
                return False

            if not isinstance(total, int):
                response.failure(
                    "Staff total is not an integer"
                )
                return False

            if page != 1:
                response.failure(
                    f"Unexpected page value: {page}"
                )
                return False

            if per_page != 50:
                response.failure(
                    f"Unexpected per_page value: {per_page}"
                )
                return False

            if len(items) > 50:
                response.failure(
                    "Returned more than 50 staff records"
                )
                return False

            for item in items:
                if not isinstance(item, dict):
                    response.failure(
                        "Staff item is not an object"
                    )
                    return False

                for field in (
                    "id",
                    "clinic_id",
                    "user_id",
                    "first_name",
                    "last_name",
                    "status",
                ):
                    if field not in item:
                        response.failure(
                            f"Staff item missing {field}"
                        )
                        return False

            response.success()
            return True

    def _get_assets(self) -> bool:
        with self.client.get(
            "/api/v1/assets?page=1&per_page=50",
            headers=self._headers(
                self.access_token
            ),
            name=(
                "GET /api/v1/assets "
                "[page=1,per_page=50]"
            ),
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"Unexpected HTTP "
                    f"{response.status_code}: "
                    f"{response.text[:200]}"
                )
                return False

            body = self._validate_json_body(
                response
            )

            if body is None:
                return False

            items = body.get("items")

            if not isinstance(items, list):
                response.failure(
                    "Asset items is missing or invalid"
                )
                return False

            if body.get("page") != 1:
                response.failure(
                    "Unexpected page value"
                )
                return False

            if body.get("per_page") != 50:
                response.failure(
                    "Unexpected per_page value"
                )
                return False

            total = body.get("total")

            if not isinstance(total, int):
                response.failure(
                    "Asset total must be an integer"
                )
                return False

            pages = body.get("pages")

            if not isinstance(pages, int):
                response.failure(
                    "Asset pages must be an integer"
                )
                return False

            if not isinstance(
                body.get("has_next"),
                bool,
            ):
                response.failure(
                    "Asset has_next must be boolean"
                )
                return False

            if not isinstance(
                body.get("has_prev"),
                bool,
            ):
                response.failure(
                    "Asset has_prev must be boolean"
                )
                return False

            if len(items) > 50:
                response.failure(
                    "Returned more than 50 assets"
                )
                return False

            for item in items:
                if not isinstance(item, dict):
                    response.failure(
                        "Asset item must be an object"
                    )
                    return False

                for field in (
                    "id",
                    "clinic_id",
                    "asset_tag",
                    "name",
                    "category",
                    "status",
                    "condition",
                    "ownership",
                    "maintenance_status",
                    "is_active",
                ):
                    if field not in item:
                        response.failure(
                            f"Asset item missing {field}"
                        )
                        return False

            response.success()
            return True

    def _get_wards(self) -> bool:
        with self.client.get(
            "/api/v1/wards?page=1&per_page=50",
            headers=self._headers(
                self.access_token
            ),
            name=(
                "GET /api/v1/wards "
                "[page=1,per_page=50]"
            ),
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"Unexpected HTTP "
                    f"{response.status_code}: "
                    f"{response.text[:200]}"
                )
                return False

            body = self._validate_json_body(
                response
            )

            if body is None:
                return False

            items = body.get("items")
            total = body.get("total")
            page = body.get("page")
            per_page = body.get("per_page")

            if not isinstance(items, list):
                response.failure(
                    "Ward items is not a list"
                )
                return False

            if not isinstance(total, int):
                response.failure(
                    "Ward total is not an integer"
                )
                return False

            if page != 1:
                response.failure(
                    f"Unexpected page value: {page}"
                )
                return False

            if per_page != 50:
                response.failure(
                    f"Unexpected per_page value: {per_page}"
                )
                return False

            if len(items) > 50:
                response.failure(
                    f"Returned more than 50 wards: {len(items)}"
                )
                return False

            for item in items:
                if not isinstance(item, dict):
                    response.failure(
                        "Ward item is not an object"
                    )
                    return False

                if not isinstance(
                    item.get("id"),
                    int,
                ):
                    response.failure(
                        "Ward id is not an integer"
                    )
                    return False

                if not isinstance(
                    item.get("clinic_id"),
                    int,
                ):
                    response.failure(
                        "Ward clinic_id is not an integer"
                    )
                    return False

                if not isinstance(
                    item.get("name"),
                    str,
                ):
                    response.failure(
                        "Ward name is not a string"
                    )
                    return False

                if not isinstance(
                    item.get("capacity"),
                    int,
                ):
                    response.failure(
                        "Ward capacity is not an integer"
                    )
                    return False

            response.success()
            return True

    def _login_with_credentials(
        self,
        email: str,
        password: str,
        name: str,
    ) -> str | None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Load-Test-ID": self.load_test_id,
        }

        with self.client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": password,
            },
            headers=headers,
            name=name,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"Unexpected HTTP "
                    f"{response.status_code}: "
                    f"{response.text[:200]}"
                )
                return None

            body = self._validate_json_body(
                response
            )

            if body is None:
                return None

            data = body.get("data")

            if not isinstance(data, dict):
                response.failure(
                    "Login response data is missing or invalid"
                )
                return None

            access_token = data.get(
                "access_token"
            )

            if not access_token:
                response.failure(
                    "Login response has no access_token"
                )
                return None

            response.success()

            return str(access_token)

    def _get_access_control_token(
        self,
    ) -> str | None:
        include_access_control = (
            os.getenv(
                "LOCUST_SYSTEM_MIXED_INCLUDE_ACCESS_CONTROL",
                "false",
            )
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
        if not include_access_control:
            return None

        if self.access_control_token is not None:
            return self.access_control_token

        email = get_required_env(
            "LOCUST_ACCESS_CONTROL_EMAIL"
        )

        password = get_required_env(
            "LOCUST_ACCESS_CONTROL_PASSWORD"
        )

        token = self._login_with_credentials(
            email,
            password,
            "POST /api/v1/auth/login [setup]",
        )

        if token is None:
            return None

        self.access_control_token = token

        return token

    def _get_clinical_token(
        self,
    ) -> str | None:
        if self.clinical_token is not None:
            return self.clinical_token

        email = os.getenv(
            "LOCUST_CLINICAL_EMAIL",
            "benchmark-staff-0001@example.com",
        )

        password = os.getenv(
            "LOCUST_CLINICAL_PASSWORD",
            "BenchmarkPassword0001!",
        )

        token = self._login_with_credentials(
            email,
            password,
            "POST /api/v1/auth/login [setup]",
        )

        if token is None:
            return None

        self.clinical_token = token

        return token

    @task(4)
    def clinical_workflow(self) -> None:
        self._get_json(
            "/api/v1/profile/me",
            "GET /api/v1/profile/me",
        )

        self._get_json(
            "/api/v1/patients?page=1&per_page=50",
            "GET /api/v1/patients [page=1,per_page=50]",
        )

        self._get_json(
            (
                "/api/v1/appointments/staff/"
                f"{self.staff_id}"
                "?page=1&per_page=50"
            ),
            (
                "GET /api/v1/appointments/staff/{staff_id} "
                "[page=1,per_page=50]"
            ),
        )

        self._get_json(
            (
                "/api/v1/consultations/staff/"
                f"{self.staff_id}"
                "?page=1&per_page=50"
            ),
            (
                "GET /api/v1/consultations/staff/{staff_id} "
                "[page=1,per_page=50]"
            ),
        )

        self._get_json(
            (
                "/api/v1/lab/orders"
                f"?patient_id={self.lab_patient_id}"
                "&page=1"
                "&per_page=50"
            ),
            (
                "GET /api/v1/lab/orders "
                "[patient,page=1,per_page=50]"
            ),
        )

        clinical_token = (
            self._get_clinical_token()
        )

        if clinical_token is not None:
            self._get_json(
                (
                    "/api/v1/prescriptions/patients/"
                    f"{self.prescription_patient_id}"
                    "?page=1&per_page=50"
                ),
                (
                    "GET /api/v1/prescriptions/patients/"
                    "{patient_id} [page=1,per_page=50]"
                ),
                token=clinical_token,
            )

        self._get_json(
            "/api/v1/pharmacy/drugs?page=1&per_page=50",
            (
                "GET /api/v1/pharmacy/drugs "
                "[page=1,per_page=50]"
            ),
        )

        self._get_json(
            "/api/v1/notifications/?page=1&per_page=50",
            (
                "GET /api/v1/notifications/ "
                "[page=1,per_page=50]"
            ),
        )

        self._get_json(
            "/api/v1/chat/conversations?page=1&per_page=50",
            (
                "GET /api/v1/chat/conversations "
                "[page=1,per_page=50]"
            ),
        )

    @task(2)
    def medication_inventory_workflow(self) -> None:
        self._get_json(
            "/api/v1/pharmacy/drugs?page=1&per_page=50",
            (
                "GET /api/v1/pharmacy/drugs "
                "[page=1,per_page=50]"
            ),
        )

        self._get_json(
            "/api/v1/inventory/items?page=1&per_page=50",
            (
                "GET /api/v1/inventory/items "
                "[page=1,per_page=50]"
            ),
        )

        clinical_token = (
            self._get_clinical_token()
        )

        if clinical_token is not None:
            self._get_json(
                (
                    "/api/v1/prescriptions/patients/"
                    f"{self.prescription_patient_id}"
                    "?page=1&per_page=50"
                ),
                (
                    "GET /api/v1/prescriptions/patients/"
                    "{patient_id} [page=1,per_page=50]"
                ),
                token=clinical_token,
            )

        self._get_json(
            "/api/v1/notifications/?page=1&per_page=50",
            (
                "GET /api/v1/notifications/ "
                "[page=1,per_page=50]"
            ),
        )

    @task(2)
    def front_desk_workflow(self) -> None:
        self._get_clinics()

        self._get_staff()

        self._get_json(
            (
                "/api/v1/appointments/staff/"
                f"{self.staff_id}"
                "?page=1&per_page=50"
            ),
            (
                "GET /api/v1/appointments/staff/{staff_id} "
                "[page=1,per_page=50]"
            ),
        )

        self._get_json(
            (
                "/api/v1/billing/invoices/outstanding"
                "?page=1&per_page=50"
            ),
            (
                "GET /api/v1/billing/invoices/outstanding "
                "[page=1,per_page=50]"
            ),
        )

        self._get_json(
            "/api/v1/users/devices/?page=1&per_page=20",
            (
                "GET /api/v1/users/devices/ "
                "[page=1,per_page=20]"
            ),
        )

        self._get_json(
            "/api/v1/notifications/?page=1&per_page=50",
            (
                "GET /api/v1/notifications/ "
                "[page=1,per_page=50]"
            ),
        )

    @task(1)
    def admin_operations_workflow(self) -> None:
        date_from = os.getenv(
            "LOCUST_DASHBOARD_DATE_FROM",
            "2026-01-01",
        )

        date_to = os.getenv(
            "LOCUST_DASHBOARD_DATE_TO",
            "2026-12-31",
        )

        self._get_json(
            (
                "/api/v1/dashboard"
                f"?date_from={date_from}"
                f"&date_to={date_to}"
            ),
            (
                "GET /api/v1/dashboard "
                "[2026-01-01..2026-12-31]"
            ),
        )

        self._get_json(
            "/api/v1/settings/clinic",
            "GET /api/v1/settings/clinic",
        )

        self._get_assets()

        access_control_token = (
            self._get_access_control_token()
        )

        if access_control_token is not None:
            self._get_json(
                (
                    "/api/v1/access-control/users"
                    "?page=1&per_page=50"
                ),
                (
                    "GET /api/v1/access-control/users "
                    "[page=1,per_page=50]"
                ),
                token=access_control_token,
            )

        self._get_json(
            "/api/v1/audit-logs?page=1&per_page=20",
            "GET /api/v1/audit-logs?page=1&per_page=20",
        )

        clinical_token = (
            self._get_clinical_token()
        )

        if clinical_token is not None:
            self._get_json(
                "/api/v1/reports?page=1&per_page=20",
                (
                    "GET /api/v1/reports "
                    "[page=1,per_page=20]"
                ),
                token=clinical_token,
            )

        self._get_json(
            "/api/v1/ambulance/trips?page=1&per_page=50",
            (
                "GET /api/v1/ambulance/trips "
                "[page=1,per_page=50]"
            ),
        )

        self._get_wards()

        self._get_json(
            "/api/v1/hie/submissions?page=1&per_page=20",
            (
                "GET /api/v1/hie/submissions "
                "[page=1,per_page=20]"
            ),
        )

    @task(1)
    def communication_workflow(self) -> None:
        self._get_json(
            "/api/v1/chat/conversations?page=1&per_page=50",
            (
                "GET /api/v1/chat/conversations "
                "[page=1,per_page=50]"
            ),
        )

        self._get_json(
            "/api/v1/notifications/?page=1&per_page=50",
            (
                "GET /api/v1/notifications/ "
                "[page=1,per_page=50]"
            ),
        )

        self._get_json(
            "/api/v1/profile/me",
            "GET /api/v1/profile/me",
        )

        if self.include_ai:
            self._post_ai_drug_interaction()

    def _post_ai_drug_interaction(self) -> bool:
        payload = {
            "drug_names": self.drug_names,
            "patient_id": self.ai_patient_id,
        }

        with self.client.post(
            "/api/v1/ai/drug-interactions",
            json=payload,
            headers=self._headers(
                self.access_token,
                json_body=True,
            ),
            name=(
                "POST /api/v1/ai/drug-interactions"
            ),
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"Unexpected HTTP "
                    f"{response.status_code}: "
                    f"{response.text[:200]}"
                )
                return False

            body = self._validate_json_body(
                response
            )

            if body is None:
                return False

            if "data" not in body:
                response.failure(
                    "HTTP 200 but response has no data"
                )
                return False

            response.success()
            return True