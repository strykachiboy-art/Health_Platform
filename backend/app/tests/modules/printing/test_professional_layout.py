"""R2C: independently inspect native/ReportLab parity using synthetic fixtures."""
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import json
import os
import subprocess
import pytest
from PIL import Image
from app.modules.printing.services.print_policy import build_document, PrintDenied
from app.modules.printing.services.branding_payload import PrintBranding
from app.modules.printing.services.professional_layout import make_plan, paint_reportlab
from app.modules.printing.services import rust_print_bridge as native
from app.tests.modules.printing.test_branded_rendering import document, brand_meta


def fixture():
    rgb=Image.new('RGB',(160,64),'#176B65').tobytes()
    branding=PrintBranding(1,('Organisation: Synthetic Alpha','Clinic: Alpha HQ','Address: 12 Synthetic Way','Country: Nigeria'), 'a'*64,'b'*64,'organization',1,0,rgb,160,64,'#176B65','#283548')
    invoice=build_document(kind='invoice',clinic_id=1,record_id=90,source_version=0,source={'invoice_number':'R2C-SYNTHETIC','patient_id':11,'status':'issued','currency':'NGN','total_amount':'20000.00','amount_paid':'2500.00','invoice_items':json.dumps([['Consultation Café','2','5000.00','10000.00'],['Synthetic lab service','1','10000.00','10000.00']],ensure_ascii=False,separators=(',',':')),'outstanding_balance':'17500.00'})
    return invoice,branding


def test_native_professional_plan_parity_artifacts(tmp_path):
    invoice,branding=fixture()
    for label,doc in [('invoice',invoice),('draft',document('radiology_draft',1,long=True))]:
        plan,faces=make_plan(doc,branding)
        for page in plan['pages']:
            assert any(t['bold'] and t['tag']=='header' for t in page['text'])
            assert any(t['tag']=='warning' and t['color']=='#842020' for t in page['text'])
            assert any(t['tag']=='disclaimer' for t in page['text'])
            assert page['logo'] is not None
        a=native.render_pdf(doc,branding=branding);b=paint_reportlab(plan,faces,branding)
        for engine,data in [('native',a),('reference',b)]:
            assert data.startswith(b'%PDF-') and data.rstrip().endswith(b'%%EOF') and len(data)<=3*1024*1024
            (tmp_path/f'l05r2c_{label}_{engine}.pdf').write_bytes(data)
        (tmp_path/f'l05r2c_{label}_plan.json').write_text(json.dumps({'doc':asdict(doc),'branding':brand_meta(branding),'plan':plan,'native_sha256':sha256(a).hexdigest(),'reference_sha256':sha256(b).hexdigest()},ensure_ascii=True,indent=2),encoding='utf-8')
    with pytest.raises(PrintDenied):make_plan(replace(invoice,source_digest='0'*64),branding)
    with pytest.raises(PrintDenied):make_plan(invoice,replace(branding,clinic_id=2))


def test_native_professional_plan_tampering_is_denied():
    doc,b=fixture();plan,_=make_plan(doc,b)
    wire={**asdict(doc),'protocol':3,'branding':b.wire(),'professional':plan}
    def invoke(payload):return subprocess.run([os.environ['CSP_RUST_PRINT_RENDERER']],input=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8'),capture_output=True,timeout=30)
    r=invoke(wire);assert r.returncode==0 and r.stdout.startswith(b'%PDF-')
    variants=[]
    q=deepcopy(wire);q['professional']['pages'][0]['text'][0]['text']='Foreign organisation';variants.append(q)
    q=deepcopy(wire);q['professional']['pages'][0]['text']= [t for t in q['professional']['pages'][0]['text'] if t['tag']!='warning'];variants.append(q)
    q=deepcopy(wire);next(t for t in q['professional']['pages'][0]['text'] if t['tag']=='body')['text']='Untrusted';variants.append(q)
    q=deepcopy(wire);q['professional']['pages'][0]['text'][0]['x']=-12;variants.append(q)
    q=deepcopy(wire);q['professional']['pages'][0]['rects'][0]['color']='#ABCDEF';variants.append(q)
    q=deepcopy(wire);q['professional']['pages'][0]['logo']=list(q['professional']['pages'][0]['logo']);q['professional']['pages'][0]['logo'][2]=500;variants.append(q)
    q=deepcopy(wire);q['professional']['pages'][0]['text'][0]['tag']='other';variants.append(q)
    q=deepcopy(wire);q['branding']['clinic_id']=2;variants.append(q)
    variants.extend([{**wire,'protocol':2},{**wire,'professional':None},{**wire,'is_signed':True}])
    for bad in variants:
        r=invoke(bad);assert r.returncode!=0 and not r.stdout.startswith(b'%PDF-')
