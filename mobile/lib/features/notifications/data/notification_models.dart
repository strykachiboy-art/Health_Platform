/// P24A backend-authoritative, clinic- and recipient-scoped notification data.
/// No local PHI persistence, speculative reference navigation, or push transport.
class ClinicNotification {
  const ClinicNotification({
    required this.id,
    required this.clinicId,
    required this.userId,
    required this.title,
    required this.message,
    required this.type,
    required this.priority,
    required this.channel,
    required this.status,
    required this.isRead,
    required this.createdAt,
    required this.readAt,
    required this.referenceType,
    required this.referenceId,
  });

  final int id;
  final int clinicId;
  final int userId;
  final String title;
  final String message;
  final String type;
  final String priority;
  final String channel;
  final String status;
  final bool isRead;
  final String? createdAt;
  final String? readAt;
  final String? referenceType;
  final int? referenceId;

  static const _types = <String>{
    'general',
    'appointment',
    'consultation',
    'lab',
    'pharmacy',
    'prescription',
    'inventory',
    'billing',
    'ward',
    'ambulance',
    'hie',
    'system',
  };
  static const _priorities = <String>{'normal', 'high', 'urgent'};
  static const _channels = <String>{'in_app', 'email', 'sms', 'push'};
  static const _statuses = <String>{
    'pending',
    'sent',
    'delivered',
    'read',
    'failed',
  };

  static int _id(Map<String, dynamic> json, String key, {int minimum = 1}) {
    final value = json[key];
    if (value is! int || value < minimum) {
      throw const FormatException('Invalid notification identifier.');
    }
    return value;
  }

  static String _text(Map<String, dynamic> json, String key) {
    final value = json[key];
    if (value is! String || value.trim().isEmpty) {
      throw const FormatException('Invalid notification text.');
    }
    return value;
  }

  static String _choice(
    Map<String, dynamic> json,
    String key,
    Set<String> choices,
  ) {
    final value = json[key];
    if (value is! String || !choices.contains(value)) {
      throw const FormatException('Unknown notification classification.');
    }
    return value;
  }

  static String? _optionalText(Map<String, dynamic> json, String key) {
    final value = json[key];
    if (value == null) return null;
    if (value is! String) {
      throw const FormatException('Malformed optional notification field.');
    }
    return value;
  }

  static String? _time(Map<String, dynamic> json, String key) {
    final raw = json[key];
    if (raw == null) return null;
    if (raw is! String || DateTime.tryParse(raw) == null) {
      throw const FormatException('Invalid notification timestamp.');
    }
    return raw;
  }

  factory ClinicNotification.fromJson(
    Map<String, dynamic> json, {
    required int expectedUserId,
    required int expectedClinicId,
  }) {
    final user = _id(json, 'user_id');
    final clinic = _id(json, 'clinic_id');
    if (user != expectedUserId || clinic != expectedClinicId) {
      throw const FormatException('Notification owner or clinic mismatch.');
    }
    final isRead = json['is_read'];
    if (isRead is! bool) {
      throw const FormatException('Invalid notification read state.');
    }
    final referenceId = json['reference_id'];
    if (referenceId != null && (referenceId is! int || referenceId < 1)) {
      throw const FormatException('Invalid notification reference.');
    }
    final value = ClinicNotification(
      id: _id(json, 'id'),
      clinicId: clinic,
      userId: user,
      title: _text(json, 'title'),
      message: _text(json, 'message'),
      type: _choice(json, 'notification_type', _types),
      priority: _choice(json, 'priority', _priorities),
      channel: _choice(json, 'channel', _channels),
      status: _choice(json, 'status', _statuses),
      isRead: isRead,
      createdAt: _time(json, 'created_at'),
      readAt: _time(json, 'read_at'),
      referenceType: _optionalText(json, 'reference_type'),
      referenceId: referenceId as int?,
    );
    if (isRead && value.readAt == null) {
      throw const FormatException('Read notification missing timestamp.');
    }
    return value;
  }

  static ClinicNotification fromEnvelope(
    Map<String, dynamic> envelope, {
    required int expectedUserId,
    required int expectedClinicId,
    required int expectedId,
  }) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Notification detail not confirmed.');
    }
    final parsed = ClinicNotification.fromJson(
      envelope['data'] as Map<String, dynamic>,
      expectedUserId: expectedUserId,
      expectedClinicId: expectedClinicId,
    );
    if (parsed.id != expectedId) {
      throw const FormatException('Notification detail ID mismatch.');
    }
    return parsed;
  }
}

class ClinicNotificationPage {
  const ClinicNotificationPage({
    required this.items,
    required this.page,
    required this.perPage,
    required this.total,
    required this.pages,
    required this.hasNext,
    required this.hasPrev,
  });

  final List<ClinicNotification> items;
  final int page;
  final int perPage;
  final int total;
  final int pages;
  final bool hasNext;
  final bool hasPrev;

  factory ClinicNotificationPage.fromEnvelope(
    Map<String, dynamic> envelope, {
    required int expectedUserId,
    required int expectedClinicId,
    required int requestedPage,
    required int requestedPerPage,
    required bool unreadOnly,
  }) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Notification page not confirmed.');
    }
    final data = envelope['data'] as Map<String, dynamic>;
    final rows = data['items'];
    final page = data['page'];
    final perPage = data['per_page'];
    final total = data['total'];
    final pages = data['pages'];
    final hasNext = data['has_next'];
    final hasPrev = data['has_prev'];
    if (rows is! List ||
        page is! int ||
        perPage is! int ||
        total is! int ||
        pages is! int ||
        hasNext is! bool ||
        hasPrev is! bool ||
        page != requestedPage ||
        perPage != requestedPerPage ||
        total < 0 ||
        perPage < 1 ||
        perPage > 500 ||
        pages != (total + perPage - 1) ~/ perPage ||
        hasNext != (page < pages) ||
        hasPrev != (page > 1) ||
        rows.length > perPage ||
        rows.length > total ||
        (pages > 0 && page > pages && rows.isNotEmpty)) {
      throw const FormatException('Inconsistent notification pagination.');
    }
    final seen = <int>{};
    final parsed = <ClinicNotification>[];
    for (final row in rows) {
      if (row is! Map<String, dynamic>) {
        throw const FormatException('Malformed notification page item.');
      }
      final item = ClinicNotification.fromJson(
        row,
        expectedUserId: expectedUserId,
        expectedClinicId: expectedClinicId,
      );
      if (!seen.add(item.id) || (unreadOnly && item.isRead)) {
        throw const FormatException('Invalid unread notification page.');
      }
      parsed.add(item);
    }
    return ClinicNotificationPage(
      items: List<ClinicNotification>.unmodifiable(parsed),
      page: page,
      perPage: perPage,
      total: total,
      pages: pages,
      hasNext: hasNext,
      hasPrev: hasPrev,
    );
  }
}
