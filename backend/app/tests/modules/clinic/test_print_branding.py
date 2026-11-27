"""Synthetic branding only: explicit real memberships and signed JWTs."""
from io import BytesIO
from hashlib import sha256
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from flask_jwt_extended import create_access_token
from PIL import Image, PngImagePlugin
from sqlalchemy.exc import IntegrityError
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, ValidationError, NotFoundError
from app.core.audit.models.audit_model import AuditLog
from app.modules.clinic.models.organization_model import HospitalOrganization, TenantMembership, ClinicMembership
from app.modules.clinic.models.branding_model import OrganizationBranding, ClinicBranding
from app.modules.clinic.services import branding_service as svc

ORG = '/api/v1/clinics/branding/organization'
CLINIC = '/api/v1/clinics/branding/clinic'


def png(color='red', size=(40, 20), fmt='PNG'):
    f = BytesIO()
    Image.new('RGB', size, color).save(f, format=fmt)
    return f.getvalue()


@pytest.fixture
def world(app, db, make_clinic, make_user):
    app.config['PRINT_BRANDING_STAGED_ENABLED'] = True
    a = HospitalOrganization(id=uuid4(), name='Synthetic Alpha')
    b = HospitalOrganization(id=uuid4(), name='Synthetic Beta')
    db.session.add_all([a, b]); db.session.flush()
    ca = make_clinic(organization_id=a.id, name='Alpha HQ', address='Synthetic 1')
    cb = make_clinic(organization_id=a.id, name='Alpha branch', address='Synthetic 2')
    cc = make_clinic(organization_id=b.id, name='Beta HQ')
    owner = make_user(ca, role=Role.ADMIN)
    admin = make_user(cb, role=Role.ADMIN)
    other = make_user(cc, role=Role.ADMIN)
    doctor = make_user(ca, role=Role.DOCTOR)
    billing = make_user(ca, role=Role.ACCOUNTANT)
    platform = make_user(role=Role.SUPER_ADMIN)
    for user, org, role, clinics in [(owner,a,'organization_admin',[ca,cb]),(admin,a,'member',[cb]),(other,b,'organization_admin',[cc]),(doctor,a,'member',[ca]),(billing,a,'billing_admin',[ca])]:
        db.session.add(TenantMembership(user_id=user.id, organization_id=org.id, role=role, is_active=True,status='active'))
        db.session.flush()
        for c in clinics:
            db.session.add(ClinicMembership(user_id=user.id, organization_id=org.id, clinic_id=c.id, role=user.role.value,is_active=True))
    db.session.commit()
    def headers(user, org=None, clinic=None):
        claims = {'role':user.role.value,'token_version':user.token_version}
        if org is not None: claims['tenant_id'] = str(org.id)
        if clinic is not None: claims['clinic_context_id'] = clinic.id
        with app.test_request_context():
            token = create_access_token(identity=str(user.id), additional_claims=claims)
        return {'Authorization':'Bearer '+token}
    return dict(a=a,b=b,ca=ca,cb=cb,cc=cc,owner=owner,admin=admin,other=other,doctor=doctor,billing=billing,platform=platform,
                h=headers(owner,a,ca),branch=headers(admin,a,cb),other_h=headers(other,b,cc),doc_h=headers(doctor,a,ca),billing_h=headers(billing,a,ca),sa=headers(platform),tenant_only=headers(owner,a),headers=headers)


def upload(client, path, headers, version, raw=None, mime='image/png'):
    return client.put(path, headers=headers, data={'expected_version':str(version),'logo':(BytesIO(png() if raw is None else raw),'logo.png',mime)},content_type='multipart/form-data')


def grant(client, w, version, allowed=True, headers=None):
    return client.put(f"/api/v1/clinics/organizations/{w['a'].id}/branding-entitlement",headers=headers or w['sa'],json={'expected_version':version,'clinic_override_allowed':allowed})


def data(response):
    assert response.status_code==200, response.get_data(as_text=True)
    assert 'no-store' in response.headers['Cache-Control']
    return response.get_json()['data']


