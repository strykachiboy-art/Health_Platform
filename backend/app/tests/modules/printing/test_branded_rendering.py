"""Synthetic cross-tenant document matrix, real renderers and no external data."""
from dataclasses import asdict, replace
from hashlib import sha256
from io import BytesIO
import json
import os
import subprocess
from pathlib import Path
import pytest
from PIL import Image, ImageDraw
from app.extensions import db
from app.modules.clinic.models.branding_model import OrganizationBranding
from app.modules.clinic.services import branding_service as svc
from app.modules.printing.services.branding_payload import prepare_branding, render_authorized, brand_lines
from app.modules.printing.services.print_policy import build_document, PrintDenied
from app.modules.printing.services import rust_print_bridge as rust, print_renderer as py
from app.tests.modules.clinic.test_print_branding import world


def logo(color, size=(320,120)):
    im=Image.new('RGBA',size,(0,0,0,0)); draw=ImageDraw.Draw(im)
    draw.rectangle((size[0]//8,size[1]//8,7*size[0]//8,7*size[1]//8),fill=color)
    draw.line((size[0]//4,size[1]//2,3*size[0]//4,size[1]//2),fill='white',width=max(2,size[1]//10))
    out=BytesIO();im.save(out,format='PNG');return out.getvalue()


def seed_logo(clinic, color='#176B65'):
    """Direct fixture data only. Management authorization tested in Run 1."""
    raw=svc.normalize_logo(logo(color),'image/png')
    db.session.add(OrganizationBranding(organization_id=clinic.organization_id,version=1,logo_png=raw,logo_sha256=sha256(raw).hexdigest()))
    db.session.commit()


def write_artifact(tmp_path, name, data, meta):
    assert data.startswith(b'%PDF-') and data.rstrip().endswith(b'%%EOF') and len(data)<=3*1024*1024
    (tmp_path/(name+'.pdf')).write_bytes(data)
    (tmp_path/(name+'.json')).write_text(json.dumps(meta,ensure_ascii=True,indent=2),encoding='utf-8')


def brand_meta(b):
    return {'lines':list(brand_lines(b)), 'branding_sha256':b.branding_sha256,'logo_sha256':b.logo_sha256,
            'logo_source':b.logo_source,'width':b.width,'height':b.height,
            'rgb_sha256':sha256(b.rgb).hexdigest() if b.rgb is not None else None}


def document(kind, cid, *, long=False):
    common={'patient_id':11}; version=1
    sources={
        'invoice':{**common,'invoice_number':'SYNTHETIC-BRAND-INVOICE','status':'issued','currency':'NGN','total_amount':'100.00','amount_paid':'0.00'},
        'appointment':{**common,'assigned_staff_id':2,'status':'scheduled','scheduled_start':'2030-01-01T10:00:00','scheduled_end':'2030-01-01T10:30:00'},
        'lab_order':{**common,'status':'pending','created_at':'2030-01-01','sample_collected_at':None},
        'radiology_draft':{'imaging_order_id':1,'accession_number':'SYNTHETIC-DRAFT','report_revision':1,'report_text':('Café - ‐ − μ µ ≥ ≤ Ω <b>literal</b> '*(120 if long else 1))+'END-DRAFT'},
        'released_lab_result':{**common,'verified_at':'2030-01-01','completed_at':'2030-01-01','released_at':'2030-01-01','report_text':'\n\n'.join(f'{i}. Test: Synthetic Café - ‐ −\nResult: 5 μg/L\nReference range: ≥ 1; ≤ 10 µg/L\nFlag: normal <b>literal</b>' for i in range(1,41 if long else 3))+'\nEND-LAB'},
        'staged_radiology_copy':{**common,'released_at':'2030-01-01','report_text':'SYNTHETIC non-diagnostic.\n'+('Finding <b>literal</b> '+ 'W'*100+'\n')*(55 if long else 1)+'END-RADIOLOGY'},
    }
    if kind in ('invoice','appointment','lab_order'):version=0
    return build_document(kind=kind,clinic_id=cid,record_id=99,source_version=version,source=sources[kind])


def test_l05r2_cross_clinic_render_matrix(world, app, tmp_path, make_clinic):
    w=world; a,b,ca,cb,cc=[w[k] for k in ('a','b','ca','cb','cc')]
    ca.name='Alpha HQ'; ca.address='12 Synthetic Way'; ca.city='Port Harcourt'; ca.country='Nigeria'; ca.phone='08000000000'; ca.email='alpha@example.invalid'
    cb.name='Alpha Branch'; cb.address='Branch Road'; cc.name='Beta HQ'; cc.address='Beta Road'
    # Exercise long text and non-ASCII header without changing radiology body restrictions.
    b.name='Synthetic Beta Café'
    db.session.commit()
    svc.change_logo(user_id=w['owner'].id,organization_id=a.id,expected_version=0,raw=logo('#176B65'),mime='image/png')
    svc.set_override_entitlement(user_id=w['platform'].id,organization_id=a.id,expected_version=1,allowed=True)
    svc.change_logo(user_id=w['admin'].id,organization_id=a.id,clinic_id=cb.id,expected_version=0,raw=logo('#235CA0',(90,320)),mime='image/png')
    svc.change_logo(user_id=w['other'].id,organization_id=b.id,expected_version=0,raw=logo('#A34123',(200,200)),mime='image/png')
    from app.modules.clinic.models.organization_model import HospitalOrganization
    org=HospitalOrganization(name='Synthetic text only '+ 'W'*100);db.session.add(org);db.session.flush()
    no_logo=make_clinic(organization_id=org.id,name='Text Only Clinic',address='W'*240);db.session.commit()
    cases=[('invoice_inherited','invoice',ca,'organization',False),('appointment_override','appointment',cb,'clinic',False),
           ('lab_order_foreign','lab_order',cc,'organization',False),('invoice_no_logo','invoice',no_logo,'none',False),
           ('draft_stress','radiology_draft',cb,'clinic',True),('lab_stress','released_lab_result',ca,'organization',True),
           ('lab_foreign','released_lab_result',cc,'organization',False),('rad_stress','staged_radiology_copy',cb,'clinic',True),
           ('rad_no_logo','staged_radiology_copy',no_logo,'none',False)]
    seen={}
    def proof(name,kind,clinic,source,long):
        doc=document(kind,clinic.id,long=long); brand=prepare_branding(doc,owning_clinic_id=clinic.id)
        assert brand.logo_source==source
        engine=py.render_pdf if kind in ('released_lab_result','staged_radiology_copy') else rust.render_pdf
        data,audit=render_authorized(doc,engine,owning_clinic_id=clinic.id)
        assert audit['pdf_sha256']==sha256(data).hexdigest() and audit['branding_sha256']==brand.branding_sha256
        assert audit['logo_sha256']==brand.logo_sha256
        write_artifact(tmp_path,'l05r2_'+name,data,{'doc':asdict(doc),'branding':brand_meta(brand),'audit':audit})
        seen[name]=brand
    for args in cases:proof(*args)
    assert seen['invoice_inherited'].logo_sha256==seen['lab_stress'].logo_sha256
    assert seen['appointment_override'].logo_sha256==seen['rad_stress'].logo_sha256
    assert len({seen[k].logo_sha256 for k in ('invoice_inherited','appointment_override','lab_order_foreign')})==3
    svc.set_override_entitlement(user_id=w['platform'].id,organization_id=a.id,expected_version=2,allowed=False)
    proof('invoice_revoked','invoice',cb,'organization',False)
    proof('lab_revoked','released_lab_result',cb,'organization',False)
    assert seen['invoice_revoked'].logo_sha256==seen['invoice_inherited'].logo_sha256==seen['lab_revoked'].logo_sha256
    assert seen['invoice_revoked'].branding_sha256!=seen['invoice_inherited'].branding_sha256
    d=document('invoice',ca.id)
    with pytest.raises(PrintDenied):prepare_branding(d,owning_clinic_id=cc.id)
    for engine in (rust.render_pdf,py.render_pdf):
        with pytest.raises(PrintDenied):engine(d,branding=seen['appointment_override'])
    row=db.session.query(OrganizationBranding).filter_by(organization_id=a.id).one();row.logo_sha256='0'*64;db.session.commit()
    with pytest.raises(PrintDenied):render_authorized(d,rust.render_pdf,owning_clinic_id=ca.id)


def test_l05r2_rust_protocol_negatives(world):
    w=world;doc=document('invoice',w['ca'].id)
    brand=prepare_branding(doc,owning_clinic_id=doc.clinic_id)
    d=asdict(doc);d.pop('clinic_id');d.update(protocol=2,clinic_id=doc.clinic_id,branding=brand.wire())
    def denied(payload):
        result=subprocess.run([os.environ['CSP_RUST_PRINT_RENDERER']],input=json.dumps(payload).encode(),capture_output=True,timeout=20)
        assert result.returncode!=0 and not result.stdout.startswith(b'%PDF-')
    denied({**d,'protocol':1})
    denied({**d,'branding':None})
    denied({**d,'branding':{**d['branding'],'clinic_id':w['cc'].id}})
    denied({**d,'branding':{**d['branding'],'logo':{'width':257,'height':1,'rgb_hex':'00'}}})
    denied({**d,'branding':{**d['branding'],'logo':{'width':1,'height':1,'rgb_hex':'xxxxxx'}}})
    denied({**d,'branding':{**d['branding'],'lines':['Organisation: A\nForged','Clinic: B']}})
    denied({**d,'is_signed':True})
    denied({**d,'branding':{**d['branding'],'logo_path':'C:/secret.png'}})


def test_l05r2_disabled_legacy_and_non_testing(world,app):
    d=document('invoice',world['ca'].id)
    app.config['PRINT_BRANDING_STAGED_ENABLED']=False
    assert prepare_branding(d,owning_clinic_id=d.clinic_id) is None
    app.config['PRINT_BRANDING_STAGED_ENABLED']=True;app.config['TESTING']=False
    try:
        with pytest.raises(PrintDenied):prepare_branding(d,owning_clinic_id=d.clinic_id)
    finally:app.config['TESTING']=True
