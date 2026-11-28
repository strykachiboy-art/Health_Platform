"""L05 Run7: colour responsibility is a presentation-only contract."""
from dataclasses import replace
from hashlib import sha256
import base64
import json
import os
from pathlib import Path
import re
import zlib

import pytest
from app.modules.printing.services.professional_layout import make_plan, paint_reportlab, H, FOOT_TOP, L, WIDTH, WARN, WARN_BG, INK
from app.modules.printing.services.print_policy import build_document
from app.modules.printing.services.polished_patient import canonical_lab_text
from app.modules.printing.services import rust_print_bridge as rust, print_renderer as py
from app.tests.modules.clinic.test_print_branding import world
from app.tests.modules.printing.test_professional_layout import fixture
from app.tests.modules.printing.test_branded_rendering import document

PALETTES = [('#136228', '#000000'), ('#1D4ED8', '#8A4B26')]
KINDS = ('invoice', 'appointment', 'lab_order', 'radiology_draft', 'released_lab_result', 'staged_radiology_copy')


def painted_rgb(pdf):
    # Inspect actual PDF page-content colour operators (not file hash alone).
    # Both engines emit RGB fill operators; fonts/images are not evidence.
    assert pdf.startswith(b'%PDF-') and pdf.rstrip().endswith(b'%%EOF') and len(pdf) <= 3 * 1024 * 1024
    number = rb'([-+]?(?:\d+(?:\.\d*)?|\.\d+))'
    pattern = re.compile(rb'(?<!\S)'+number+rb'\s+'+number+rb'\s+'+number+rb'\s+rg(?=\s)')
    found = []
    for m in re.finditer(rb'(?m)^\d+\s+\d+\s+obj\b', pdf):
        end = pdf.find(b'endobj', m.end())
        if end < 0: continue
        obj = pdf[m.end():end]
        st = re.search(rb'\bstream\r?\n', obj)
        if st is None: continue
        header = obj[:st.start()]
        if re.search(rb'/Subtype\s*/Image\b', header) or b'/Length1' in header: continue
        stop = obj.rfind(b'endstream')
        if stop < st.end(): continue
        raw = obj[st.end():stop]
        try:
            if b'/ASCII85Decode' in header: raw = base64.a85decode(raw.strip(), adobe=True)
            if b'/FlateDecode' in header: raw = zlib.decompress(raw)
        except (ValueError, zlib.error): continue
        if b'BT' not in raw or b' re' not in raw: continue
        found.extend(tuple(float(v) for v in x.groups()) for x in pattern.finditer(raw))
    assert found, 'No PDF painting operators detected'
    return found


def has_rgb(found, color):
    rgb = tuple(int(color[i:i+2], 16)/255 for i in (1,3,5))
    return any(all(abs(a-b)<0.00002 for a,b in zip(v,rgb)) for v in found)