def test_inheritance_override_and_downgrade(client, world, db):
    w=world
    assert data(client.get(CLINIC,headers=w['branch']))['logo_source']=='none'
    assert upload(client,ORG,w['tenant_only'],0).status_code==200
    snap=data(client.get(CLINIC,headers=w['branch']))
    assert snap['clinic_name']=='Alpha branch' and snap['address']=='Synthetic 2' and snap['logo_source']=='organization'
    assert client.get(CLINIC+'/logo',headers=w['branch']).data==svc.normalize_logo(png(),'image/png')
    assert upload(client,CLINIC,w['branch'],0,png('blue')).status_code==404
    assert data(grant(client,w,1))['version']==2
    assert upload(client,CLINIC,w['branch'],0,png('blue')).status_code==200
    branch=data(client.get(CLINIC,headers=w['branch']))
    assert branch['logo_source']=='clinic' and branch['logo_sha256']!=snap['logo_sha256']
    assert data(client.get(CLINIC,headers=w['h']))['logo_source']=='organization'
    assert data(client.get(CLINIC,headers=w['other_h']))['logo_source']=='none'
    assert data(grant(client,w,2,False))['version']==3
    assert data(client.get(CLINIC,headers=w['branch']))['logo_sha256']==snap['logo_sha256']
    assert client.delete(CLINIC,headers=w['branch'],json={'expected_version':1}).status_code==200
    assert data(grant(client,w,3))['version']==4
    assert data(client.get(CLINIC,headers=w['branch']))['logo_source']=='organization'
    assert db.session.query(AuditLog).filter(AuditLog.entity_type.in_(['OrganizationBranding','ClinicBranding'])).count()==6


def test_role_and_scope_denials(client, world, db):
    w=world
    for h in (w['branch'],w['doc_h'],w['billing_h'],w['sa']):
        assert upload(client,ORG,h,0).status_code in (403,404)
    assert upload(client,CLINIC,w['doc_h'],0).status_code==403
    assert grant(client,w,0,headers=w['h']).status_code==403
    assert client.get(ORG,headers=w['sa']).status_code==404
    forged=w['headers'](w['owner'],w['a'],w['cc'])
    assert client.get(CLINIC,headers=forged).status_code in (401,403,404)
    assert client.get(ORG+'?organization_id='+str(w['b'].id),headers=w['h']).status_code==422
    assert db.session.query(OrganizationBranding).count()==0


def test_version_and_no_store(client, world, db):
    w=world
    assert upload(client,ORG,w['h'],0).status_code==200
    assert upload(client,ORG,w['h'],0,png('blue')).status_code==409
    assert client.delete(ORG,headers=w['h'],json={'expected_version':True}).status_code==422
    assert client.delete(ORG,headers=w['h'],json={'expected_version':1}).status_code==200
    assert client.get(ORG+'/logo',headers=w['h']).status_code==404
    assert data(client.get(ORG,headers=w['h']))['version']==2
    assert upload(client,ORG,w['h'],0).status_code==409
    assert upload(client,ORG,w['h'],2,png('green')).status_code==200
    r=client.get(ORG+'/logo',headers=w['h']);assert r.mimetype=='image/png' and 'no-store' in r.headers['Cache-Control']
    assert r.headers['X-Content-Type-Options']=='nosniff'
    assert data(client.get(ORG,headers=w['h']))['logo_sha256']==sha256(r.data).hexdigest()


@pytest.mark.parametrize('raw,mime', [(b'', 'image/png'), (b'<svg/>','image/png'),(b'\x89PNG\r\n\x1a\n','image/png'),(png(fmt='JPEG'),'image/png'),(png(size=(2049,1)),'image/png'),(b'x'*(svc.MAX_UPLOAD+1),'image/png'),(png(),'image/svg+xml')], ids=['empty','svg','truncated','mismatched-mime','oversized-dimensions','oversized-bytes','unsupported-mime'])
def test_invalid_logo_denied(raw,mime):
    with pytest.raises(ValidationError): svc.normalize_logo(raw,mime)


