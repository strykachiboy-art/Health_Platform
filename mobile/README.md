# Clinic System Pro — Mobile Architecture & Developer Guide

`clinic_system_pro` is the high-stakes clinical operations mobile client for the Clinic Management System, engineered with Flutter and Dart. It provides real-time clinical workflows, offline-first operation queuing, session security controls, and multi-tenant clinic context isolation.

---

## 🏛️ Architecture & Component Layering

The codebase follows a **Feature-First Clean Architecture** pattern designed for high modularity, testability, and strict separation of concerns.

```
mobile/lib/
├── core/                        # Shared Infrastructure & System Controls
│   ├── config/                  # Backend URL & environment configuration
│   ├── errors/                  # Core error definitions & functional failures
│   ├── logging/                 # Structured logging services
│   ├── networking/              # Dio / API client with interceptors & connectivity monitor
│   ├── routing/                 # GoRouter declaration & SessionNavigationGuard
│   ├── security/                # Secure storage (FlutterSecureStorage, Key Vault)
│   ├── session/                 # Session invalidation & authenticated session access
│   ├── storage/                 # Drift SQLite database & schema migration steps
│   └── sync/                    # Durable offline queue, worker, scheduler & crypto
├── features/                    # Modular Clinical & Operational Domains
│   ├── appointments/            # Appointment booking, mutations & queue read models
│   ├── auth/                    # OAuth, session coordination, context switching
│   ├── backend_health/          # Backend service health check cubits & screen
│   ├── clinical_orders/         # Orders (Diet/Nursing, Imaging, Blood Products)
│   ├── clinical_safety/         # Clinical alerts, rules, inbox & acknowledgements
│   ├── consultations/           # Consultation management & clinical note-taking
│   ├── dashboard/               # Operational metrics & dashboard snapshots
│   ├── emergency_access/        # "Break-Glass" emergency access request & review
│   ├── home/                    # Shell frame & authenticated home navigation
│   └── patients/                # Patient admissions, vitals, allergies & history
```

### Layer Responsibility Guidelines
* **Presentation**: Declarative UI built with Flutter Material 3, powered by `flutter_bloc` / `Cubit` for deterministic state transitions.
* **Data**: Remote data sources (`Dio`), local data sources (`Drift`), data transfer objects (DTOs serialized via `freezed` and `json_annotation`), and repository implementations.
* **Core Controls**: Cross-cutting infrastructure services injected via `GetIt` service locator.

---

## 🔄 Offline-First Sync & Replay Engine

The application is engineered to operate seamlessly during connectivity loss and network instability:

1. **Local Relational Storage**: Powered by `Drift` (`AppDatabase`) running SQLite with strict migration tests covering schema versions `v1`, `v2`, and `v3`.
2. **Durable Operation Queue**: `OperationQueueStore` persists pending offline mutations with idempotency key tracking to guarantee exact-once delivery.
3. **Background Worker & Replay**: `OperationReplayWorker` and `OperationReplayScheduler` automatically drain and process queued operations when network connectivity is restored.
4. **Payload Encryption**: Outgoing offline payload payloads are encrypted at rest using `OperationPayloadCrypto` before disk persistence.
5. **Failure & Dead-Letter Handling**: Hard errors trigger automatic discard cleanup policies (`OperationDiscardService`), preventing queue poisoning.

---

## 🛡️ Security, Auth & Session Safety

* **Session Navigation Guarding**: `SessionNavigationGuard` intercepts `GoRouter` route evaluations, ensuring unauthenticated or expired sessions cannot access protected clinical routes.
* **Session Invalidation**: `SessionInvalidationCoordinator` handles immediate local storage purge, secure key clearance, and state resets upon logout or HTTP 401/403 responses.
* **Clinic Context Isolation**: `ClinicContextSwitchCoordinator` enforces tenant isolation, ensuring patient data from one clinic context is never leaked into another.
* **Emergency Access ("Break-Glass")**: `EmergencyAccessContext` manages time-bounded emergency access overrides with audit logging and negative-containment enforcement.

---

## 🧪 Testing & Quality Discipline

The repository maintains an extensive multi-tier test suite (**190+ test files**) guaranteeing clinical correctness and security containment:

| Test Tier | Location | Scope |
| :--- | :--- | :--- |
| **Unit Tests** | `test/core/`, `test/features/*/data/` | Isolation testing of repositories, crypto handlers, sync workers, and cubits. |
| **Negative Security Matrices** | `test/features/*/security/` | Negative containment testing for unauthorized access, invalid states, and session invalidation. |
| **Router Integration** | `test/core/routing/`, `test/features/*/presentation/` | Deep-link guarding, route redirects, and shell state integrity. |
| **Database Migrations** | `test/drift/app_database/` | Migration verification from Drift schema `v1` $\rightarrow$ `v2` $\rightarrow$ `v3`. |
| **Integration Tests** | `integration_test/` | On-device integration tests for SQLite and encrypted key storage. |

---

## 🛠️ Tooling & Developer Workflows

### Prerequisites
* **Flutter SDK**: 3.27+ (Dart 3.13+)
* **Dependencies**: Managed via `pubspec.yaml`

### Key Commands

```bash
# Fetch dependencies
flutter pub get

# Run static code analysis
flutter analyze

# Code Generation (Freezed, JSON Serializable, Drift)
dart run build_runner build --delete-conflicting-outputs

# Execute test suite
flutter test

# Run integration tests
flutter test integration_test/
```

### Static Analysis Configuration
Static analysis rules are declared in `analysis_options.yaml` extending `package:flutter_lints/flutter.yaml`. Generated artifacts (`*.g.dart`, `*.freezed.dart`, `*.steps.dart`) are explicitly excluded from analysis to prevent false positives.
