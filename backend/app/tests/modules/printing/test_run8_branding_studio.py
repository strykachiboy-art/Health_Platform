"""L05 Run8 backend: actual studio authority, native/reference colour proof."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import importlib.util
import json
import os
import subprocess
from uuid import uuid4

import pytest
from app.modules.clinic.services.branding_studio_palette import DEFAULTS, resolve, paint_color, validate_effective, paper_color
from app.modules.clinic.services import branding_service as svc
from app.modules.printing.services.professional_layout import make_plan, paint_reportlab, WARN, WARN_BG, INK
from app.modules.printing.services.print_policy import PrintDenied
from app.modules.printing.services import rust_print_bridge as rust, print_renderer as py
from app.tests.modules.clinic.test_print_branding import world, ORG, CLINIC, grant, data
from app.tests.modules.printing.test_run7_colour_contract import create_case, KINDS, painted_rgb, has_rgb

PALETTE = dict(primary_color='#136228', secondary_color='#8A4B26', text_color='#263D36',
               warning_color='#92400E', background_color='#FFF8E7', output_mode='full')
MODES = ('full', 'ink_saving', 'monochrome')


def put(client, scope, headers, version, org_version, overrides):
    return client.put(scope+'/studio', headers=headers, json={
        'expected_version':version, 'expected_organization_version':org_version, 'overrides':overrides})


def get(client, scope, headers):
    return data(client.get(scope+'/studio', headers=headers))


def test_studio_inheritance_reset_legacy_compat_and_scope(client, world, db):
    w=world
    initial=get(client, ORG, w['h']);assert initial['effective']==DEFAULTS and initial['local']=={}
    assert get(client, CLINIC, w['branch'])['effective']==DEFAULTS
    saved=data(put(client, ORG,w['h'],0,0,PALETTE));assert saved['version']==1 and saved['effective']==PALETTE
    assert get(client, CLINIC,w['branch'])['effective']==PALETTE
    assert get(client, CLINIC,w['other_h'])['effective']==DEFAULTS
    assert put(client,CLINIC,w['branch'],0,1,{'primary_color':'#1D4ED8'}).status_code==404
    assert data(grant(client,w,1))['version']==2
    local={'primary_color':'#1D4ED8','text_color':'#000000','output_mode':'ink_saving'}
    assert put(client,CLINIC,w['branch'],0,1,local).status_code==409
    branch=data(put(client,CLINIC,w['branch'],0,2,local));assert branch['version']==1
    assert branch['effective']=={**PALETTE,**local} and branch['sources']['warning_color']=='organization'
    assert branch['sources']['primary_color']=='clinic'
    # Reset only primary; the other two clinic overrides survive.
    local.pop('primary_color')
    branch=data(put(client,CLINIC,w['branch'],1,2,local));assert branch['effective']=={**PALETTE,**local}
    assert branch['sources']['primary_color']=='organization'
    # Old API can still update both legacy colours, without dropping new fields.
    old=data(client.put(ORG+'/colors',headers=w['h'],json={'expected_version':2,'primary_color':'#17354A','secondary_color':'#475569'}))
    assert old['version']==3 and 'studio' not in old
    updated={**PALETTE,'primary_color':'#17354A','secondary_color':'#475569'}
    assert get(client,ORG,w['h'])['effective']==updated
    old_read=data(client.get(CLINIC,headers=w['branch']));assert old_read['primary_color']==updated['primary_color'] and 'studio' not in old_read
    assert data(grant(client,w,3,False))['version']==4
    assert get(client,CLINIC,w['branch'])['effective']==updated
    assert put(client,CLINIC,w['branch'],2,4,{'primary_color':'#000000'}).status_code==404
    cleared=data(put(client,CLINIC,w['branch'],2,4,{}));assert cleared['local']=={} and cleared['effective']==updated
    assert data(grant(client,w,4))['version']==5
    assert get(client,CLINIC,w['branch'])['effective']==updated
    for h in (w['branch'],w['doc_h'],w['billing_h'],w['sa']):
        r=client.get(ORG+'/studio',headers=h);assert r.status_code in (403,404) and 'no-store' in r.headers['Cache-Control']
    assert client.get(CLINIC+'/studio',headers=w['doc_h']).status_code==403
    assert client.get(CLINIC+'/studio?clinic_id='+str(w['cc'].id),headers=w['h']).status_code==422
    assert data(put(client,ORG,w['h'],5,5,{}))['effective']==DEFAULTS
    assert get(client,CLINIC,w['branch'])['effective']==DEFAULTS


@pytest.mark.parametrize('overrides',[
    {'primary_color':'#39E380'}, {'secondary_color':'#FFFFFF'},
    {'text_color':'#666666'}, {'warning_color':'#136228'},
    {'warning_color':'#FF0000'}, {'background_color':'#000000'},
    {'output_mode':'invalid'}, {'primary_color':'red'}, {'unexpected':'#000000'},
    {'text_color':True}, {'primary_color':'#000000','background_color':'#EEEEEE','secondary_color':'#767676'},
])
def test_studio_invalid_palette_denied_without_write(client,world,overrides):
    r=put(client,ORG,world['h'],0,0,overrides);assert r.status_code==422,r.get_data(as_text=True)
    assert get(client,ORG,world['h'])['version']==0


def test_parent_change_and_entitlement_cannot_activate_unsafe_combination(client,world):
    w=world
    assert data(grant(client,w,0))['version']==1
    assert data(put(client,CLINIC,w['branch'],0,1,{'primary_color':'#767676'}))['version']==1
    assert put(client,ORG,w['h'],1,1,{'background_color':'#F2F2F2'}).status_code==422
    assert get(client,ORG,w['h'])['version']==1
    assert data(grant(client,w,1,False))['version']==2
    assert data(put(client,ORG,w['h'],2,2,{'background_color':'#F2F2F2'}))['version']==3
    assert grant(client,w,3).status_code==422
    assert get(client,CLINIC,w['branch'])['effective']['background_color']=='#F2F2F2'
    assert data(put(client,CLINIC,w['branch'],1,3,{}))['version']==2
    assert data(grant(client,w,3))['version']==4


def test_studio_gate_and_version_rejection(client,world,app):
    w=world
    assert put(client,ORG,w['h'],True,0,PALETTE).status_code==422
    assert put(client,ORG,w['h'],0,True,PALETTE).status_code==422
    app.config['PRINT_BRANDING_STAGED_ENABLED']=False
    assert client.get(ORG+'/studio',headers=w['h']).status_code==404
    app.config['PRINT_BRANDING_STAGED_ENABLED']=True
    app.config['TESTING']=False
    try: assert client.get(ORG+'/studio',headers=w['h']).status_code==404
    finally: app.config['TESTING']=True


@pytest.mark.parametrize('kind',KINDS)
@pytest.mark.parametrize('mode',MODES)
def test_studio_six_document_three_mode_matrix(kind,mode,tmp_path):
    doc,brand=create_case(kind)
    palette={**PALETTE,'output_mode':mode}
    brand=replace(brand,primary_color=palette['primary_color'],secondary_color=palette['secondary_color'],studio=palette)
    plan,faces=make_plan(doc,brand)
    for page in plan['pages']:
        assert any(t['text']=='Synthetic Alpha' and t['color']==palette['primary_color'] for t in page['text'])
        assert any(t['text']=='Alpha HQ' and t['color']==palette['secondary_color'] for t in page['text'])
        assert all(t['color']==palette['warning_color'] for t in page['text'] if t['tag'] in ('warning','disclaimer'))
        assert any(t['tag']=='warning' for t in page['text'])
        assert any(r['color']==WARN_BG for r in page['rects'])
        assert all(t['color']==palette['text_color'] for t in page['text'] if t['text']=='Synthetic Patient')
        assert all(t['color']==WARN for t in page['text'] if t['text'] in ('critical','NGN 2,500.00'))
        assert all(t['color']=='#FFFFFF' for t in page['text'] if t['tag']=='table_header')
        assert any(r['color']==INK for r in page['rects'] if r['y']>700) # QR ink remains fixed in plan
    actual=(py.render_pdf if kind in ('released_lab_result','staged_radiology_copy') else rust.render_pdf)(doc,branding=brand)
    reference=paint_reportlab(plan,faces,brand)
    for engine,pdf in (('actual',actual),('reference',reference)):
        observed=painted_rgb(pdf)
        if mode=='monochrome':
            assert all(abs(r-g)<1e-6 and abs(g-b)<1e-6 for r,g,b in observed)
            assert has_rgb(observed,'#000000') and has_rgb(observed,'#FFFFFF')
        else:
            assert all(has_rgb(observed,palette[key]) for key in ('primary_color','secondary_color','text_color','warning_color'))
            assert has_rgb(observed,palette['background_color'])==(mode=='full')
        if kind in ('invoice','released_lab_result') and mode!='monochrome':assert has_rgb(observed,WARN)
        out=Path(os.environ.get('CSP_RUN8_EVIDENCE',str(tmp_path)))
        (out/f'{kind}_{mode}_{engine}.pdf').write_bytes(pdf)


def test_native_studio_tamper_denied():
    from dataclasses import asdict
    from copy import deepcopy
    doc,brand=create_case('invoice');brand=replace(brand,primary_color=PALETTE['primary_color'],secondary_color=PALETTE['secondary_color'],studio=PALETTE)
    plan,_=make_plan(doc,brand);wire={**asdict(doc),'protocol':3,'branding':brand.wire(),'professional':plan}
    def invoke(payload):return subprocess.run([os.environ['CSP_RUST_PRINT_RENDERER']],input=json.dumps(payload,separators=(',',':')).encode(),capture_output=True,timeout=30)
    assert invoke(wire).stdout.startswith(b'%PDF-')
    for key,value in [('background_color','#000000'),('text_color','#FFFFFF'),('warning_color','#136228'),('output_mode','invalid')]:
        bad=deepcopy(wire);bad['branding']['studio'][key]=value
        result=invoke(bad);assert result.returncode!=0 and not result.stdout.startswith(b'%PDF-')
    bad=deepcopy(wire);next(t for t in bad['professional']['pages'][0]['text'] if t['tag']=='warning')['color']=PALETTE['text_color']
    assert invoke(bad).returncode!=0
    bad=deepcopy(wire);bad['branding']['studio']['arbitrary']='value';assert invoke(bad).returncode!=0
    bad=deepcopy(wire);bad['protocol']=2;assert invoke(bad).returncode!=0


def test_migration_preserves_legacy_and_denies_destructive_downgrade(app,db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    backend=Path(__file__).resolve().parents[4]
    path=backend/'migrations/versions/c8261128b004_branding_studio.py'
    spec=importlib.util.spec_from_file_location('run8_migration',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    assert db.engine.dialect.name=='postgresql' and db.engine.url.database=='csp_run8'
    schema='run8_migration_'+uuid4().hex
    with db.engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA '+schema));conn.execute(text('SET LOCAL search_path TO '+schema))
        for table in ('organization_branding','clinic_branding'):
            conn.execute(text(f'CREATE TABLE {table} (id integer primary key, primary_color varchar(7), secondary_color varchar(7))'))
            conn.execute(text(f"INSERT INTO {table} VALUES (1, '#17354A', '#475569')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            for table in ('organization_branding','clinic_branding'):
                assert conn.execute(text(f'SELECT primary_color, secondary_color, studio_palette FROM {table}')).one()==('#17354A','#475569',None)
            migration.downgrade();migration.upgrade()
            conn.execute(text("UPDATE organization_branding SET studio_palette = CAST(:v AS json)"),{'v':json.dumps(PALETTE)})
            with pytest.raises(RuntimeError):migration.downgrade()
        conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))


def test_fresh_http_five_color_pipeline(client,world,app,db,make_staff,make_patient,make_invoice,make_appointment,make_lab_test,make_lab_order,tmp_path):
    from datetime import date, datetime, timezone
    from decimal import Decimal
    from app.tests.modules.printing.test_hospital_grade_printing import actor
    from app.modules.billing.models.billing_model import InvoiceItem
    assert app.testing and db.engine.dialect.name=='postgresql' and db.engine.url.database=='csp_run8'
    app.config.update(RAD6_PRINTING_ENABLED=True,PRINT_BRANDING_STAGED_ENABLED=True,PRINT_HOSPITAL_GRADE_STAGED_ENABLED=True,PRINT_VERIFICATION_STAGED_ENABLED=False)
    w=world;ca=w['ca'];staff,headers=actor(w,db,make_staff)
    patient=make_patient(ca,first_name='Synthetic',last_name='Patient',date_of_birth=date(1990,1,1))
    inv=make_invoice(ca,patient,invoice_number='RUN8-SYNTH',currency='NGN',total_amount=Decimal('20000.00'),amount_paid=Decimal('2500.00'))
    db.session.add(InvoiceItem(invoice_id=inv.id,description='Synthetic care',quantity=2,unit_price=Decimal('10000.00'),subtotal=Decimal('20000.00')))
    appt=make_appointment(ca,patient,staff,scheduled_start=datetime(2030,1,1,10),scheduled_end=datetime(2030,1,1,10,30))
    lab=make_lab_order(ca,patient,staff,[make_lab_test(ca,name='Full Blood Count')],created_at=datetime(2030,1,1,9,tzinfo=timezone.utc));db.session.commit()
    rows=[('invoice',inv.id),('appointment',appt.id),('lab_order',lab.id)]
    out=Path(os.environ.get('CSP_RUN8_EVIDENCE',str(tmp_path)));previous={};summary={}
    for version,mode in enumerate(MODES):
        palette={**PALETTE,'output_mode':mode}
        saved=data(put(client,ORG,w['h'],version,version,palette));assert saved['effective']==palette
        db.session.expire_all();assert get(client,ORG,w['h'])['effective']==palette
        for kind,rid in rows:
            response=client.get(f'/api/v1/printing/{kind}/{rid}.pdf',headers=headers)
            assert response.status_code==200,(kind,response.status_code,response.get_data(as_text=True)[:300] if response.status_code!=200 else '')
            colors=painted_rgb(response.data)
            if mode=='monochrome':assert all(abs(r-g)<1e-6 and abs(g-b)<1e-6 for r,g,b in colors)
            else:
                assert all(has_rgb(colors,palette[k]) for k in ('primary_color','secondary_color','text_color','warning_color'))
                assert has_rgb(colors,palette['background_color'])==(mode=='full')
            record={'source':response.headers['X-Source-SHA256'],'pdf':sha256(response.data).hexdigest()}
            if version:assert previous[kind]['source']==record['source'] and previous[kind]['pdf']!=record['pdf']
            previous[kind]=record;summary[kind+'_'+mode]=record
            (out/f'http_{kind}_{mode}.pdf').write_bytes(response.data)
    (out/'fresh_http.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
