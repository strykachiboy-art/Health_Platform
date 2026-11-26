import 'dart:convert';
import 'dart:math';

import '../../features/auth/data/models/auth_token_response.dart';
import 'operation_lifecycle.dart';
import 'operation_payload_crypto.dart';
import 'tenant_operation_scope.dart';
import 'run2_module_catalog.dart';

/// The local catalogue is not a grant of clinical or administrative access.
/// No free text, clinical identifiers, medical records or credentials are
/// accepted by these nonclinical storage contracts.
class Run2Policy {
  const Run2Policy._();

  static const feedback = 'feedback.product.preset';
  static const chat = 'chat.message.preset';
  static const draft = 'local.workflow.reminder';
  static const snapshot = 'local.backend.health';
  static const replayTypes = <String>{feedback, chat};
  static const localTypes = <String>{draft, snapshot};
  static const leaseAge = Duration(minutes: 15);
  static const feedbackAge = Duration(hours: 24);
  static const chatAge = Duration(minutes: 30);
  static const localAge = Duration(days: 1);
  static const roles = <String>{
    'admin',
    'doctor',
    'nurse',
    'patient',
    'pharmacist',
    'lab_technician',
    'receptionist',
    'accountant',
    'paramedic',
    'emt',
    'driver',
    'ambulance_dispatcher',
    'ambulance_coordinator',
    'other',
  };
  static const feedbackPresets = <String, List<String>>{
    'navigation': [
      'product_feedback',
      'usability',
      'Navigation feedback',
      'Please improve navigation between screens.',
    ],
    'performance': [
      'product_feedback',
      'performance',
      'Performance feedback',
      'Please improve application responsiveness.',
    ],
    'accessibility': [
      'feature_request',
      'accessibility',
      'Accessibility feedback',
      'Please improve accessibility of the application controls.',
    ],
  };
  static const chatPresets = <String, String>{
    'greeting': 'Hello.',
    'thanks': 'Thank you.',
  };
  static const intents = <String>{
    'Review online',
    'Prepare for online review',
    'Follow up online',
  };
  static const sharedDomains = <String>{
    'home',
    'backend_health',
    'status',
    'profile',
    'devices',
    'notifications',
    'feedback',
    'pending_work',
    'auth',
    'dashboard',
    'core_or_app',
  };
  static const roleDomains = <String, Set<String>>{
    'admin': {
      'administration',
      'appointments',
      'patients',
      'billing',
      'blood_bank',
      'pharmacy',
      'lab',
      'radiology',
      'surgery',
      'coordination',
      'ambulance',
      'chat',
      'printing',
      'hie',
      'unified_search',
      'p23h_navigation',
      'p23i_navigation',
    },
    'doctor': {
      'appointments',
      'patients',
      'consultations',
      'clinical_orders',
      'clinical_timeline',
      'problems',
      'blood_bank',
      'nursing',
      'emar',
      'medication_reconciliation',
      'lab',
      'radiology',
      'surgery',
      'chat',
      'clinical_ai',
      'clinical_safety',
      'emergency_access',
      'printing',
      'unified_search',
      'p23h_navigation',
    },
    'nurse': {
      'appointments',
      'patients',
      'nursing',
      'clinical_orders',
      'clinical_timeline',
      'blood_bank',
      'emar',
      'medication_reconciliation',
      'problems',
      'chat',
      'clinical_safety',
      'emergency_access',
      'coordination',
      'unified_search',
    },
    'patient': {'patient_portal', 'appointments', 'billing'},
    'pharmacist': {
      'pharmacy',
      'patients',
      'clinical_orders',
      'medication_reconciliation',
      'clinical_timeline',
      'chat',
      'p23h_navigation',
    },
    'lab_technician': {
      'lab',
      'patients',
      'blood_bank',
      'clinical_orders',
      'chat',
    },
    'receptionist': {'appointments', 'patients', 'coordination', 'chat'},
    'accountant': {'billing', 'administration', 'p23i_navigation'},
    'paramedic': {
      'ambulance',
      'coordination',
      'patients',
      'clinical_safety',
      'chat',
      'p23i_navigation',
    },
    'emt': {
      'ambulance',
      'coordination',
      'patients',
      'clinical_safety',
      'chat',
      'p23i_navigation',
    },
    'driver': {'ambulance', 'coordination', 'p23i_navigation'},
    'ambulance_dispatcher': {
      'ambulance',
      'coordination',
      'chat',
      'p23i_navigation',
    },
    'ambulance_coordinator': {
      'ambulance',
      'coordination',
      'chat',
      'p23i_navigation',
    },
    'other': <String>{},
  };
  static const chatRoles = <String>{
    'admin',
    'doctor',
    'nurse',
    'pharmacist',
    'lab_technician',
    'receptionist',
    'paramedic',
    'emt',
    'ambulance_dispatcher',
    'ambulance_coordinator',
  };

