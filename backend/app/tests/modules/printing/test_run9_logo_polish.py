"""Final logo sizing/alpha proof. Synthetic fixtures only."""
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess

import pytest
from PIL import Image, ImageDraw
from app.modules.printing.services.branding_payload import prepare_branding
from app.modules.printing.services.professional_layout import make_plan, paint_reportlab, L, R, H, FOOT_TOP
from app.modules.printing.services import rust_print_bridge as native
from app.modules.clinic.services import branding_service as svc
from app.tests.modules.clinic.test_print_branding import world, ORG, CLINIC, grant, data
from app.tests.modules.printing.test_branded_rendering import document
from app.tests.modules.printing.test_run7_colour_contract import create_case, painted_rgb, has_rgb
from app.tests.modules.printing.test_run8_branding_studio import PALETTE, MODES

SIZES = {'small':(100,48), 'medium':(120,64), 'large':(144,80)}


def geometry(page, brand):
    x,y,w,h=page['logo'];mw,mh=SIZES[brand.logo_size];s=min(mw/brand.width,mh/brand.height)
    assert (x,w,h)==pytest.approx((L,brand.width*s,brand.height*s),abs=.001)
    identity=[]
    expected=''.join((''.join(brand.lines[:2]).replace('Organisation: ','',1).replace('Clinic: ','',1)+' | '.join(brand.lines[2:])).split())
    for t in page['text']:
        if ''.join(''.join(q['text'] for q in identity).split())==expected:break
        identity.append(t)
    assert ''.join(''.join(t['text'] for t in identity).split())==expected
    assert all(abs(t['x']-(x+w+12))<.001 for t in identity)
    top=identity[0]['y']-identity[0]['size'];bottom=identity[-1]['y']
    assert min(y,top)==pytest.approx(58,abs=.001)
    assert y+h/2==pytest.approx((top+bottom)/2,abs=.002)
    divider=page['rects'][1]
    assert divider['y']>max(y+h,bottom)
    assert all(t['y']<H-FOOT_TOP for t in identity)


@pytest.mark.parametrize('size',SIZES)
@pytest.mark.parametrize('shape',[(64,64),(48,180),(200,42)])
def test_adaptive_geometry(size,shape,tmp_path):
    doc,b=create_case('invoice');w,h=shape
    b=replace(b,rgb=Image.new('RGB',shape,'#8A4B26').tobytes(),width=w,height=h,logo_size=size)
    plan,faces=make_plan(doc,b)
    for page in plan['pages']:geometry(page,b)
    for engine,pdf in [('native',native.render_pdf(doc,branding=b)),('reference',paint_reportlab(plan,faces,b))]:
        assert pdf.startswith(b'%PDF-') and pdf.rstrip().endswith(b'%%EOF')
        assert has_rgb(painted_rgb(pdf),'#842020')
        (Path(os.environ.get('CSP_RUN9_EVIDENCE',str(tmp_path)))/f'{size}_{w}x{h}_{engine}.pdf').write_bytes(pdf)


@pytest.mark.parametrize('mode',MODES)
def test_alpha_mask_and_mode(mode,tmp_path):
    doc,b=create_case('invoice');shape=(64,48)
    im=Image.new('RGBA',shape,(0,0,0,0));d=ImageDraw.Draw(im)
    d.ellipse((0,0,63,47),fill=(170,45,20,255));d.rectangle((20,12,40,32),fill=(255,255,255,128))
    palette={**PALETTE,'output_mode':mode}
    b=replace(b,rgb=im.convert('RGB').tobytes(),alpha=im.getchannel('A').tobytes(),width=64,height=48,
              primary_color=palette['primary_color'],secondary_color=palette['secondary_color'],studio=palette)
    plan,faces=make_plan(doc,b)
    assert Image.open(BytesIO(b.png())).getchannel('A').tobytes()==b.alpha
    for engine,pdf in [('native',native.render_pdf(doc,branding=b)),('reference',paint_reportlab(plan,faces,b))]:
        assert pdf.startswith(b'%PDF-1.4') and b'/SMask' in pdf and b'/DeviceGray' in pdf
        rgb=painted_rgb(pdf)
        assert has_rgb(rgb,PALETTE['background_color'])==(mode=='full')
        if mode=='monochrome':assert all(abs(r-g)<1e-6 and abs(g-bl)<1e-6 for r,g,bl in rgb)
        (Path(os.environ.get('CSP_RUN9_EVIDENCE',str(tmp_path)))/f'alpha_{mode}_{engine}.pdf').write_bytes(pdf)


def test_large_wrapped_header_and_no_logo():
    doc,b=create_case('radiology_draft')
    b=replace(b,logo_size='large',lines=('Organisation: Synthetic '+ 'Wide name '*18,'Clinic: Branch clinic '+ 'W'*75,'Address: '+ 'Long Street '*18))
    plan,_=make_plan(doc,b)
    assert len(plan['pages'])>1
    for page in plan['pages']:geometry(page,b)
    assert native.render_pdf(doc,branding=b).startswith(b'%PDF-')
    no=replace(b,rgb=None,width=0,height=0,alpha=None)
    plan,_=make_plan(doc,no)
    assert all(p['logo'] is None and p['text'][0]['x']==L for p in plan['pages'])
    assert native.render_pdf(doc,branding=no).startswith(b'%PDF-')


