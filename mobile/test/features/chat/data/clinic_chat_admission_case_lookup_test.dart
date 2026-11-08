import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';

AuthSessionState _session({String token = 'case-token', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: token,
        refreshToken: 'case-refresh',
        userId: 42,
        role: 'nurse',
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> _conversation({
  int id = 51,
  int clinic = 3,
  int patient = 7,
  String type = 'patient',
  String status = 'active',
}) => {
  'id': id,
  'clinic_id': clinic,
  'conversation_type': type,
  'status': status,
  'title': 'Admission 11 clinical case',
  'description': null,
  'created_by_id': 8,
  'patient_id': patient,
  'last_message_at': null,
  'created_at': '2026-11-08T10:00:00Z',
};

Map<String, dynamic> _lookup({
  int admission = 11,
  int patient = 7,
  int clinic = 3,
  int conversationId = 51,
  Map<String, dynamic>? conversation,
}) => {
  'success': true,
  'data': {
    'clinic_id': clinic,
    'patient_id': patient,
    'admission_id': admission,
    'conversation_id': conversationId,
    'conversation': conversation ?? _conversation(id: conversationId),
  },
};

http.Response _json(Object data, [int status = 200]) =>
    http.Response(jsonEncode(data), status);

ClinicChatReadRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loadSession,
}) => ClinicChatReadRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: loadSession ?? () async => _session(),
  sessionNavigationGuard: guard,
);

void main() {
  test('authoritative case link and conversation detail are both authenticated GETs', () async {
    final paths = <String>[];
    final client = MockClient((req) async {
      expect(req.method, 'GET');
      expect(req.headers['authorization'], 'Bearer case-token');
      paths.add(req.url.path);
      if (req.url.path.endsWith('/admissions/11/conversation')) {
        return _json(_lookup());
      }
      if (req.url.path.endsWith('/conversations/51')) {
        return _json({'success': true, 'data': _conversation()});
      }
      throw StateError('Unexpected GET ${req.url.path}');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await _source(client, guard).getAdmissionCaseConversation(
      admissionId: 11,
      patientId: 7,
    );
    expect(result.id, 51);
    expect(result.patientId, 7);
    expect(paths, [
      '/api/v1/chat/admissions/11/conversation',
      '/api/v1/chat/conversations/51',
    ]);
  });

  test('wrong admission is rejected without conversation readback', () async {
    var requests = 0;
    final client = MockClient((req) async {
      requests++;
      return _json(_lookup(admission: 12));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).getAdmissionCaseConversation(admissionId: 11, patientId: 7),
      throwsFormatException,
    );
    expect(requests, 1);
  });

  test('wrong patient/clinic/case status fail closed', () async {
    for (final payload in [
      _lookup(patient: 8),
      _lookup(clinic: 4),
      _lookup(conversation: _conversation(type: 'group')),
      _lookup(conversation: _conversation(status: 'closed')),
      _lookup(conversation: _conversation(patient: 8)),
    ]) {
      final client = MockClient((req) async => _json(payload));
      final guard = SessionNavigationGuard()..markSessionAvailable();
      try {
        await expectLater(
          _source(client, guard).getAdmissionCaseConversation(admissionId: 11, patientId: 7),
          throwsFormatException,
        );
      } finally {
        client.close();
        guard.dispose();
      }
    }
  });

  test('detail readback mismatch or revoked access is never accepted', () async {
    for (final denied in [false, true]) {
      final client = MockClient((req) async {
        if (req.url.path.endsWith('/admissions/11/conversation')) {
          return _json(_lookup());
        }
        if (denied) {
          return _json({'success': false, 'error': 'Case unavailable'}, 404);
        }
        return _json({'success': true, 'data': _conversation(id: 52)});
      });
      final guard = SessionNavigationGuard()..markSessionAvailable();
      try {
        await expectLater(
          _source(client, guard).getAdmissionCaseConversation(admissionId: 11, patientId: 7),
          denied ? throwsA(isA<ApiException>()) : throwsFormatException,
        );
      } finally {
        client.close();
        guard.dispose();
      }
    }
  });

  test('session switch between authoritative reads denies result', () async {
    var current = 'case-token';
    final client = MockClient((req) async {
      if (req.url.path.endsWith('/admissions/11/conversation')) {
        return _json(_lookup());
      }
      current = 'rotated-case-token';
      return _json({'success': true, 'data': _conversation()});
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard, loadSession: () async => _session(token: current))
          .getAdmissionCaseConversation(admissionId: 11, patientId: 7),
      throwsA(isA<ClinicChatSessionUnavailable>()),
    );
  });

  test('signout during pending case lookup blocks stale result', () async {
    final pending = Completer<http.Response>();
    final client = MockClient((req) async => pending.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final lookup = _source(client, guard).getAdmissionCaseConversation(
      admissionId: 11, patientId: 7,
    );
    await Future<void>.delayed(Duration.zero);
    guard.markSignedOut();
    pending.complete(_json(_lookup()));
    await expectLater(lookup, throwsA(isA<ClinicChatSessionUnavailable>()));
  });

  test('invalid identifiers rejected without HTTP request', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      return _json(_lookup());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final data = _source(client, guard);
    await expectLater(
      data.getAdmissionCaseConversation(admissionId: 0, patientId: 7),
      throwsArgumentError,
    );
    await expectLater(
      data.getAdmissionCaseConversation(admissionId: 11, patientId: 0),
      throwsArgumentError,
    );
    expect(calls, 0);
  });
}