  static Set<String> domains(String role) => roles.contains(role)
      ? <String>{...sharedDomains, ...?roleDomains[role]}
      : <String>{};

  static String role(AuthTokenResponse session) =>
      session.role.trim().toLowerCase();

  static OperationPayloadContext? context(AuthTokenResponse? session) {
    final base = TenantOperationScope.fromSession(session);
    if (session == null || base == null || !roles.contains(role(session))) {
      return null;
    }
    return OperationPayloadContext(
      userId: base.userId,
      clinicContextId: '${base.clinicContextId}:role:${role(session)}',
    );
  }

  /// A locally decoded expiry is a restriction, not a verification of JWT
  /// authority. Online server verification is independently required.
  static DateTime? tokenExpiry(AuthTokenResponse session) {
    try {
      final parts = session.accessToken.split('.');
      if (parts.length != 3) {
        return null;
      }
      final raw = jsonDecode(
        utf8.decode(base64Url.decode(base64Url.normalize(parts[1]))),
      );
      if (raw is! Map<String, dynamic> || raw['exp'] is! int) {
        return null;
      }
      return DateTime.fromMillisecondsSinceEpoch(
        (raw['exp'] as int) * 1000,
        isUtc: true,
      );
    } on Object {
      return null;
    }
  }

  static bool sameSession(AuthTokenResponse a, AuthTokenResponse? b) =>
      b != null &&
      a.userId == b.userId &&
      a.tenantId == b.tenantId &&
      a.clinicContextId == b.clinicContextId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  static OperationIdentity identity(String type) {
    if (!replayTypes.contains(type) &&
        !localTypes.contains(type) &&
        !Run2ModuleCatalog.localTypes.contains(type)) {
      throw ArgumentError('Unknown local operation.');
    }
    final random = Random.secure();
    String token() => List<int>.generate(
      24,
      (_) => random.nextInt(256),
    ).map((n) => n.toRadixString(16).padLeft(2, '0')).join();
    return OperationIdentity(
      operationId: 'run2_${token()}',
      operationType: type,
      idempotencyKey: 'run2_${token()}',
    );
  }

  static Map<String, dynamic> feedbackBody(String preset) {
    final item = feedbackPresets[preset];
    if (item == null) {
      throw const FormatException('Invalid feedback preset.');
    }
    return {
      'feedback_type': item[0],
      'category': item[1],
      'subject': item[2],
      'message': item[3],
    };
  }

  static Map<String, dynamic> chatBody(String preset) {
    final content = chatPresets[preset];
    if (content == null) {
      throw const FormatException('Invalid message preset.');
    }
    return {'message_type': 'text', 'priority': 'normal', 'content': content};
  }

  static DateTime captured(Map<String, dynamic> payload) {
    final value = payload['captured_at'];
    final parsed = value is String ? DateTime.tryParse(value) : null;
    if (parsed == null || !parsed.isUtc || parsed.toIso8601String() != value) {
      throw const FormatException('Invalid local capture timestamp.');
    }
    return parsed;
  }

  static Duration retention(String type) => switch (type) {
    feedback => feedbackAge,
    chat => chatAge,
    draft || snapshot => localAge,
    _ => Duration.zero,
  };

  static bool fresh(String type, Map<String, dynamic> payload, DateTime now) {
    final age = now.toUtc().difference(captured(payload));
    return !age.isNegative && age < retention(type);
  }

  static void validate(String type, Map<String, dynamic> p, String actorRole) {
    if (!roles.contains(actorRole) || p['role'] != actorRole) {
      throw const FormatException('Local role mismatch.');
    }
    captured(p);
    final fields = switch (type) {
      feedback => {'role', 'captured_at', 'preset'},
      chat => {'role', 'captured_at', 'preset', 'conversation_id'},
      draft => {'role', 'captured_at', 'domain', 'intent'},
      snapshot => {'role', 'captured_at', 'healthy'},
      _ => <String>{},
    };
    if (fields.isEmpty ||
        p.length != fields.length ||
        !p.keys.every(fields.contains)) {
      throw const FormatException('Unapproved local payload.');
    }
    switch (type) {
      case feedback:
        if (!feedbackPresets.containsKey(p['preset'])) {
          throw const FormatException('Unknown feedback preset.');
        }
      case chat:
        final id = p['conversation_id'];
        if (!chatRoles.contains(actorRole) ||
            !chatPresets.containsKey(p['preset']) ||
            id is! int ||
            id <= 0 ||
            id > 2147483647) {
          throw const FormatException('Unknown ordinary message intent.');
        }
      case draft:
        if (!domains(actorRole).contains(p['domain']) ||
            !intents.contains(p['intent'])) {
          throw const FormatException('Unknown workflow reminder.');
        }
      case snapshot:
        if (p['healthy'] is! bool) {
          throw const FormatException('Invalid liveness observation.');
        }
    }
  }
}
