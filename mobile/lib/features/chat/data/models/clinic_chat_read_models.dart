// CHAT-B: read-only, strictly clinic-scoped REST models.
// No response fields are trusted to grant authorization by themselves.

enum ClinicChatKind { direct, group, patient, department, team }

enum ClinicChatStatus { active, archived, closed }

enum ClinicMessageKind { text, image, video, audio, file, system }

enum ClinicMessageStatus { pending, sent, failed, edited, deleted }

enum ClinicMessagePriority { normal, urgent, stat }

class ClinicChatDecode {
  const ClinicChatDecode._();

  static Map<String, dynamic> object(Object? value) {
    if (value is! Map<String, dynamic>) {
      throw const FormatException('Expected chat JSON object.');
    }
    return value;
  }

  static int id(Map<String, dynamic> value, String key) {
    final item = value[key];
    if (item is! int || item < 1 || item > 2147483647) {
      throw FormatException('Invalid chat $key.');
    }
    return item;
  }

  static int? optionalId(Map<String, dynamic> value, String key) {
    if (value[key] == null) {
      return null;
    }
    return id(value, key);
  }

  static String text(Map<String, dynamic> value, String key) {
    final item = value[key];
    if (item is! String) {
      throw FormatException('Invalid chat $key.');
    }
    return item;
  }

  static String? optionalText(Map<String, dynamic> value, String key) {
    final item = value[key];
    if (item == null) {
      return null;
    }
    return text(value, key);
  }

  static DateTime timestamp(Map<String, dynamic> value, String key) {
    final raw = text(value, key);
    if (!RegExp(r'(Z|[+-][0-9]{2}:[0-9]{2})$').hasMatch(raw)) {
      throw FormatException('Chat $key needs an explicit timezone.');
    }
    final parsed = DateTime.tryParse(raw);
    if (parsed == null) {
      throw FormatException('Invalid chat $key.');
    }
    return parsed.toUtc();
  }

  static DateTime? optionalTimestamp(Map<String, dynamic> value, String key) {
    if (value[key] == null) {
      return null;
    }
    return timestamp(value, key);
  }

  static T enumeration<T extends Enum>(
    Map<String, dynamic> value,
    String key,
    Iterable<T> values,
  ) {
    final raw = text(value, key);
    for (final item in values) {
      if (item.name == raw) {
        return item;
      }
    }
    throw FormatException('Unsupported chat $key.');
  }
}

class ClinicChatConversation {
  const ClinicChatConversation({
    required this.id,
    required this.clinicId,
    required this.kind,
    required this.status,
    required this.title,
    required this.description,
    required this.createdById,
    required this.patientId,
    required this.lastMessageAt,
    required this.createdAt,
  });

  final int id;
  final int clinicId;
  final ClinicChatKind kind;
  final ClinicChatStatus status;
  final String? title;
  final String? description;
  final int createdById;
  final int? patientId;
  final DateTime? lastMessageAt;
  final DateTime createdAt;

  String get displayTitle {
    if (title != null && title!.trim().isNotEmpty) {
      return title!;
    }
    return switch (kind) {
      ClinicChatKind.direct => 'Direct conversation #$id',
      ClinicChatKind.patient => 'Patient case #$id',
      _ => 'Conversation #$id',
    };
  }

  factory ClinicChatConversation.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
  }) {
    final clinicId = ClinicChatDecode.id(json, 'clinic_id');
    if (clinicId != expectedClinicId) {
      throw const FormatException('Chat conversation clinic mismatch.');
    }
    final kind = ClinicChatDecode.enumeration(
      json,
      'conversation_type',
      ClinicChatKind.values,
    );
    final patientId = ClinicChatDecode.optionalId(json, 'patient_id');
    if (kind == ClinicChatKind.patient && patientId == null) {
      throw const FormatException('Patient conversation needs a patient ID.');
    }
    return ClinicChatConversation(
      id: ClinicChatDecode.id(json, 'id'),
      clinicId: clinicId,
      kind: kind,
      status: ClinicChatDecode.enumeration(
        json,
        'status',
        ClinicChatStatus.values,
      ),
      title: ClinicChatDecode.optionalText(json, 'title'),
      description: ClinicChatDecode.optionalText(json, 'description'),
      createdById: ClinicChatDecode.id(json, 'created_by_id'),
      patientId: patientId,
      lastMessageAt: ClinicChatDecode.optionalTimestamp(
        json,
        'last_message_at',
      ),
      createdAt: ClinicChatDecode.timestamp(json, 'created_at'),
    );
  }
}

class ClinicChatAttachment {
  const ClinicChatAttachment({
    required this.id,
    required this.type,
    required this.fileName,
    required this.mimeType,
    required this.fileSizeBytes,
  });

  final int id;
  final String type;
  final String? fileName;
  final String mimeType;
  final int? fileSizeBytes;

  factory ClinicChatAttachment.fromJson(Map<String, dynamic> json) {
    final type = ClinicChatDecode.text(json, 'attachment_type');
    if (!const <String>{
      'file',
      'image',
      'audio',
      'video',
      'document',
    }.contains(type)) {
      throw const FormatException('Unsupported chat attachment type.');
    }
    final size = json['file_size_bytes'];
    if (size != null && (size is! int || size < 0 || size > 25 * 1024 * 1024)) {
      throw const FormatException('Invalid chat attachment size.');
    }
    final mime = ClinicChatDecode.text(json, 'mime_type');
    if (mime.isEmpty || mime.length > 150) {
      throw const FormatException('Invalid chat attachment MIME.');
    }
    return ClinicChatAttachment(
      id: ClinicChatDecode.id(json, 'id'),
      type: type,
      fileName: ClinicChatDecode.optionalText(json, 'file_name'),
      mimeType: mime,
      fileSizeBytes: size as int?,
    );
  }
}

