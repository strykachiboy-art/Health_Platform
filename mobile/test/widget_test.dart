import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/main.dart';

class _TestLoginRoute extends StatelessWidget {
  const _TestLoginRoute();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('Test login route'),
            ElevatedButton(
              onPressed: () => context.go('/health'),
              child: const Text('Open backend health'),
            ),
          ],
        ),
      ),
    );
  }
}

void main() {
  testWidgets('app opens login route and retains backend health route', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.url, Uri.parse('http://127.0.0.1:5000/health/live'));
      return http.Response('{"success":true,"status":"ok"}', 200);
    });
    addTearDown(client.close);

    final dataSource = BackendHealthRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
    );

    await tester.pumpWidget(
      ClinicSystemProApp(
        healthDataSource: dataSource,
        loginScreen: const _TestLoginRoute(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Test login route'), findsOneWidget);

    await tester.tap(find.text('Open backend health'));
    await tester.pumpAndSettle();

    expect(find.text('Backend connectivity'), findsOneWidget);
    expect(find.text('Backend reachable'), findsOneWidget);
    expect(find.text('Check again'), findsOneWidget);
  });
}