def test_normalize_metadata_and_size():
    f=BytesIO(); info=PngImagePlugin.PngInfo();info.add_text('Comment','DO NOT KEEP')
    Image.new('RGBA',(1024,512),(50,80,100,120)).save(f,format='PNG',pnginfo=info)
    out=svc.normalize_logo(f.getvalue(),'image/png')
    with Image.open(BytesIO(out)) as im:
        assert im.size==(512,256) and im.mode=='RGBA' and not im.info
    jpeg=svc.normalize_logo(png(fmt='JPEG'),'image/jpeg')
    with Image.open(BytesIO(jpeg)) as im: assert im.format=='PNG'


def test_resolver_canonical_identity_and_integrity(world, db, client):
    w=world
    assert upload(client,ORG,w['h'],0).status_code==200
    first=svc.resolve_branding(owning_clinic_id=w['ca'].id)
    second=svc.resolve_branding(owning_clinic_id=w['cb'].id)
    assert first.logo_png==second.logo_png and first.branding_sha256!=second.branding_sha256
    assert svc.resolve_branding(owning_clinic_id=w['cc'].id).logo_png is None
    row=db.session.query(OrganizationBranding).filter_by(organization_id=w['a'].id).one();row.logo_sha256='0'*64;db.session.commit()
    with pytest.raises(ConflictError):svc.resolve_branding(owning_clinic_id=w['cb'].id)


def test_foreign_clinic_fk(world, db):
    w=world
    db.session.add(ClinicBranding(organization_id=w['b'].id,clinic_id=w['ca'].id,version=1))
    with pytest.raises(IntegrityError): db.session.flush()
    db.session.rollback()


def test_disabled_and_non_testing(client, world, app):
    w=world
    app.config['PRINT_BRANDING_STAGED_ENABLED']=False
    assert client.get(ORG,headers=w['h']).status_code==404
    app.config['PRINT_BRANDING_STAGED_ENABLED']=True
    app.config['TESTING']=False
    try: assert client.get(ORG,headers=w['h']).status_code==404
    finally: app.config['TESTING']=True


def test_postgres_first_write_race(world, db, app):
    assert db.engine.dialect.name=='postgresql'
    w=world; oid=w['a'].id; uid=w['owner'].id
    db.session.remove(); barrier=Barrier(2,timeout=15)
    def attempt(color):
        with app.app_context():
            barrier.wait()
            try:
                svc.change_logo(user_id=uid,organization_id=oid,expected_version=0,raw=png(color),mime='image/png')
                return 'written'
            except ConflictError:
                return 'conflict'
            finally: db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,['red','blue']))
    assert sorted(results)==['conflict','written']
    assert db.session.query(OrganizationBranding).count()==1
    assert db.session.query(AuditLog).filter_by(entity_type='OrganizationBranding').count()==1


def test_revoked_membership_and_unauthorized_scope(client, world, db):
    w=world
    assert upload(client,ORG,w['h'],0).status_code==200
    row=db.session.query(TenantMembership).filter_by(user_id=w['owner'].id,organization_id=w['a'].id).one()
    row.status='revoked';row.is_active=False;db.session.commit()
    assert client.get(ORG,headers=w['h']).status_code in (401,403,404)
    assert client.get(ORG+'/logo',headers=w['other_h']).status_code==404
    assert 'no-store' in client.get(ORG+'/logo',headers=w['other_h']).headers['Cache-Control']


def test_audit_failure_rolls_back(world, db, monkeypatch):
    w=world
    def fail(**kwargs):
        raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(svc,'create_audit_log',fail)
    with pytest.raises(RuntimeError):
        svc.change_logo(user_id=w['owner'].id,organization_id=w['a'].id,expected_version=0,raw=png(),mime='image/png')
    assert db.session.query(OrganizationBranding).count()==0


def test_animated_png_denied():
    f=BytesIO(); first=Image.new('RGB',(10,10),'red'); second=Image.new('RGB',(10,10),'blue')
    first.save(f,format='PNG',save_all=True,append_images=[second],duration=50,loop=0)
    with pytest.raises(ValidationError): svc.normalize_logo(f.getvalue(),'image/png')