def create_case(kind):
    invoice, brand = fixture()
    base = invoice if kind == 'invoice' else document(kind, 1, long=kind in ('radiology_draft', 'staged_radiology_copy'))
    source = {k.lower().replace(' ', '_'): v for k,v in base.fields}
    source['patient_name'] = 'Synthetic Patient'
    if kind == 'invoice':
        rows = [[f'Synthetic charge {i:02d}', '1', '100.00', '100.00'] for i in range(1,31)]
        source.update(total_amount='3000.00', amount_paid='500.00', outstanding_balance='2500.00', invoice_items=json.dumps(rows,separators=(',',':')), created_date='27 Nov 2026, 10:00 AM UTC+00:00')
    elif kind == 'appointment':
        source.update(assigned_staff_name='Synthetic Clinician')
    elif kind == 'lab_order':
        source.update(age_at_order='32 years', recorded_gender='female', ordered_by='Synthetic Clinician', ordered_tests=json.dumps([[f'Synthetic test {i:02d}','blood','Not recorded'] for i in range(1,26)],separators=(',',':')))
    elif kind in ('radiology_draft','staged_radiology_copy'):
        source.update(modality='ct',body_part='Chest',report_author='Synthetic Clinician',signature_status='Unsigned controlled copy')
    else:
        rows=tuple(tuple(json.dumps(v,ensure_ascii=False) for v in (f'Synthetic parameter {i:02d}','1234-5','7','mg/L','1 - 5','critical' if i%3==0 else 'normal','2030-01-01T10:00:00Z')) for i in range(1,26))
        brand=replace(brand,lab_rows=rows)
        source['report_text']=canonical_lab_text(rows)
    doc=build_document(kind=kind,clinic_id=1,record_id=base.record_id,source_version=base.source_version,source=source)
    brand=replace(brand,verification_url='https://verify.example.invalid/api/v1/printing/verify/'+'a'*32,issued_at='27 Nov 2026, 10:00 AM UTC+00:00')
    return doc, brand


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('primary,secondary', PALETTES, ids=['green-black','blue-brown'])
def test_run7_palette_matrix(kind, primary, secondary, tmp_path):
    doc, brand = create_case(kind)
    brand=replace(brand,primary_color=primary,secondary_color=secondary)
    plan, faces=make_plan(doc,brand)
    other=replace(brand,primary_color=secondary,secondary_color=primary)
    swapped,_=make_plan(doc,other)
    # Only the presentation colours may change, never text, placement or QR geometry.
    assert len(plan['pages'])==len(swapped['pages'])
    title='Laboratory Results' if kind=='released_lab_result' else doc.title
    table_kind=kind in ('invoice','lab_order','released_lab_result')
    section_label={'invoice':'Invoice Items','lab_order':'Ordered Tests','radiology_draft':'Report Text','released_lab_result':'Report Text','staged_radiology_copy':'Report Text'}.get(kind)
    table_count=section_count=critical_count=balance_count=0
    for page,alt in zip(plan['pages'],swapped['pages']):
        assert page['logo']==alt['logo'] and page['logo'] is not None
        assert len(page['rects'])==len(alt['rects']) and len(page['text'])==len(alt['text'])
        for left,right in zip(page['rects']+page['text'],alt['rects']+alt['text']):
            assert {k:v for k,v in left.items() if k!='color'}=={k:v for k,v in right.items() if k!='color'}
        texts=page['text'];rects=page['rects']
        assert any(t['text']=='Synthetic Alpha' and t['tag']=='header' and t['color']==primary for t in texts)
        assert any(t['text']=='Alpha HQ' and t['tag']=='header' and t['color']==secondary for t in texts)
        assert any(t['text']==title and t['tag']=='header' and t['color']==primary for t in texts)
        assert rects[0]['color']==primary and rects[0]['y']==45 and rects[0]['h']==4
        assert any(abs(r['y']-(H-FOOT_TOP))<.001 and abs(r['w']-WIDTH)<.001 and r['color']==primary for r in rects)
        assert any(t['tag']=='warning' and t['color']==WARN for t in texts)
        assert all(t['color']==WARN for t in texts if t['tag'] in ('warning','disclaimer'))
        assert any(r['color']==WARN_BG for r in rects)
        assert all(t['color']==INK for t in texts if t['text']=='Synthetic Patient')
        qr=[r for r in rects if r['y']>H-FOOT_TOP+1]
        assert len(qr)>50 and qr==[r for r in alt['rects'] if r['y']>H-FOOT_TOP+1]
        for t in texts:
            if t['text']==section_label:
                section_count+=1;assert t['color']==secondary
                assert any(r['color']==secondary and abs(r['h']-.7)<.001 and t['y']<r['y']<t['y']+20 for r in rects)
            if t['tag']=='table_header':
                table_count+=1;assert t['color']=='#FFFFFF'
                assert any(r['color']==secondary and r['h']>10 and r['y']<t['y']<r['y']+r['h'] for r in rects)
            if t['text']=='critical':critical_count+=1;assert t['color']==WARN
            if t['text']=='NGN 2,500.00':balance_count+=1;assert t['color']==WARN
    assert bool(table_count)==table_kind
    assert bool(section_count)==bool(section_label)
    assert bool(critical_count)==(kind=='released_lab_result')
    assert bool(balance_count)==(kind=='invoice')
    # The patient paths use the same plan with ReportLab; staff paths use Rust.
    actual=(py.render_pdf if kind in ('released_lab_result','staged_radiology_copy') else rust.render_pdf)(doc,branding=brand)
    ref=paint_reportlab(plan,faces,brand)
    for data in (actual,ref):
        colors=painted_rgb(data)
        assert all(has_rgb(colors,c) for c in (primary,secondary,WARN,INK))
    out=Path(os.environ.get('CSP_RUN7_EVIDENCE',str(tmp_path)))
    prefix=f'{kind}_{primary[1:]}_{secondary[1:]}'
    (out/(prefix+'.pdf')).write_bytes(actual)
    (out/(prefix+'.json')).write_text(json.dumps({'kind':kind,'primary':primary,'secondary':secondary,'pdf_sha256':sha256(actual).hexdigest(),'plan':plan},indent=2),encoding='utf-8')


