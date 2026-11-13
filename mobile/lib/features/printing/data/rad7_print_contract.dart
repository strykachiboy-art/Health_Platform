/// RAD7 describes the four RAD6 server-authorized source kinds. This client
/// never grants printing authority. The backend source adapters remain final.
enum Rad7PrintKind { invoice, appointment, labOrder, radiologyDraft }

extension Rad7PrintKindContract on Rad7PrintKind {
  String get apiKind => switch (this) {
    Rad7PrintKind.invoice => 'invoice',
    Rad7PrintKind.appointment => 'appointment',
    Rad7PrintKind.labOrder => 'lab_order',
    Rad7PrintKind.radiologyDraft => 'radiology_draft',
  };

  String get label => switch (this) {
    Rad7PrintKind.invoice => 'Invoice copy (not a receipt)',
    Rad7PrintKind.appointment => 'Appointment information (not attendance)',
    Rad7PrintKind.labOrder => 'Lab order (not a verified result)',
    Rad7PrintKind.radiologyDraft => 'Radiology PRIVATE UNSIGNED working note',
  };

  bool allowsRole(String role) {
    final normalized = role.trim().toLowerCase();
    return switch (this) {
      Rad7PrintKind.invoice => const <String>{
        'admin',
        'accountant',
      }.contains(normalized),
      Rad7PrintKind.appointment => const <String>{
        'admin',
        'receptionist',
        'doctor',
      }.contains(normalized),
      Rad7PrintKind.labOrder => const <String>{
        'admin',
        'lab_technician',
      }.contains(normalized),
      Rad7PrintKind.radiologyDraft => normalized == 'doctor',
    };
  }
}

class Rad7PrintContract {
  const Rad7PrintContract._();

  // RAD6_PRINTING_ENABLED=False on the pinned backend. A compile-time flag,
  // remote config, or UI role MUST NOT enable this action. Release requires a
  // separately reviewed backend authorization and device print integration.
  static const bool downloadAndPrintApproved = false;

  static List<Rad7PrintKind> permitted(String role) =>
      List<Rad7PrintKind>.unmodifiable(
        Rad7PrintKind.values.where((kind) => kind.allowsRole(role)),
      );

  static String? plannedServerPath(Rad7PrintKind kind, int recordId) {
    if (recordId <= 0 || recordId > 2147483647) return null;
    return 'api/v1/printing/${kind.apiKind}/$recordId.pdf';
  }
}