class ClinicChatMessage {
  const ClinicChatMessage({
    this.attachments = const <ClinicChatAttachment>[],
    required this.id,
    required this.clinicId,
    required this.conversationId,
    required this.senderId,
    required this.kind,
    required this.status,
    required this.priority,
    required this.content,
    required this.replyToMessageId,
    required this.createdAt,
  });

  final int id;
  final int clinicId;
  final int conversationId;
  final int senderId;
  final ClinicMessageKind kind;
  final List<ClinicChatAttachment> attachments;
  final ClinicMessageStatus status;
  final ClinicMessagePriority priority;
  final String? content;
  final int? replyToMessageId;
  final DateTime createdAt;

  String get safePreview {
    if (status == ClinicMessageStatus.deleted) {
      return 'Message deleted';
    }
    if (kind != ClinicMessageKind.text) {
      if (attachments.isNotEmpty) {
        return switch (kind) {
          ClinicMessageKind.image => 'Image attachment',
          ClinicMessageKind.audio => 'Voice attachment',
          ClinicMessageKind.file => 'Document attachment',
          _ => 'Protected attachment',
        };
      }
      return 'Unsupported media message';
    }
    return content ?? '';
  }

  factory ClinicChatMessage.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    int? expectedConversationId,
  }) {
    final clinicId = ClinicChatDecode.id(json, 'clinic_id');
    final conversationId = ClinicChatDecode.id(json, 'conversation_id');
    if (clinicId != expectedClinicId ||
        (expectedConversationId != null &&
            conversationId != expectedConversationId)) {
      throw const FormatException('Chat message scope mismatch.');
    }
    final kind = ClinicChatDecode.enumeration(
      json,
      'message_type',
      ClinicMessageKind.values,
    );
    final status = ClinicChatDecode.enumeration(
      json,
      'status',
      ClinicMessageStatus.values,
    );
    final content = ClinicChatDecode.optionalText(json, 'content');
    if (kind == ClinicMessageKind.text &&
        status != ClinicMessageStatus.deleted &&
        (content == null || content.trim().isEmpty)) {
      throw const FormatException('Text message has no content.');
    }
    final rawAttachments = json['attachments'];
    if (rawAttachments != null && rawAttachments is! List) {
      throw const FormatException('Invalid chat attachment list.');
    }
    final attachments = <ClinicChatAttachment>[
      for (final raw in (rawAttachments as List? ?? const []))
        ClinicChatAttachment.fromJson(ClinicChatDecode.object(raw)),
    ];
    if (attachments.map((item) => item.id).toSet().length !=
        attachments.length) {
      throw const FormatException('Duplicate chat attachment ID.');
    }
    return ClinicChatMessage(
      attachments: List.unmodifiable(attachments),
      id: ClinicChatDecode.id(json, 'id'),
      clinicId: clinicId,
      conversationId: conversationId,
      senderId: ClinicChatDecode.id(json, 'sender_id'),
      kind: kind,
      status: status,
      priority: ClinicChatDecode.enumeration(
        json,
        'priority',
        ClinicMessagePriority.values,
      ),
      content: content,
      replyToMessageId: ClinicChatDecode.optionalId(
        json,
        'reply_to_message_id',
      ),
      createdAt: ClinicChatDecode.timestamp(json, 'created_at'),
    );
  }
}

class ClinicChatPage<T> {
  const ClinicChatPage({
    required this.items,
    required this.page,
    required this.perPage,
    required this.total,
    required this.pages,
    required this.hasNext,
    required this.hasPrevious,
  });

  final List<T> items;
  final int page;
  final int perPage;
  final int total;
  final int pages;
  final bool hasNext;
  final bool hasPrevious;

  static ClinicChatPage<E> parse<E>(
    Map<String, dynamic> envelope,
    E Function(Map<String, dynamic>) read,
  ) {
    if (envelope['success'] != true) {
      throw const FormatException('Chat list did not indicate success.');
    }
    final raw = envelope['data'];
    if (raw is! List) {
      throw const FormatException('Chat list data must be an array.');
    }
    final page = envelope['page'];
    final perPage = envelope['per_page'];
    final total = envelope['total'];
    final pages = envelope['pages'];
    final next = envelope['has_next'];
    final previous = envelope['has_previous'];
    if (page is! int ||
        page < 1 ||
        perPage is! int ||
        perPage < 1 ||
        perPage > 500 ||
        total is! int ||
        total < 0 ||
        pages is! int ||
        pages < 0 ||
        next is! bool ||
        previous is! bool ||
        pages != (total + perPage - 1) ~/ perPage ||
        next != (page < pages) ||
        previous != (page > 1 && total > 0) ||
        raw.length > perPage ||
        raw.length > total) {
      throw const FormatException('Chat pagination metadata is inconsistent.');
    }
    final items = <E>[];
    for (final item in raw) {
      items.add(read(ClinicChatDecode.object(item)));
    }
    return ClinicChatPage<E>(
      items: List<E>.unmodifiable(items),
      page: page,
      perPage: perPage,
      total: total,
      pages: pages,
      hasNext: next,
      hasPrevious: previous,
    );
  }
}
