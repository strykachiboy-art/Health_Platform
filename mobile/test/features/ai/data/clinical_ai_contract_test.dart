import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/ai/data/clinical_ai_contract.dart';

void main() {
  test('GAP-AI exact Flask endpoint mapping', () {
    expect(
      ClinicalAiFeature.drugInteractions.path,
      'api/v1/ai/drug-interactions',
    );
    expect(ClinicalAiFeature.triage.path, 'api/v1/ai/triage');
    expect(
      ClinicalAiFeature.labInterpretation.path,
      'api/v1/ai/lab-results/interpret',
    );
  });

  test('valid triage result remains clinician-review-only', () {
    final advisory = ClinicalAiAdvisory.fromEnvelope(
      ClinicalAiFeature.triage,
      <String, dynamic>{
        'success': true,
        'data': <String, dynamic>{
          'summary': 'Review observations.',
          'risk_score': 'high',
          'recommendation': 'Clinician assessment.',
        },
      },
    );
    expect(advisory.riskScore, 'high');
    expect(advisory.summary, 'Review observations.');
    expect(advisory.interpretation, isNull);
  });

  test('reject malformed envelope, incorrect risk, unbounded results', () {
    expect(
      () => ClinicalAiAdvisory.fromEnvelope(ClinicalAiFeature.triage, {
        'success': true,
        'data': {'risk_score': 'certain'},
      }),
      throwsFormatException,
    );
    expect(
      () => ClinicalAiAdvisory.fromEnvelope(
        ClinicalAiFeature.drugInteractions,
        {'success': false, 'data': {}},
      ),
      throwsFormatException,
    );
    expect(
      () =>
          ClinicalAiAdvisory.fromEnvelope(ClinicalAiFeature.labInterpretation, {
            'success': true,
            'data': {'abnormal_findings': List<String>.filled(101, 'x')},
          }),
      throwsFormatException,
    );
    expect(
      () =>
          ClinicalAiAdvisory.fromEnvelope(ClinicalAiFeature.drugInteractions, {
            'success': true,
            'data': {
              'interactions': ['not-an-object'],
            },
          }),
      throwsFormatException,
    );
  });
}
