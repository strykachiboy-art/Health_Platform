#!/usr/bin/env python3
"""RAD6 synthetic-only PDF artifact/evidence gate, no database or network."""
from __future__ import annotations
import argparse
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
import types

THIS=Path(__file__).resolve()
BACKEND=THIS.parents[2]
SERVICES=BACKEND/'app/modules/printing/services'
pkg=types.ModuleType('rad6preview')
pkg.__path__=[str(SERVICES)]
sys.modules['rad6preview']=pkg
policy=importlib.import_module('rad6preview.print_policy')
renderer=importlib.import_module('rad6preview.print_renderer')

SOURCES={
 'invoice':dict(invoice_number='FAKE-INV-0001',patient_id=42,status='ISSUED',currency='NGN',total_amount='150.00',amount_paid='0.00'),
 'appointment':dict(patient_id=42,assigned_staff_id=8,status='SCHEDULED',scheduled_start='2030-01-01 09:00 UTC',scheduled_end='2030-01-01 09:30 UTC'),
 'lab_order':dict(patient_id=42,status='PENDING',created_at='2030-01-01',sample_collected_at='NOT COLLECTED'),
 'radiology_draft':dict(imaging_order_id=102,accession_number='RAD-0123456789ABCDEF01234567',report_revision=2,report_text='SYNTHETIC WORKING NOTE ONLY. Not a real diagnosis.'),
}

def run(out:Path):
    out.mkdir(parents=True,exist_ok=False)
    evidence={'gate':'RAD6_IN_MEMORY_SYNTHETIC_PDF','clinical_data':'NONE',
              'printing_feature_enabled':False,'documents':[]}
    for kind,source in SOURCES.items():
        d=policy.build_document(kind=kind,clinic_id=1,record_id=42,
            source_version=2 if kind=='radiology_draft' else 0,source=source)
        data=renderer.render_pdf(d)
        assert data.startswith(b'%PDF-') and len(data)<250000
        assert d.watermark.encode('ascii') in data
        assert b'/JavaScript' not in data and b'/URI' not in data
        name='RAD6_SYNTHETIC_'+kind+'.pdf'
        (out/name).write_bytes(data)
        evidence['documents'].append({'name':name,'sha256':sha256(data).hexdigest(),
              'source_sha256':d.source_digest,'watermark':d.watermark,'bytes':len(data)})
    evidence['all_four_marked_and_in_memory']=True
    (out/'RAD6_SYNTHETIC_PDF_EVIDENCE.json').write_text(
        json.dumps(evidence,indent=2,sort_keys=True)+'\n',encoding='utf8')
    print('RAD6_FOUR_SYNTHETIC_SHARED_PRINT_PDFS=PASS',flush=True)
    print('RAD6_NO_DIAGNOSIS_SIGNATURE_CHART_RELEASE=PASS',flush=True)
    print('RAD6_SYNTHETIC_PDF_EVIDENCE='+str(out/'RAD6_SYNTHETIC_PDF_EVIDENCE.json'),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',required=True)
    ns=parser.parse_args()
    run(Path(ns.out).resolve())