def test_platform_entitlement_metadata_only(client, world):
    w=world; path=f"/api/v1/clinics/organizations/{w['a'].id}/branding-entitlement"
    assert data(client.get(path,headers=w['sa']))=={'version':0,'clinic_override_allowed':False}
    assert upload(client,ORG,w['h'],0).status_code==200
    assert data(client.get(path,headers=w['sa']))=={'version':1,'clinic_override_allowed':False}
    assert client.get(path,headers=w['h']).status_code==403
    assert data(grant(client,w,1))=={'version':2,'clinic_override_allowed':True}
    assert client.get(ORG+'/logo',headers=w['sa']).status_code==404


def test_selected_clinic_grant_and_no_body_override(client, world):
    w=world
    assert upload(client,ORG,w['h'],0).status_code==200
    assert data(grant(client,w,1))['clinic_override_allowed'] is True
    switched=w['headers'](w['owner'],w['a'],w['cb'])
    assert upload(client,CLINIC,switched,0,png('blue')).status_code==200
    assert data(client.get(CLINIC,headers=switched))['clinic_id']==w['cb'].id
    assert data(client.get(CLINIC,headers=w['h']))['logo_source']=='organization'
    forged=w['headers'](w['admin'],w['a'],w['ca'])
    assert client.get(CLINIC,headers=forged).status_code in (401,403,404)
    assert client.delete(CLINIC,headers=switched,json={'expected_version':1,'clinic_id':w['ca'].id}).status_code==422
    assert data(client.get(CLINIC,headers=switched))['logo_source']=='clinic'


DIRECTORY = '/api/v1/clinics/branding/organizations'
CAPABILITIES = '/api/v1/clinics/branding/capabilities'


def test_l05r2d_platform_directory_minimal_projection(client, world, db):
    w = world
    assert upload(client, ORG, w['h'], 0).status_code == 200
    assert grant(client, w, 1).status_code == 200
    result = data(client.get(DIRECTORY, headers=w['sa']))
    assert set(result) == {'items', 'next_cursor'}
    items = {item['organization_id']: item for item in result['items']}
    assert items[str(w['a'].id)] == {'organization_id': str(w['a'].id),
        'organization_name': w['a'].name, 'version': 2, 'clinic_override_allowed': True}
    assert items[str(w['b'].id)]['version'] == 0
    assert all(set(item) == {'organization_id', 'organization_name', 'version',
                             'clinic_override_allowed'} for item in result['items'])
    assert data(client.get(DIRECTORY+'/'+str(w['a'].id), headers=w['sa'])) == items[str(w['a'].id)]
    assert client.get(ORG+'/logo', headers=w['sa']).status_code == 404
    assert db.session.query(OrganizationBranding).count() == 1


def test_l05r2d_directory_cursor_bounded_no_duplicates(client, world, db):
    from uuid import UUID
    w = world
    db.session.add_all([HospitalOrganization(id=UUID(f'abcdef00-1234-4000-8000-{i+1:012x}'), name=f'Synthetic {i}')
                        for i in range(103)])
    db.session.commit()
    expected = sorted(str(row.id) for row in db.session.query(HospitalOrganization).all())
    observed = []
    path = DIRECTORY
    for _ in range(10):
        result = data(client.get(path, headers=w['sa']))
        page = [item['organization_id'] for item in result['items']]
        assert 0 < len(page) <= 50
        observed.extend(page)
        cursor = result['next_cursor']
        if cursor is None:
            break
        assert len(page) == 50 and cursor == page[-1]
        path = DIRECTORY+'?after='+cursor
    else:
        pytest.fail('Directory did not terminate')
    assert observed == expected and len(set(observed)) == len(expected)
    assert data(client.get(DIRECTORY+'?after='+expected[-1], headers=w['sa'])) == {'items': [], 'next_cursor': None}


def test_l05r2d_platform_directory_role_denials(client, world):
    w = world
    for h in (w['h'], w['branch'], w['other_h'], w['doc_h'], w['billing_h']):
        for path in (DIRECTORY, DIRECTORY+'/'+str(w['a'].id)):
            r = client.get(path, headers=h)
            assert r.status_code == 403
            assert 'no-store' in r.headers['Cache-Control']
            assert r.headers['X-Content-Type-Options'] == 'nosniff'
            assert 'data' not in r.get_json()
    assert client.get(DIRECTORY).status_code == 401


