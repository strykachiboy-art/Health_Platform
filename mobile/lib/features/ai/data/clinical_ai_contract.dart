/// Strict, clinician-review-only representations of the existing Flask AI API.
/// These are not orders, diagnoses, prescriptions or lab finalizations.
enum ClinicalAiFeature { drugInteractions, triage, labInterpretation }

extension ClinicalAiFeatureContract on ClinicalAiFeature {
  String get path => switch (this) {
    ClinicalAiFeature.drugInteractions => 'api/v1/ai/drug-interactions',
    ClinicalAiFeature.triage => 'api/v1/ai/triage',
    ClinicalAiFeature.labInterpretation => 'api/v1/ai/lab-results/interpret',
  };

  String get label => switch (this) {
    ClinicalAiFeature.drugInteractions => 'Drug interactions',
    ClinicalAiFeature.triage => 'Triage assistance',
    ClinicalAiFeature.labInterpretation => 'Laboratory interpretation',
  };
}

class ClinicalAiAdvisory {
  const ClinicalAiAdvisory({
    required this.feature,
    this.summary,
    this.interactions = const [],
    this.recommendations = const [],
    this.riskScore,
    this.recommendation,
    this.interpretation,
    this.abnormalFindings = const [],
  });

  final ClinicalAiFeature feature;
  final String? summary;
  final List<Map<String, dynamic>> interactions;
  final List<String> recommendations;
  final String? riskScore;
  final String? recommendation;
  final String? interpretation;
  final List<String> abnormalFindings;

  static String? _optionalText(
    Map<String, dynamic> obj,
    String field,
    int max,
  ) {
    final value = obj[field];
    if (value == null) return null;
    if (value is! String ||
        value.trim() != value ||
        value.isEmpty ||
        value.length > max) {
      throw const FormatException('Invalid AI advisory text.');
    }
    return value;
  }

  static List<String> _strings(
    Map<String, dynamic> obj,
    String field,
    int maxCount,
    int maxLength,
  ) {
    final raw = obj[field];
    if (raw == null) return const [];
    if (raw is! List || raw.length > maxCount) {
      throw const FormatException('Invalid AI advisory list.');
    }
    final items = <String>[];
    for (final item in raw) {
      if (item is! String ||
          item.trim().isEmpty ||
          item != item.trim() ||
          item.length > maxLength) {
        throw const FormatException('Invalid AI advisory item.');
      }
      items.add(item);
    }
    return List<String>.unmodifiable(items);
  }

  static List<Map<String, dynamic>> _interactions(Map<String, dynamic> obj) {
    final raw = obj['interactions'];
    if (raw == null) return const [];
    if (raw is! List || raw.length > 100) {
      throw const FormatException('Invalid AI interaction list.');
    }
    final items = <Map<String, dynamic>>[];
    for (final entry in raw) {
      if (entry is! Map<String, dynamic> || entry.length > 20) {
        throw const FormatException('Invalid AI interaction entry.');
      }
      items.add(Map<String, dynamic>.unmodifiable(entry));
    }
    return List<Map<String, dynamic>>.unmodifiable(items);
  }

  factory ClinicalAiAdvisory.fromEnvelope(
    ClinicalAiFeature feature,
    Map<String, dynamic> envelope,
  ) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid AI response envelope.');
    }
    final data = envelope['data'] as Map<String, dynamic>;
    final summary = _optionalText(data, 'summary', 5000);
    switch (feature) {
      case ClinicalAiFeature.drugInteractions:
        return ClinicalAiAdvisory(
          feature: feature,
          summary: summary,
          interactions: _interactions(data),
          recommendations: _strings(data, 'recommendations', 50, 2000),
        );
      case ClinicalAiFeature.triage:
        final risk = data['risk_score'];
        if (risk is! String ||
            !const {'low', 'medium', 'high', 'critical'}.contains(risk)) {
          throw const FormatException('Invalid AI risk classification.');
        }
        return ClinicalAiAdvisory(
          feature: feature,
          summary: summary,
          riskScore: risk,
          recommendation: _optionalText(data, 'recommendation', 2000),
        );
      case ClinicalAiFeature.labInterpretation:
        return ClinicalAiAdvisory(
          feature: feature,
          summary: summary,
          interpretation: _optionalText(data, 'interpretation', 10000),
          abnormalFindings: _strings(data, 'abnormal_findings', 100, 2000),
          recommendations: _strings(data, 'recommendations', 50, 2000),
        );
    }
  }
}
