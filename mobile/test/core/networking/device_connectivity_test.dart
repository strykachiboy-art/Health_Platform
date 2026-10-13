import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';

import 'package:clinic_system_pro/core/networking/device_connectivity_banner.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_cubit.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';

const offlineMessage =
    'No device network connection detected. '
    'Online requests may fail; changes are not saved offline.';

class FakeDeviceConnectivitySource implements DeviceConnectivitySource {
  FakeDeviceConnectivitySource({
    this.initialStatus = DeviceNetworkStatus.connected,
  });

  final DeviceNetworkStatus initialStatus;
  final StreamController<DeviceNetworkStatus> _changes =
      StreamController<DeviceNetworkStatus>.broadcast(sync: true);

  Completer<DeviceNetworkStatus>? pendingCheck;
  Object? checkError;
  int checkCount = 0;

  @override
  Future<DeviceNetworkStatus> checkStatus() async {
    checkCount++;

    final error = checkError;
    if (error != null) throw error;

    final pending = pendingCheck;
    if (pending != null) return pending.future;

    return initialStatus;
  }

  @override
  Stream<DeviceNetworkStatus> get statusChanges => _changes.stream;

  void emit(DeviceNetworkStatus status) => _changes.add(status);

  Future<void> dispose() => _changes.close();
}

ApiClient createApiClient(http.Client client) {
  return ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
}

const apiCommunicationMessage =
    'The latest API request did not receive an HTTP response. '
    'Use the affected screen to verify the result before retrying.';

void main() {
  group('Connectivity result classification', () {
    test('classifies no active network as disconnected', () {
      expect(
        classifyConnectivityResults([ConnectivityResult.none]),
        DeviceNetworkStatus.disconnected,
      );
    });

    test('classifies available network types as connected', () {
      expect(
        classifyConnectivityResults([ConnectivityResult.wifi]),
        DeviceNetworkStatus.connected,
      );

      expect(
        classifyConnectivityResults([
          ConnectivityResult.wifi,
          ConnectivityResult.mobile,
        ]),
        DeviceNetworkStatus.connected,
      );
    });

    test('classifies an empty result as unknown', () {
      expect(
        classifyConnectivityResults(const <ConnectivityResult>[]),
        DeviceNetworkStatus.unknown,
      );
    });
  });

  group('DeviceConnectivityCubit', () {
    test('checks current connectivity on startup', () async {
      final source = FakeDeviceConnectivitySource(
        initialStatus: DeviceNetworkStatus.connected,
      );
      final cubit = DeviceConnectivityCubit(source: source);

      addTearDown(() async {
        await cubit.close();
        await source.dispose();
      });

      await cubit.start();

      expect(cubit.state, DeviceConnectivityViewState.connected);
      expect(source.checkCount, 1);
    });

    test('follows disconnected and reconnected stream events', () async {
      final source = FakeDeviceConnectivitySource();
      final cubit = DeviceConnectivityCubit(source: source);

      addTearDown(() async {
        await cubit.close();
        await source.dispose();
      });

      await cubit.start();

      source.emit(DeviceNetworkStatus.disconnected);
      expect(cubit.state, DeviceConnectivityViewState.disconnected);

      source.emit(DeviceNetworkStatus.connected);
      expect(cubit.state, DeviceConnectivityViewState.connected);
    });

    test('uses unknown when the initial connectivity check fails', () async {
      final source = FakeDeviceConnectivitySource()
        ..checkError = StateError('test failure');
      final cubit = DeviceConnectivityCubit(source: source);

      addTearDown(() async {
        await cubit.close();
        await source.dispose();
      });

      await cubit.start();

      expect(cubit.state, DeviceConnectivityViewState.unknown);
    });

    test(
      'does not let an older check overwrite a newer stream event',
      () async {
        final source = FakeDeviceConnectivitySource();
        final pending = Completer<DeviceNetworkStatus>();
        source.pendingCheck = pending;

        final cubit = DeviceConnectivityCubit(source: source);

        addTearDown(() async {
          await cubit.close();
          await source.dispose();
        });

        final startup = cubit.start();

        source.emit(DeviceNetworkStatus.disconnected);
        pending.complete(DeviceNetworkStatus.connected);
        await startup;

        expect(cubit.state, DeviceConnectivityViewState.disconnected);
      },
    );
  });

  group('DeviceConnectivityBanner', () {
    testWidgets('shows offline notice and hides it after reconnection', (
      tester,
    ) async {
      final source = FakeDeviceConnectivitySource(
        initialStatus: DeviceNetworkStatus.disconnected,
      );
      final apiMock = MockClient(
        (request) async => http.Response('{"success":true}', 200),
      );
      final apiClient = createApiClient(apiMock);
      addTearDown(apiClient.close);
      addTearDown(apiMock.close);
      final cubit = DeviceConnectivityCubit(source: source);

      addTearDown(() async {
        await cubit.close();
        await source.dispose();
      });

      await cubit.start();

      await tester.pumpWidget(
        MaterialApp(
          builder: (context, child) => BlocProvider.value(
            value: cubit,
            child: DeviceConnectivityBanner(
              apiClient: apiClient,
              child: child ?? const SizedBox.shrink(),
            ),
          ),
          home: const Scaffold(body: Text('Application content')),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text(offlineMessage), findsOneWidget);
      expect(find.text('Application content'), findsOneWidget);

      source.emit(DeviceNetworkStatus.connected);
      await tester.pumpAndSettle();

      expect(find.text(offlineMessage), findsNothing);
      expect(find.text('Application content'), findsOneWidget);
    });

    testWidgets(
      'shows a communication warning after a request fails to receive a response',
      (tester) async {
        var shouldFail = true;

        final mock = MockClient((request) async {
          if (shouldFail) {
            throw http.ClientException(
              'Synthetic connection failure',
              request.url,
            );
          }

          // An HTTP error still proves that an HTTP response arrived.
          return http.Response('{"success":false}', 503);
        });
        final apiClient = createApiClient(mock);
        final source = FakeDeviceConnectivitySource();
        final cubit = DeviceConnectivityCubit(source: source);

        addTearDown(() async {
          await cubit.close();
          await source.dispose();
          apiClient.close();
          mock.close();
        });

        await cubit.start();

        await tester.pumpWidget(
          MaterialApp(
            builder: (context, child) => BlocProvider.value(
              value: cubit,
              child: DeviceConnectivityBanner(
                apiClient: apiClient,
                child: child ?? const SizedBox.shrink(),
              ),
            ),
            home: const Scaffold(body: Text('Application content')),
          ),
        );
        await tester.pumpAndSettle();

        await expectLater(
          apiClient.getJson('health/live'),
          throwsA(isA<ApiException>()),
        );
        await tester.pumpAndSettle();

        expect(find.text(apiCommunicationMessage), findsOneWidget);

        shouldFail = false;
        await expectLater(
          apiClient.getJson('health/live'),
          throwsA(
            isA<ApiException>().having(
              (error) => error.statusCode,
              'statusCode',
              503,
            ),
          ),
        );
        await tester.pumpAndSettle();

        expect(find.text(apiCommunicationMessage), findsNothing);
        expect(find.text('Application content'), findsOneWidget);
      },
    );
  });
}
