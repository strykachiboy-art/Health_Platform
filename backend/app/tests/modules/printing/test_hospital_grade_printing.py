"""Synthetic, exact-scope L05 R2E proof; never uses production records."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone, date
from decimal import Decimal
from hashlib import sha256
import json
import os
import subprocess

import pytest
from app.core.enums.role_enums import Role
from app.modules.clinic.models.organization_model import TenantMembership, ClinicMembership
from app.modules.printing.services import hospital_grade as grade, verification as verify
from app.modules.printing.services.print_policy import build_document, PrintDenied
from app.modules.printing.services.source_adapters import snapshot
from app.modules.printing.services.professional_layout import make_plan, paint_reportlab
from app.modules.printing.services.branding_payload import prepare_branding
from app.modules.printing.services.rust_print_bridge import render_pdf as native
from app.tests.modules.clinic.test_print_branding import world
from app.tests.modules.printing.test_branded_rendering import seed_logo, write_artifact


def activate(app):
    app.config.update(RAD6_PRINTING_ENABLED=True, PRINT_BRANDING_STAGED_ENABLED=True,
        PRINT_HOSPITAL_GRADE_STAGED_ENABLED=True, PRINT_VERIFICATION_STAGED_ENABLED=True,
        PRINT_VERIFICATION_ORIGIN='https://verify.example.invalid', PRINT_THERMAL_STAGED_ENABLED=True)


def captured_reservations(monkeypatch):
    reserved=[];original=verify.reserve
    def capture():
        value=original();reserved.append(value);return value
    monkeypatch.setattr(verify,'reserve',capture)
    return reserved


def receipt(client, r, reservation, name, tmp_path, expected=None):
    assert r.status_code==200, r.get_data(as_text=True)[:500] if r.status_code!=200 else ''
    token,url,issued=reservation;digest=sha256(r.data).hexdigest()
    path='/api/v1/printing/verify/'+token
    response=client.get(path)
    assert response.status_code==200 and 'no-store' in response.headers['Cache-Control']
    result=response.get_json()['data']
    assert result=={'issuance_record_found':True,'file_hash_match':None,'status':'historical_issuance_only','notice':verify.NOTICE}
    for candidate,match in [(digest,True),('0'*64,False),(r.headers['X-Source-SHA256'],False)]:
        response=client.post(path,json={'pdf_sha256':candidate})
        assert response.status_code==200 and response.get_json()['data']['file_hash_match'] is match
    assert client.get(path+'?hash='+digest).status_code==404
    assert client.post(path,json={'pdf_sha256':digest,'patient_id':1}).status_code==404
    meta={'verification_url':url,'issued_at':grade.stamp(issued),'pdf_sha256':digest,'source_sha256':r.headers['X-Source-SHA256']}
    if expected is not None:
        assert r.headers['X-Source-SHA256']==expected.source_digest
        meta['doc']=asdict(expected)
    write_artifact(tmp_path,name,r.data,meta)


def actor(world,db,make_staff,role=Role.ADMIN):
    ca=world['ca'];s=make_staff(ca,role=role,first_name='Synthetic',last_name='Clinician')
    db.session.add(TenantMembership(organization_id=ca.organization_id,user_id=s.user_id,role='member',status='active',is_active=True));db.session.flush()
    db.session.add(ClinicMembership(organization_id=ca.organization_id,user_id=s.user_id,clinic_id=ca.id,role=role.value,is_active=True));db.session.commit()
    return s,world['headers'](s.user,world['a'],ca)


def test_r2e_staff_http_sources(world,app,db,client,make_staff,make_patient,make_invoice,make_appointment,make_lab_test,make_lab_order,monkeypatch,tmp_path):
    from app.modules.billing.models.billing_model import InvoiceItem
    ca=world['ca'];staff,h=actor(world,db,make_staff);patient=make_patient(ca,first_name='Synthetic',last_name='Patient',date_of_birth=date(1990,1,1))
    inv=make_invoice(ca,patient,invoice_number='R2E-INVOICE',currency='NGN',total_amount=Decimal('20000.00'),amount_paid=Decimal('2500.00'))
    db.session.add(InvoiceItem(invoice_id=inv.id,description='Synthetic care',quantity=2,unit_price=Decimal('10000.00'),subtotal=Decimal('20000.00')))
    appt=make_appointment(ca,patient,staff,scheduled_start=datetime(2030,1,1,10),scheduled_end=datetime(2030,1,1,10,30))
    lab=make_lab_order(ca,patient,staff,[make_lab_test(ca,name='Full Blood Count'),make_lab_test(None,name='Malaria parasites')],created_at=datetime(2030,1,1,9,tzinfo=timezone.utc))
    db.session.commit();seed_logo(ca);activate(app);reserved=captured_reservations(monkeypatch)
    for kind,row in [('invoice',inv),('appointment',appt),('lab_order',lab)]:
        d=snapshot(kind=kind,record_id=row.id,clinic_id=ca.id,actor_role='ADMIN',actor_staff_id=staff.id);fields=dict(d.fields)
        assert fields['Patient Name']=='Synthetic Patient'
        if kind=='invoice':
            assert fields['Outstanding Balance']=='17500.00' and fields['Created Date']!='Not recorded'
        elif kind=='appointment':
            assert fields['Assigned Staff Name']=='Synthetic Clinician' and fields['Scheduled Start'].startswith('01 Jan 2030, 10:00 AM') and fields['Scheduled End'].startswith('01 Jan 2030, 10:30 AM')
        else:
            assert fields['Age At Order']=='40 years' and fields['Ordered By']=='Synthetic Clinician'
            assert json.loads(fields['Ordered Tests'])==[['Full Blood Count','blood','Not recorded'],['Malaria parasites','blood','Not recorded']]
        r=client.get(f'/api/v1/printing/{kind}/{row.id}.pdf',headers=h)
        receipt(client,r,reserved[-1],'r2e_real_'+kind,tmp_path,d)
    assert len(reserved)==3 and db.session.query(verify.PrintIssuance).count()==3
    assert len({r[0] for r in reserved})==3


def test_r2e_related_source_denials(world,app,db,client,make_staff,make_patient,make_appointment,make_lab_test,make_lab_order):
    ca,cc=world['ca'],world['cc'];staff,h=actor(world,db,make_staff)
    patient=make_patient(ca);foreign=make_patient(cc)
    appt=make_appointment(ca,foreign,staff)
    lab=make_lab_order(ca,patient,staff,[make_lab_test(cc,name='FOREIGN SECRET')])
    db.session.commit();activate(app)
    for kind,row in [('appointment',appt),('lab_order',lab)]:
        assert client.get(f'/api/v1/printing/{kind}/{row.id}.pdf',headers=h).status_code==404
    assert db.session.query(verify.PrintIssuance).count()==0


def test_r2e_issuance_failure_rolls_back(world,app,db,client,make_staff,make_patient,make_invoice,monkeypatch):
    from app.modules.printing.routes import printing_routes
    ca=world['ca'];staff,h=actor(world,db,make_staff)
    inv=make_invoice(ca,make_patient(ca),total_amount=Decimal('100.00'),amount_paid=Decimal('0.00'),currency='NGN');db.session.commit()
    activate(app)
    def fail(**kwargs):raise RuntimeError('Synthetic audit failure')
    monkeypatch.setattr(printing_routes,'create_audit_log',fail)
    # Test config may propagate or handle the exception. Neither may issue bytes.
    try:r=client.get(f'/api/v1/printing/invoice/{inv.id}.pdf',headers=h)
    except RuntimeError as exc:assert str(exc)=='Synthetic audit failure'
    else:assert r.status_code>=500 and not r.data.startswith(b'%PDF-')
    assert db.session.query(verify.PrintIssuance).count()==0


def test_r2e_verification_disabled_and_invalid(world,app,client):
    token='a'*32;path='/api/v1/printing/verify/'+token
    assert client.get(path).status_code==404
    activate(app)
    for invalid in ['x'*32,'a'*31,'A'*32,token]:assert client.get('/api/v1/printing/verify/'+invalid).status_code==404
    app.config['TESTING']=False
    try:assert client.get(path).status_code==404
    finally:app.config['TESTING']=True


def test_r2e_thermal_payment_http(world,app,db,client,make_staff,make_patient,make_invoice,tmp_path):
    from app.modules.billing.models.billing_model import Payment
    from app.core.enums.billing_enums import PaymentStatus,PaymentMethod
    ca,cc=world['ca'],world['cc'];staff,h=actor(world,db,make_staff)
    inv=make_invoice(ca,make_patient(ca),invoice_number='THERMAL-SYNTH',currency='NGN',total_amount=Decimal('100.00'),amount_paid=Decimal('40.00'))
    # The invoice can have a partial payment; it is not itself a receipt.
    foreign=make_invoice(cc,make_patient(cc),invoice_number='FOREIGN-THERMAL',currency='NGN',total_amount=Decimal('100.00'),amount_paid=Decimal('40.00'))
    paid=Payment(invoice_id=inv.id,amount=Decimal('40.00'),currency='NGN',method=PaymentMethod.CASH,status=PaymentStatus.SUCCESSFUL,paid_at=datetime.now(timezone.utc))
    other=Payment(invoice_id=foreign.id,amount=Decimal('40.00'),currency='NGN',method=PaymentMethod.CASH,status=PaymentStatus.SUCCESSFUL,paid_at=datetime.now(timezone.utc))
    db.session.add_all([paid,other]);db.session.commit();activate(app)
    path=lambda paper,id:f'/api/v1/printing/thermal/{paper}/payment/{id}.escpos'
    for paper,width in [(58,32),(80,48)]:
        r=client.get(path(paper,paid.id),headers=h);assert r.status_code==200 and r.data.startswith(b'\x1b@') and 'no-store' in r.headers['Cache-Control']
        lines=r.data[2:].decode('ascii').splitlines();assert max(map(len,lines))<=width
        assert 'NGN 40.00' in ' '.join(lines) and all(all(32<=ord(c)<=126 for c in line) for line in lines)
        (tmp_path/f'r2e_thermal_{paper}.escpos').write_bytes(r.data)
    assert client.get(path(80,other.id),headers=h).status_code==404
    assert client.get(path(76,paid.id),headers=h).status_code==404
    assert client.get(path(80,paid.id),headers=world['doc_h']).status_code==403
    for status in [PaymentStatus.PENDING,PaymentStatus.REFUNDED,PaymentStatus.FAILED]:
        paid.status=status;db.session.commit();assert client.get(path(80,paid.id),headers=h).status_code==404
    paid.status=PaymentStatus.SUCCESSFUL;db.session.commit();inv.invoice_number='INJECT\x1b@';db.session.commit()
    assert client.get(path(80,paid.id),headers=h).status_code==404


def test_r2e_ordered_test_schema_and_literal_report():
    assert grade.stamp(datetime(2030,1,1,10,30))=='01 Jan 2030, 10:30 AM (offset not recorded)'
    assert grade.stamp(datetime(2030,1,1,10,30,tzinfo=timezone.utc))=='01 Jan 2030, 10:30 AM UTC+00:00'
    source={'patient_id':1,'status':'ordered','created_at':'2030','sample_collected_at':None,'patient_name':'Synthetic Patient','age_at_order':'Not recorded','recorded_gender':'Not recorded','ordered_by':'Synthetic Doctor','ordered_tests':'[["FBC","blood","urgent"]]'}
    with pytest.raises(PrintDenied):build_document(kind='lab_order',clinic_id=1,record_id=1,source_version=0,source=source)
    source['ordered_tests']='[["FBC","blood","Not recorded"]]'
    assert 'Ordered Tests' in dict(build_document(kind='lab_order',clinic_id=1,record_id=1,source_version=0,source=source).fields)


def test_r2e_native_plan_tamper(world,app,db):
    ca=world['ca'];activate(app)
    source={'patient_id':1,'status':'ordered','created_at':'2030','sample_collected_at':'Not recorded','patient_name':'Synthetic Patient','age_at_order':'Not recorded','recorded_gender':'Not recorded','ordered_by':'Synthetic Doctor','ordered_tests':'[["FBC","blood","Not recorded"]]'}
    doc=build_document(kind='lab_order',clinic_id=ca.id,record_id=1,source_version=0,source=source)
    brand=replace(prepare_branding(doc,owning_clinic_id=ca.id),verification_url='https://verify.example.invalid/api/v1/printing/verify/'+'a'*32,issued_at='27 Nov 2026, 10:00 AM UTC+00:00')
    plan,_=make_plan(doc,brand);wire={**asdict(doc),'protocol':3,'branding':brand.wire(),'professional':plan}
    def invoke(p):return subprocess.run([os.environ['CSP_RUST_PRINT_RENDERER']],input=json.dumps(p,ensure_ascii=False,separators=(',',':')).encode('utf-8'),capture_output=True,timeout=30)
    assert invoke(wire).returncode==0
    variants=[]
    q=deepcopy(wire);q['professional']['pages'][0]['rects'].pop();variants.append(q)
    q=deepcopy(wire);q['professional']['pages'][0]['rects'][-1]['x']+=.5;variants.append(q)
    q=deepcopy(wire);q['branding']['verification_rows'][0]='1'+q['branding']['verification_rows'][0][1:];variants.append(q)
    q=deepcopy(wire);q['branding']['verification_url']=q['branding']['verification_url'][:-1]+'b';variants.append(q)
    q=deepcopy(wire);q['fields']=[list(v) for v in q['fields']];q['fields'][-1][1]='[["FBC","blood","urgent"]]';variants.append(q)
    q=deepcopy(wire);next(t for t in q['professional']['pages'][0]['text'] if t['tag']=='table_header')['text']='Untrusted';variants.append(q)
    for q in variants:
        r=invoke(q);assert r.returncode!=0 and not r.stdout.startswith(b'%PDF-')


def test_r2e_migration_nonempty_hold(world,app,db):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text
    path=Path(__file__).resolve().parents[4]/'migrations/versions/c8261127b003_print_issuance.py'
    spec=importlib.util.spec_from_file_location('issuance_migration_probe',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    assert db.session.query(verify.PrintIssuance).count()==0
    ca_id=world['ca'].id;db.session.commit();db.session.remove()
    # Fixture create_all already registered the model. Test the migration on
    # the disposable database only and leave the table present for teardown.
    with db.engine.begin() as conn:
        verify.PrintIssuance.__table__.drop(conn)
        with Operations.context(MigrationContext.configure(conn)):
            m.upgrade();assert 'print_issuances' in inspect(conn).get_table_names()
            conn.execute(verify.PrintIssuance.__table__.insert().values(token_sha256='a'*64,clinic_id=ca_id,kind='invoice',record_id=1,source_version=0,source_sha256='b'*64,pdf_sha256='c'*64,issued_at=datetime.now(timezone.utc)))
            with pytest.raises(RuntimeError,match='Nonempty'):m.downgrade()
            assert conn.execute(text('SELECT COUNT(*) FROM print_issuances')).scalar_one()==1
            conn.execute(text('DELETE FROM print_issuances'));m.downgrade();assert 'print_issuances' not in inspect(conn).get_table_names();m.upgrade()


def test_r2e_stress_pdf_matrix(tmp_path):
    from app.tests.modules.printing.test_professional_layout import fixture
    from app.tests.modules.printing.test_branded_rendering import document
    from app.modules.printing.services.polished_patient import canonical_lab_text
    invoice,brand=fixture()
    url='https://verify.example.invalid/api/v1/printing/verify/'+'a'*32
    brand=replace(brand,verification_url=url,issued_at='27 Nov 2026, 10:00 AM UTC+00:00')
    extras={'patient_name':'Synthetic Patient Cafe'}
    for kind in ('invoice','appointment','lab_order','radiology_draft','released_lab_result','staged_radiology_copy'):
        b=brand
        d0=invoice if kind=='invoice' else document(kind,1,long=True)
        source={k.lower().replace(' ','_'):v for k,v in d0.fields};source.update(extras)
        if kind=='invoice':
            rows=[[f'Synthetic charge {i:02d} Consultation and laboratory services','1','100.00','100.00'] for i in range(1,61)]
            source.update(total_amount='6000.00',amount_paid='500.00',outstanding_balance='5500.00',invoice_items=json.dumps(rows,separators=(',',':')),created_date='27 Nov 2026, 10:00 AM UTC+00:00')
        elif kind=='appointment':
            source.update(assigned_staff_name='Synthetic Clinician',scheduled_start='01 Jan 2030, 10:00 AM (offset not recorded)',scheduled_end='01 Jan 2030, 10:30 AM (offset not recorded)')
        elif kind=='lab_order':
            rows=[[f'Synthetic ordered test {i:02d} with a long laboratory parameter name','blood','Not recorded'] for i in range(1,41)]
            source.update(age_at_order='32 years',recorded_gender='female',ordered_by='Synthetic Clinician',ordered_tests=json.dumps(rows,separators=(',',':')))
        elif kind in ('radiology_draft','staged_radiology_copy'):
            source.update(modality='ct',body_part='Chest',report_author='Synthetic Clinician',signature_status='Unsigned controlled copy')
        else:
            rows=tuple(tuple(json.dumps(v,ensure_ascii=False) for v in (f'Synthetic parameter {i:02d}','1234-5','7','mg/L','1 - 5','critical' if i%3==0 else 'normal','2030-01-01T10:00:00Z')) for i in range(1,41))
            b=replace(b,lab_rows=rows);source['report_text']=canonical_lab_text(rows)
        doc=build_document(kind=kind,clinic_id=1,record_id=d0.record_id,source_version=d0.source_version,source=source)
        plan,faces=make_plan(doc,b);reference=paint_reportlab(plan,faces,b)
        for page in plan['pages']:
            assert any(t['tag']=='warning' for t in page['text']) and any(t['tag']=='disclaimer' for t in page['text'])
            assert any(t['text']=='Powered by Stryka-Max' for t in page['text'])
        if kind=='invoice':
            assert any(t['text']=='NGN 5,500.00' and t['bold'] and t['color']=='#842020' for t in plan['pages'][-1]['text'])
            assert any(t['tag']=='table_header' for t in plan['pages'][-1]['text'])
        if kind=='radiology_draft':assert '<b>literal</b>' in dict(doc.fields)['Report Text']
        data=reference if kind in ('released_lab_result','staged_radiology_copy') else native(doc,branding=b)
        name='r2e_stress_'+kind
        write_artifact(tmp_path,name,data,{'doc':asdict(doc),'verification_url':url,'plan':plan,'reference_sha256':sha256(reference).hexdigest(),'pdf_sha256':sha256(data).hexdigest()})
        if kind not in ('released_lab_result','staged_radiology_copy'):
            (tmp_path/(name+'_reference.pdf')).write_bytes(reference)