def test_run7_fresh_http_palette(world, app, db, client, make_staff, make_patient, make_invoice, make_appointment, make_lab_test, make_lab_order, tmp_path):
    """Actual branding API -> PostgreSQL -> fresh Rust PDF endpoints."""
    from datetime import date, datetime, timezone
    from decimal import Decimal
    from app.tests.modules.clinic.test_print_branding import ORG, data
    from app.tests.modules.printing.test_hospital_grade_printing import actor
    from app.modules.billing.models.billing_model import InvoiceItem
    assert app.testing and db.engine.dialect.name=='postgresql'
    assert db.engine.url.host=='127.0.0.1' and db.engine.url.database=='csp_run7'
    app.config.update(RAD6_PRINTING_ENABLED=True, PRINT_BRANDING_STAGED_ENABLED=True, PRINT_HOSPITAL_GRADE_STAGED_ENABLED=True, PRINT_VERIFICATION_STAGED_ENABLED=False)
    w=world; ca=w['ca']; staff,headers=actor(w,db,make_staff)
    patient=make_patient(ca,first_name='Synthetic',last_name='Patient',date_of_birth=date(1990,1,1))
    invoice=make_invoice(ca,patient,invoice_number='RUN7-SYNTH',currency='NGN',total_amount=Decimal('20000.00'),amount_paid=Decimal('2500.00'))
    db.session.add(InvoiceItem(invoice_id=invoice.id,description='Synthetic care',quantity=2,unit_price=Decimal('10000.00'),subtotal=Decimal('20000.00')))
    appt=make_appointment(ca,patient,staff,scheduled_start=datetime(2030,1,1,10),scheduled_end=datetime(2030,1,1,10,30))
    lab=make_lab_order(ca,patient,staff,[make_lab_test(ca,name='Full Blood Count')],created_at=datetime(2030,1,1,9,tzinfo=timezone.utc))
    db.session.commit()
    rows=[('invoice',invoice.id),('appointment',appt.id),('lab_order',lab.id)]
    evidence={};out=Path(os.environ.get('CSP_RUN7_EVIDENCE',str(tmp_path)))
    previous={}
    for version,(primary,secondary) in enumerate(PALETTES):
        result=data(client.put(ORG+'/colors',headers=w['h'],json={'expected_version':version,'primary_color':primary,'secondary_color':secondary}))
        assert result['version']==version+1
        db.session.expire_all()
        read=data(client.get(ORG,headers=w['h']))
        assert read['effective_primary_color']==primary and read['effective_secondary_color']==secondary
        for kind,rid in rows:
            response=client.get(f'/api/v1/printing/{kind}/{rid}.pdf',headers=headers)
            assert response.status_code==200,(kind,response.status_code)
            color=painted_rgb(response.data)
            assert all(has_rgb(color,c) for c in (primary,secondary,WARN,INK))
            other_palette=PALETTES[1-version]
            assert all(not has_rgb(color,c) for c in other_palette)
            record={'source':response.headers['X-Source-SHA256'],'pdf':sha256(response.data).hexdigest()}
            if version:assert previous[kind]['source']==record['source'] and previous[kind]['pdf']!=record['pdf']
            else:previous[kind]=record
            label=f'http_{kind}_{primary[1:]}_{secondary[1:]}'
            evidence[label]=record;(out/(label+'.pdf')).write_bytes(response.data)
    (out/'fresh_http.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