def test_l05r2d_platform_selected_clinic_denied(client, world, app):
    w = world
    with app.test_request_context():
        token = create_access_token(identity=str(w['platform'].id), additional_claims={
            'role': w['platform'].role.value, 'token_version': w['platform'].token_version,
            'platform_clinic_id': w['ca'].id})
    h = {'Authorization': 'Bearer '+token}
    for path in (DIRECTORY, DIRECTORY+'/'+str(w['a'].id)):
        assert client.get(path, headers=h).status_code == 404


def test_l05r2d_directory_invalid_parameters(client, world):
    w = world
    for query in ('?after=', '?after=not-a-uuid', '?after='+str(w['a'].id)+'&after='+str(w['b'].id),
                  '?limit=999999', '?organization_id='+str(w['b'].id)):
        assert client.get(DIRECTORY+query, headers=w['sa']).status_code == 422
    path = DIRECTORY+'/'+str(w['a'].id)
    assert client.get(path+'?after='+str(w['b'].id), headers=w['sa']).status_code == 422
    assert client.get(path, headers=dict(w['sa'], **{'Idempotency-Key':'x'})).status_code == 422
    assert client.get(path, headers=w['sa'], json={'organization_id':str(w['b'].id)}).status_code == 422
    assert client.get(DIRECTORY+'/'+str(uuid4()), headers=w['sa']).status_code == 404


def test_l05r2d_owner_capabilities_tenant_and_clinic(client, world):
    w = world
    expected = {'organization_id':str(w['a'].id), 'organization_name':w['a'].name,
                'membership_role':'organization_admin', 'clinic_id':None,
                'can_manage_organization_branding':True, 'can_manage_clinic_branding':False,
                'can_customize_clinic_branding':False, 'can_reset_clinic_branding':False}
    assert data(client.get(CAPABILITIES, headers=w['tenant_only'])) == expected
    expected.update(clinic_id=w['ca'].id, can_manage_clinic_branding=True, can_reset_clinic_branding=True)
    assert data(client.get(CAPABILITIES, headers=w['h'])) == expected
    assert grant(client, w, 0).status_code == 200
    expected['can_customize_clinic_branding'] = True
    assert data(client.get(CAPABILITIES, headers=w['h'])) == expected


def test_l05r2d_branch_entitlement_revoke_keeps_reset(client, world):
    w = world
    expected = {'organization_id':str(w['a'].id), 'organization_name':w['a'].name,
                'membership_role':'member', 'clinic_id':w['cb'].id,
                'can_manage_organization_branding':False, 'can_manage_clinic_branding':True,
                'can_customize_clinic_branding':False, 'can_reset_clinic_branding':True}
    assert data(client.get(CAPABILITIES, headers=w['branch'])) == expected
    assert grant(client, w, 0).status_code == 200
    assert upload(client, CLINIC, w['branch'], 0).status_code == 200
    expected['can_customize_clinic_branding'] = True
    assert data(client.get(CAPABILITIES, headers=w['branch'])) == expected
    assert grant(client, w, 1, False).status_code == 200
    expected['can_customize_clinic_branding'] = False
    assert data(client.get(CAPABILITIES, headers=w['branch'])) == expected
    assert upload(client, CLINIC, w['branch'], 1).status_code == 404
    assert client.delete(CLINIC, headers=w['branch'], json={'expected_version':1}).status_code == 200


def test_l05r2d_ordinary_members_no_branding_authority(client, world):
    w = world
    for header, role in ((w['doc_h'], 'member'), (w['billing_h'], 'billing_admin')):
        result = data(client.get(CAPABILITIES, headers=header))
        assert result['membership_role'] == role
        assert result['organization_id'] == str(w['a'].id)
        assert all(result[k] is False for k in ('can_manage_organization_branding',
            'can_manage_clinic_branding', 'can_customize_clinic_branding', 'can_reset_clinic_branding'))
    assert client.get(CAPABILITIES, headers=w['sa']).status_code == 404
    assert client.get(CAPABILITIES, headers=w['headers'](w['owner'])).status_code == 404


