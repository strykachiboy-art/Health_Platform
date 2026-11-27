"""Staged professional printing: real memberships, deterministic colors and source proof."""
from dataclasses import asdict, replace
from hashlib import sha256
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from app.extensions import db
from app.core.exceptions import ValidationError, ConflictError, NotFoundError
from app.modules.clinic.models.branding_model import OrganizationBranding
from app.modules.clinic.services import branding_service as svc
from app.modules.printing.services.branding_payload import prepare_branding, render_authorized
from app.modules.printing.services.print_policy import build_document, PrintDenied
from app.modules.printing.services import rust_print_bridge as rust, print_renderer as py
from app.modules.printing.services.polished_patient import canonical_lab_text
from app.tests.modules.clinic.test_print_branding import world, ORG, CLINIC, upload, grant, data
from app.tests.modules.printing.test_branded_rendering import document, logo, brand_meta, write_artifact


def colors(client,path,h,v,p,s):
    return client.put(path+'/colors',headers=h,json={'expected_version':v,'primary_color':p,'secondary_color':s})


def test_palette_http_inheritance_override_and_revocation(client,world):
    w=world
    first=data(client.get(CLINIC,headers=w['branch']))
    assert first['palette_source']=='default'
    assert colors(client,ORG,w['tenant_only'],0,'#176b65','#283548').status_code==200
    inherited=data(client.get(CLINIC,headers=w['branch']))
    assert (inherited['primary_color'],inherited['secondary_color'],inherited['palette_source'])==('#176B65','#283548','organization')
    assert inherited['logo_source']=='none' and inherited['branding_sha256']!=first['branding_sha256']
    assert colors(client,CLINIC,w['branch'],0,'#214E91','#762A83').status_code==404
    assert data(grant(client,w,1))['version']==2
    assert colors(client,CLINIC,w['branch'],0,'#214E91','#762A83').status_code==200
    own=data(client.get(CLINIC,headers=w['branch']))
    assert own['palette_source']=='clinic' and own['primary_color']=='#214E91' and own['clinic_primary_color']=='#214E91'
    assert data(client.get(CLINIC,headers=w['h']))['primary_color']=='#176B65'
    assert data(client.get(CLINIC,headers=w['other_h']))['palette_source']=='default'
    assert colors(client,CLINIC,w['branch'],0,'#333333','#444444').status_code==409
    assert data(grant(client,w,2,False))['version']==3
    revoked=data(client.get(CLINIC,headers=w['branch']))
    assert revoked['palette_source']=='organization' and revoked['primary_color']=='#176B65' and revoked['branding_sha256']!=own['branding_sha256']
    assert colors(client,CLINIC,w['branch'],1,None,None).status_code==200
    assert data(grant(client,w,3,True))['version']==4
    assert data(client.get(CLINIC,headers=w['branch']))['palette_source']=='organization'
    assert colors(client,ORG,w['h'],4,None,None).status_code==200
    assert data(client.get(CLINIC,headers=w['branch']))['palette_source']=='default'


@pytest.mark.parametrize('p,s', [('#FFFFFF','#17354A'),('#17354A','#FFFF00'),('#123','#456'),('url(x)','#17354A'),('#17354A',None),(True,'#17354A'),('#17354A','#12345G'),('#17354A','#17354A\n')])
def test_palette_rejects_unsafe_or_partial(p,s):
    with pytest.raises(ValidationError): svc.validate_palette(p,s)


def test_palette_authority_audit_and_no_store(client,world,monkeypatch):
    w=world
    for h in (w['branch'],w['doc_h'],w['billing_h'],w['sa']):
        r=colors(client,ORG,h,0,'#214E91','#283548'); assert r.status_code in (403,404)
        assert 'no-store' in r.headers['Cache-Control']
    assert colors(client,CLINIC,w['doc_h'],0,'#214E91','#283548').status_code==403
    r=client.put(ORG+'/colors?organization_id='+str(w['b'].id),headers=w['h'],json={'expected_version':0,'primary_color':'#214E91','secondary_color':'#283548'})
    assert r.status_code==422
    r=client.put(ORG+'/colors',headers=w['h'],json={'expected_version':0,'primary_color':'#214E91','secondary_color':'#283548','organization_id':str(w['b'].id)})
    assert r.status_code==422
    assert db.session.query(OrganizationBranding).count()==0
    def fail(**kwargs): raise RuntimeError('Synthetic audit unavailable')
    with monkeypatch.context() as m:
        m.setattr(svc,'create_audit_log',fail)
        with pytest.raises(RuntimeError): svc.change_colors(user_id=w['owner'].id,organization_id=w['a'].id,expected_version=0,primary_color='#214E91',secondary_color='#283548')
    assert db.session.query(OrganizationBranding).count()==0
    r=colors(client,ORG,w['h'],0,'#214E91','#283548');assert r.status_code==200 and 'no-store' in r.headers['Cache-Control']
    from app.core.audit.models.audit_model import AuditLog
    a=db.session.query(AuditLog).filter_by(entity_type='OrganizationBranding',description='Branding colors updated').one()
    assert a.new_value['primary_color']=='#214E91' and a.new_value['secondary_color']=='#283548'
    assert a.new_value['organization_id']==str(w['a'].id)


