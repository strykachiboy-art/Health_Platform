/// Safe, immutable representation of a registered device.
/// Never keep, log or display the backend's `device_token` field.
class ManagedDevice {
  const ManagedDevice({
    required this.id,
    required this.userId,
    required this.platform,
    required this.name,
    required this.active,
    required this.lastSeenAt,
    required this.createdAt,
  });

  final int id;
  final int userId;
  final String platform;
  final String? name;
  final bool active;
  final DateTime? lastSeenAt;
  final DateTime createdAt;

  static ManagedDevice fromJson(
    Map<String, dynamic> json, {
    required int expectedUserId,
  }) {
    final id = json['id'];
    final userId = json['user_id'];
    final platform = json['platform'];
    final name = json['device_name'];
    final active = json['is_active'];
    final createdAt = json['created_at'];
    final lastSeenAt = json['last_seen_at'];
    if (id is! int ||
        id <= 0 ||
        userId is! int ||
        userId != expectedUserId ||
        platform is! String ||
        !const <String>{'android', 'ios', 'web'}.contains(platform) ||
        (name != null && (name is! String || name.length > 100)) ||
        active is! bool ||
        createdAt is! String ||
        DateTime.tryParse(createdAt) == null ||
        (lastSeenAt != null &&
            (lastSeenAt is! String || DateTime.tryParse(lastSeenAt) == null))) {
      throw const FormatException('Invalid owner-scoped device record.');
    }
    // Ignore device_token and every other unapproved property.
    return ManagedDevice(
      id: id,
      userId: userId,
      platform: platform,
      name: name as String?,
      active: active,
      lastSeenAt: lastSeenAt == null
          ? null
          : DateTime.parse(lastSeenAt as String),
      createdAt: DateTime.parse(createdAt),
    );
  }
}

class ManagedDevicePage {
  const ManagedDevicePage({
    required this.items,
    required this.page,
    required this.total,
    required this.hasNext,
    required this.hasPrev,
    required this.confirmedCurrentDeviceId,
  });

  final List<ManagedDevice> items;
  final int page;
  final int total;
  final bool hasNext;
  final bool hasPrev;

  /// Only populated from a P24B receipt confirmed in this very session.
  final int? confirmedCurrentDeviceId;

  static ManagedDevicePage fromEnvelope(
    Map<String, dynamic> envelope, {
    required int expectedUserId,
    required int expectedPage,
    int? confirmedCurrentDeviceId,
  }) {
    final data = envelope['data'];
    if (envelope['success'] != true || data is! Map<String, dynamic>) {
      throw const FormatException('Invalid device list response.');
    }
    final raw = data['items'];
    final page = data['page'];
    final perPage = data['per_page'];
    final total = data['total'];
    final pages = data['pages'];
    final hasNext = data['has_next'];
    final hasPrev = data['has_prev'];
    if (raw is! List ||
        page is! int ||
        page != expectedPage ||
        perPage is! int ||
        perPage <= 0 ||
        perPage > 100 ||
        total is! int ||
        total < 0 ||
        pages is! int ||
        pages < 0 ||
        hasNext is! bool ||
        hasPrev is! bool ||
        raw.length > perPage) {
      throw const FormatException('Invalid pagination metadata.');
    }
    final items = <ManagedDevice>[];
    final seen = <int>{};
    for (final item in raw) {
      if (item is! Map<String, dynamic>) {
        throw const FormatException('Invalid device item.');
      }
      final device = ManagedDevice.fromJson(
        item,
        expectedUserId: expectedUserId,
      );
      if (!seen.add(device.id)) {
        throw const FormatException('Duplicate device ID.');
      }
      items.add(device);
    }
    return ManagedDevicePage(
      items: List<ManagedDevice>.unmodifiable(items),
      page: page,
      total: total,
      hasNext: hasNext,
      hasPrev: hasPrev,
      confirmedCurrentDeviceId: confirmedCurrentDeviceId,
    );
  }
}