def test_l05r2d_membership_role_change_is_live(client, world, db):
    w = world
    row = db.session.query(TenantMembership).filter_by(user_id=w['owner'].id, organization_id=w['a'].id).one()
    row.role = 'member'
    db.session.commit()
    result = data(client.get(CAPABILITIES, headers=w['h']))
    assert result['membership_role'] == 'member'
    assert result['can_manage_organization_branding'] is False
    assert result['can_manage_clinic_branding'] is True
    assert client.get(ORG, headers=w['h']).status_code == 404
    row.status = 'revoked'; row.is_active = False; db.session.commit()
    assert client.get(CAPABILITIES, headers=w['h']).status_code in (401,403,404)


def test_l05r2d_forged_scope_and_parameters_denied(client, world):
    w = world
    for query in ('?organization_id='+str(w['b'].id), '?clinic_id='+str(w['cc'].id), '?role=organization_admin'):
        assert client.get(CAPABILITIES+query, headers=w['h']).status_code == 422
    assert client.get(CAPABILITIES, headers=dict(w['h'], **{'Idempotency-Key':'x'})).status_code == 422
    for h in (w['headers'](w['owner'], w['a'], w['cc']), w['headers'](w['admin'], w['a'], w['ca']),
              w['headers'](w['owner'], w['b'])):
        assert client.get(CAPABILITIES, headers=h).status_code in (401,403,404)
    result = data(client.get(CAPABILITIES, headers=w['other_h']))
    assert result['organization_id'] == str(w['b'].id) and result['clinic_id'] == w['cc'].id


def test_l05r2d_home_organization_boundary(client, world, db):
    w = world
    db.session.add(TenantMembership(user_id=w['owner'].id, organization_id=w['b'].id,
                                   role='organization_admin', is_active=True, status='active'))
    db.session.flush()
    db.session.add(ClinicMembership(user_id=w['owner'].id, organization_id=w['b'].id,
                                   clinic_id=w['cc'].id, role=Role.ADMIN.value, is_active=True))
    db.session.commit()
    h = w['headers'](w['owner'], w['b'], w['cc'])
    result = data(client.get(CAPABILITIES, headers=h))
    assert result['membership_role'] == 'organization_admin'
    assert result['can_manage_organization_branding'] is False
    assert result['can_manage_clinic_branding'] is False
    assert result['can_customize_clinic_branding'] is False
    assert result['can_reset_clinic_branding'] is False
    assert client.get(ORG, headers=h).status_code == 404
    assert client.get(CLINIC, headers=h).status_code == 404


def test_l05r2d_discovery_read_only_no_audit_or_branding_rows(client, world, db):
    w = world
    before = db.session.query(AuditLog).count()
    for path, h in ((DIRECTORY,w['sa']), (DIRECTORY+'/'+str(w['a'].id),w['sa']),
                    (CAPABILITIES,w['h']), (CAPABILITIES,w['branch'])):
        r = client.get(path, headers=h)
        data(r)
        assert r.headers['X-Content-Type-Options'] == 'nosniff'
        assert 'no-store' in r.headers['Cache-Control']
        assert client.post(path, headers=h, json={}).status_code == 405
    assert db.session.query(OrganizationBranding).count() == 0
    assert db.session.query(ClinicBranding).count() == 0
    assert db.session.query(AuditLog).count() == before


def test_l05r2d_disabled_and_non_testing(client, world, app):
    w = world
    pairs = ((DIRECTORY,w['sa']), (DIRECTORY+'/'+str(w['a'].id),w['sa']), (CAPABILITIES,w['h']))
    app.config['PRINT_BRANDING_STAGED_ENABLED'] = False
    for path,h in pairs:
        assert client.get(path, headers=h).status_code == 404
    app.config['PRINT_BRANDING_STAGED_ENABLED'] = True
    app.config['TESTING'] = False
    try:
        for path,h in pairs:
            assert client.get(path, headers=h).status_code == 404
    finally:
        app.config['TESTING'] = True