def test_logo_geometry_and_alpha_tamper_denied():
    doc,b=create_case('invoice');b=replace(b,alpha=bytes([128])*(b.width*b.height),logo_size='large')
    plan,_=make_plan(doc,b);wire={**asdict(doc),'protocol':3,'branding':b.wire(),'professional':plan}
    def invoke(p):return subprocess.run([os.environ['CSP_RUST_PRINT_RENDERER']],input=json.dumps(p,separators=(',',':')).encode(),capture_output=True,timeout=30)
    assert invoke(wire).stdout.startswith(b'%PDF-')
    variants=[]
    for key,val in [('size','giant'),('alpha_hex','00'),('alpha_hex','00'*(b.width*b.height)),('alpha_hex','ZZ'*(b.width*b.height))]:
        q=deepcopy(wire);q['branding']['logo'][key]=val;variants.append(q)
    q=deepcopy(wire);q['professional']['pages'][0]['logo']=list(q['professional']['pages'][0]['logo']);q['professional']['pages'][0]['logo'][1]+=5;variants.append(q)
    q=deepcopy(wire);q['professional']['pages'][0]['text'][0]['x']+=10;variants.append(q)
    for bad in variants:
        result=invoke(bad);assert result.returncode!=0 and not result.stdout.startswith(b'%PDF-')


def put(client,scope,h,v,ov,overrides,version='studio-v2'):
    return client.put(scope+'/'+version,headers=h,json={'expected_version':v,'expected_organization_version':ov,'overrides':overrides})


def get(client,scope,h,version='studio-v2'):
    return data(client.get(scope+'/'+version,headers=h))


def test_versioned_size_inheritance_reset_and_legacy_compat(client,world):
    w=world
    assert get(client,ORG,w['h'])['effective']['logo_size']=='medium'
    assert put(client,ORG,w['h'],0,0,{'logo_size':'giant'}).status_code==422
    assert put(client,ORG,w['h'],0,0,{'logo_size':'large'},'studio').status_code==422
    assert data(put(client,ORG,w['h'],0,0,{'logo_size':'large'}))['version']==1
    assert get(client,CLINIC,w['branch'])['effective']['logo_size']=='large'
    assert 'logo_size' not in get(client,ORG,w['h'],'studio')['effective']
    assert 'logo_size' not in data(client.get(CLINIC,headers=w['branch']))
    assert put(client,CLINIC,w['branch'],0,1,{'logo_size':'small'}).status_code==404
    assert data(grant(client,w,1))['version']==2
    assert put(client,CLINIC,w['branch'],0,1,{'logo_size':'small'}).status_code==409
    assert data(put(client,CLINIC,w['branch'],0,2,{'logo_size':'small'}))['effective']['logo_size']=='small'
    # V1 writes must preserve a V2 size, even when clearing all colours.
    assert data(put(client,ORG,w['h'],2,2,{'primary_color':'#136228'},'studio'))['version']==3
    assert get(client,ORG,w['h'])['effective']['logo_size']=='large'
    assert data(put(client,ORG,w['h'],3,3,{},'studio'))['version']==4
    assert get(client,ORG,w['h'])['effective']['logo_size']=='large'
    assert data(grant(client,w,4,False))['version']==5
    assert get(client,CLINIC,w['branch'])['effective']['logo_size']=='large'
    assert put(client,CLINIC,w['branch'],1,5,{'logo_size':'medium'}).status_code==404
    assert data(put(client,CLINIC,w['branch'],1,5,{}))['local']=={}
    assert data(put(client,ORG,w['h'],5,5,{}))['effective']['logo_size']=='medium'
    assert client.get(ORG+'/studio-v2',headers=w['doc_h']).status_code in (403,404)
    assert get(client,ORG,w['other_h'])['effective']['logo_size']=='medium'


def test_stored_logo_keeps_alpha_and_hash(client,world,db):
    from app.core.exceptions import ValidationError
    w=world;im=Image.new('RGBA',(128,128),(0,0,0,0));draw=ImageDraw.Draw(im)
    blank=BytesIO();im.save(blank,format='PNG')
    with pytest.raises(ValidationError):svc.normalize_logo(blank.getvalue(),'image/png')
    draw.ellipse((20,20,107,107),fill=(170,45,20,255));draw.rectangle((50,50,60,60),fill=(255,255,255,128))
    out=BytesIO();im.save(out,format='PNG')
    logo=svc.change_logo(user_id=w['owner'].id,organization_id=w['a'].id,expected_version=0,raw=out.getvalue(),mime='image/png')
    assert data(put(client,ORG,w['h'],1,1,{'logo_size':'large'}))['version']==2
    doc=document('invoice',w['ca'].id);b=prepare_branding(doc,owning_clinic_id=doc.clinic_id)
    assert b.logo_size=='large' and b.logo_sha256==logo['logo_sha256']
    assert (b.width,b.height)==(88,88) and min(b.alpha)==0 and max(b.alpha)==255 and 128 in b.alpha
    canonical=svc.read_branding(user_id=w['owner'].id,organization_id=w['a'].id,logo=True)
    assert sha256(canonical).hexdigest()==b.logo_sha256 and Image.open(BytesIO(canonical)).size==(128,128)
    assert b'/SMask' in native.render_pdf(doc,branding=b)