def test_palette_postgres_race(world,app):
    assert db.engine.dialect.name=='postgresql'
    w=world; oid=w['a'].id; uid=w['owner'].id
    db.session.remove(); barrier=Barrier(2,timeout=15)
    def attempt(p):
        with app.app_context():
            barrier.wait()
            try:
                svc.change_colors(user_id=uid,organization_id=oid,expected_version=0,primary_color=p,secondary_color='#283548')
                return 'written'
            except ConflictError:return 'conflict'
            finally:db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,['#214E91','#762A83']))==['conflict','written']
    assert db.session.query(OrganizationBranding).count()==1


def test_structured_lab_source_mismatch_denied(world):
    d=document('released_lab_result',world['ca'].id)
    with pytest.raises(PrintDenied,match='Structured laboratory source mismatch'):
        render_authorized(d,py.render_pdf,owning_clinic_id=d.clinic_id,lab_rows=(('A','B','C','D','E','F','G'),))


def test_rust_rejects_invalid_palette_and_invoice_rows(world):
    d=document('invoice',world['ca'].id);b=prepare_branding(d,owning_clinic_id=d.clinic_id)
    wire={**asdict(d),'protocol':2,'branding':b.wire()}
    def denied(p):
        r=subprocess.run([os.environ['CSP_RUST_PRINT_RENDERER']],input=json.dumps(p).encode(),capture_output=True,timeout=20)
        assert r.returncode!=0 and not r.stdout.startswith(b'%PDF-')
    for color in ('#FFFFFF','invalid','#176b65'):
        denied({**wire,'branding':{**wire['branding'],'primary_color':color}})
    for rows in ('not-json','[["A","0","1.00","1.00"]]','[["A","2","-1.00","2.00"]]'):
        denied({**wire,'fields':[*d.fields,['Invoice Items',rows],['Outstanding Balance','0.00']]})


