import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/notifications/data/notification_models.dart';

Map<String, dynamic> row({
  int id = 7,
  int clinic = 3,
  int user = 41,
  bool read = false,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'user_id': user,
  'title': 'New appointment',
  'message': 'There is a new appointment',
  'notification_type': 'appointment',
  'priority': 'normal',
  'channel': 'in_app',
  'status': read ? 'read' : 'pending',
  'is_read': read,
  'read_at': read ? '2026-11-11T09:00:00+00:00' : null,
  'created_at': '2026-11-11T08:00:00+00:00',
  'reference_type': null,
  'reference_id': null,
};

Map<String, dynamic> page(
  List<Map<String, dynamic>> rows, {
  int page = 1,
  int perPage = 20,
  int? total,
  bool hasNext = false,
}) => <String, dynamic>{
  'success': true,
  'data': <String, dynamic>{
    'items': rows,
    'page': page,
    'per_page': perPage,
    'total': total ?? rows.length,
    'pages': ((total ?? rows.length) + perPage - 1) ~/ perPage,
    'has_next': hasNext,
    'has_prev': page > 1,
  },
};

ClinicNotificationPage parse(
  Map<String, dynamic> envelope, {
  bool unread = false,
}) => ClinicNotificationPage.fromEnvelope(
  envelope,
  expectedClinicId: 3,
  expectedUserId: 41,
  requestedPage: 1,
  requestedPerPage: 20,
  unreadOnly: unread,
);

void main() {
  test('backend notification envelope and owner/clinic scope', () {
    final result = parse(page([row()]));
    expect(result.total, 1);
    expect(result.items.single.id, 7);
    expect(result.items.single.isRead, isFalse);
    expect(result.items.single.message, contains('appointment'));

    expect(() => parse(page([row(user: 42)])), throwsFormatException);
    expect(() => parse(page([row(clinic: 4)])), throwsFormatException);
  });

  test('duplicate IDs and read rows in unread page fail closed', () {
    expect(() => parse(page([row(), row()])), throwsFormatException);
    expect(
      () => parse(page([row(read: true)]), unread: true),
      throwsFormatException,
    );
  });

  test(
    'rejects inconsistent pagination, unrecognized enum and invalid fields',
    () {
      final invalidPage = page([row()]);
      (invalidPage['data'] as Map<String, dynamic>)['has_next'] = true;
      expect(() => parse(invalidPage), throwsFormatException);
      final wrongStatus = row()..['priority'] = 'super-urgent';
      expect(() => parse(page([wrongStatus])), throwsFormatException);
      final invalidTimestamp = row()..['created_at'] = 'not-a-date';
      expect(() => parse(page([invalidTimestamp])), throwsFormatException);
    },
  );

  test('detail ID and server read confirmation', () {
    final good = ClinicNotification.fromEnvelope(
      {'success': true, 'data': row(read: true)},
      expectedClinicId: 3,
      expectedUserId: 41,
      expectedId: 7,
    );
    expect(good.isRead, isTrue);
    expect(good.readAt, isNotNull);
    expect(
      () => ClinicNotification.fromEnvelope(
        {'success': true, 'data': row()},
        expectedClinicId: 3,
        expectedUserId: 41,
        expectedId: 8,
      ),
      throwsFormatException,
    );
  });
}