def test_polished_document_matrix(world,app,tmp_path):
    w=world; a,b,ca,cb,cc=[w[k] for k in ('a','b','ca','cb','cc')]
    for clinic,c in ((ca,'#176B65'),(cb,'#214E91'),(cc,'#762A83')):
        clinic.address='Synthetic document address';clinic.city='Synthetic City';clinic.country='Nigeria';clinic.phone='08000000000'
    db.session.commit()
    svc.change_logo(user_id=w['owner'].id,organization_id=a.id,expected_version=0,raw=logo('#176B65'),mime='image/png')
    svc.change_colors(user_id=w['owner'].id,organization_id=a.id,expected_version=1,primary_color='#176B65',secondary_color='#283548')
    svc.set_override_entitlement(user_id=w['platform'].id,organization_id=a.id,expected_version=2,allowed=True)
    svc.change_logo(user_id=w['admin'].id,organization_id=a.id,clinic_id=cb.id,expected_version=0,raw=logo('#214E91',(90,320)),mime='image/png')
    svc.change_colors(user_id=w['admin'].id,organization_id=a.id,clinic_id=cb.id,expected_version=1,primary_color='#214E91',secondary_color='#762A83')
    svc.change_logo(user_id=w['other'].id,organization_id=b.id,expected_version=0,raw=logo('#762A83',(200,200)),mime='image/png')
    svc.change_colors(user_id=w['other'].id,organization_id=b.id,expected_version=1,primary_color='#762A83',secondary_color='#475569')
    def dump(name,doc,clinic,rows=None,correction=None):
        p=prepare_branding(doc,owning_clinic_id=clinic.id)
        snap=svc.resolve_branding(owning_clinic_id=clinic.id)
        engine=py.render_pdf if doc.kind in ('released_lab_result','staged_radiology_copy') else rust.render_pdf
        pdf,audit=render_authorized(doc,engine,owning_clinic_id=clinic.id,lab_rows=rows,correction=correction)
        assert audit['pdf_sha256']==sha256(pdf).hexdigest() and audit['branding_sha256']==snap.branding_sha256
        bm=brand_meta(p);bm.update(primary_color=p.primary_color,secondary_color=p.secondary_color,metadata=dict(snap.metadata))
        write_artifact(tmp_path,'l05r2b_'+name,pdf,{'doc':asdict(doc),'branding':bm,'audit':audit,'lab_rows':rows,'correction':correction})
        return p
    def invoice(clinic):
        return build_document(kind='invoice',clinic_id=clinic.id,record_id=90,source_version=0,source={'invoice_number':'POLISH-SYNTHETIC','patient_id':11,'status':'issued','currency':'NGN','total_amount':'20000.00','amount_paid':'2500.00','invoice_items':json.dumps([['Consultation Café','2','5000.00','10000.00'],['Synthetic laboratory services','1','10000.00','10000.00']],ensure_ascii=False,separators=(',',':')),'outstanding_balance':'17500.00'})
    first=dump('invoice',invoice(ca),ca);assert first.primary_color=='#176B65'
    # Many bounded rows exercise a real multi-page invoice table, with a long description.
    long_rows=[['Synthetic item '+str(i)+' '+('W'*80 if i%8==0 else 'Café'),'1','10.00','10.00'] for i in range(1,61)]
    long_invoice=build_document(kind='invoice',clinic_id=ca.id,record_id=91,source_version=0,source={'invoice_number':'POLISH-MULTI','patient_id':11,'status':'issued','currency':'NGN','total_amount':'600.00','amount_paid':'100.00','invoice_items':json.dumps(long_rows,ensure_ascii=False,separators=(',',':')),'outstanding_balance':'500.00'})
    dump('invoice_stress',long_invoice,ca)
    assert dump('appointment',document('appointment',cb.id),cb).primary_color=='#214E91'
    assert dump('lab_order',document('lab_order',cc.id),cc).primary_color=='#762A83'
    dump('draft',document('radiology_draft',cb.id,long=True),cb)
    rows=tuple(tuple(json.dumps(v,ensure_ascii=False) for v in (f'Synthetic Café {i} - ‐ −','2345-7','5.20','μg/L','≥ 1; ≤ 10 µg/L','normal' if i%2 else 'high','2030-01-01T10:00:00+00:00')) for i in range(1,41))
    correction=json.dumps('2030-01-01T11:00:00+00:00')
    text=canonical_lab_text(rows,correction)
    doc=build_document(kind='released_lab_result',clinic_id=ca.id,record_id=99,source_version=2,source={'patient_id':11,'verified_at':'2030-01-01','completed_at':'2030-01-01','released_at':'2030-01-01','report_text':text})
    dump('released_lab_table',doc,ca,rows,correction)
    dump('staged_radiology',document('staged_radiology_copy',cb.id,long=True),cb)
    svc.set_override_entitlement(user_id=w['platform'].id,organization_id=a.id,expected_version=3,allowed=False)
    assert dump('revoked',invoice(cb),cb).primary_color=='#176B65'


def test_palette_corruption_denies_print(world):
    w=world;svc.change_colors(user_id=w['owner'].id,organization_id=w['a'].id,expected_version=0,primary_color='#176B65',secondary_color='#283548')
    row=db.session.query(OrganizationBranding).filter_by(organization_id=w['a'].id).one();row.primary_color='#FFFFFF';db.session.commit()
    d=document('invoice',w['ca'].id)
    with pytest.raises(PrintDenied):prepare_branding(d,owning_clinic_id=d.clinic_id)


def test_invoice_extension_requires_canonical_items_and_reconciled_amounts():
    d={'invoice_number':'SYNTHETIC','patient_id':11,'status':'issued','currency':'NGN','total_amount':'100.00','amount_paid':'25.00','invoice_items':'[["Synthetic","2","50.00","100.00"]]','outstanding_balance':'75.00'}
    def build(src):return build_document(kind='invoice',clinic_id=1,record_id=1,source_version=0,source=src)
    a=build(d);assert dict(a.fields)['Outstanding Balance']=='75.00'
    b=build({**d,'invoice_items':'[["Changed","2","50.00","100.00"]]'})
    assert a.source_digest!=b.source_digest
    for changes in ({'invoice_items':'[]x'},{'invoice_items':'[["Synthetic", "2", "50.00", "100.00"]]'},{'invoice_items':'[["Synthetic","2","50.00","99.00"]]'},{'outstanding_balance':'76.00'},{'invoice_items':'[["Synthetic","0","50.00","100.00"]]'}):
        with pytest.raises(PrintDenied):build({**d,**changes})
