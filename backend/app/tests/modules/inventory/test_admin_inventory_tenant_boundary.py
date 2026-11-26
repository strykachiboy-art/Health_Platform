"""Real HTTP + persistence checks, no route or authorization mocks."""
from uuid import uuid4
from datetime import datetime, timezone

import pytest

from app.core.enums.role_enums import Role
from app.core.enums.inventory_enums import InventoryTransferStatus
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.modules.clinic.models.organization_model import (
    HospitalOrganization, TenantMembership, ClinicMembership,
)
from app.modules.inventory.models.inventory_model import (
    InventorySupplier, InventoryTransfer, InventoryItem, StockMovement,
)

PREFIX = '/api/v1/inventory'

@pytest.fixture
def boundary(app, db, make_clinic, make_staff, make_inventory_item):
    source = make_clinic()
    sibling = make_clinic()
    other = HospitalOrganization(id=uuid4(), name='Independent test owner')
    db.session.add(other)
    db.session.flush()
    foreign = make_clinic(organization_id=other.id)
    def actor(clinic, role):
        staff = make_staff(clinic, role=role)
        db.session.add(TenantMembership(user_id=staff.user_id, organization_id=clinic.organization_id,
                                        role='member', is_active=True, status='active'))
        db.session.flush()
        db.session.add(ClinicMembership(user_id=staff.user_id, organization_id=clinic.organization_id,
                                        clinic_id=clinic.id, role=role.value, is_active=True))
        db.session.flush()
        with app.test_request_context():
            pair = issue_auth_tokens(staff.user, clinic_context_id=clinic.id)
        return staff.id, {'Authorization': 'Bearer ' + pair['access_token']}
    admin, headers = actor(source, Role.ADMIN)
    approver, approve_headers = actor(source, Role.ADMIN)
    pharmacist, pharmacist_headers = actor(source, Role.PHARMACIST)
    _, destination_headers = actor(sibling, Role.ADMIN)
    _, foreign_headers = actor(foreign, Role.ADMIN)
    item = make_inventory_item(source, quantity_on_hand=100)
    local = InventorySupplier(name='Local test supplier', clinic_id=source.id, is_active=True)
    shared = InventorySupplier(name='Shared test supplier', clinic_id=None, is_active=True)
    outsider = InventorySupplier(name='Foreign test supplier', clinic_id=foreign.id, is_active=True)
    db.session.add_all([local, shared, outsider]); db.session.flush()
    ids = dict(source=source.id, sibling=sibling.id, foreign=foreign.id, item=item.id,
               local=local.id, shared=shared.id, outsider=outsider.id, admin=admin,
               approver=approver, headers=headers, approve_headers=approve_headers,
               pharmacist_headers=pharmacist_headers, destination_headers=destination_headers,
               foreign_headers=foreign_headers)
    db.session.commit()
    return ids

def create(client, b, destination, headers=None):
    return client.post(PREFIX+'/transfers', headers=headers or b['headers'],
                       json={'item_id': b['item'], 'destination_clinic_id': b[destination], 'quantity': 7})

def data(response, code):
    assert response.status_code == code, response.get_json()
    return response.get_json()['data']

def denied(response):
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['success'] is False

def legacy(db, b, status, destination='foreign'):
    row = InventoryTransfer(item_id=b['item'], source_clinic_id=b['source'], destination_clinic_id=b[destination],
                            quantity=7, status=status, requested_by_id=b['admin'], requested_at=datetime.now(timezone.utc))
    if status == InventoryTransferStatus.APPROVED:
        row.approved_by_id=b['approver']; row.approved_at=datetime.now(timezone.utc)
    db.session.add(row); db.session.flush(); ident=row.id; db.session.commit(); return ident

def state(db, b):
    db.session.expire_all()
    return (db.session.get(InventoryItem, b['item']).quantity_on_hand,
            db.session.query(InventoryTransfer).count(), db.session.query(StockMovement).count(),
            db.session.query(InventoryItem).count())

@pytest.mark.parametrize('actor', ['headers','pharmacist_headers'])
def test_foreign_transfer_create_rejected(client, db, boundary, actor):
    b=boundary; before=state(db,b)
    denied(create(client,b,'foreign',b[actor])); assert state(db,b)==before

@pytest.mark.parametrize('action,status', [('approve',InventoryTransferStatus.PENDING),
    ('complete',InventoryTransferStatus.APPROVED),('cancel',InventoryTransferStatus.PENDING)])
def test_legacy_foreign_transfer_cannot_mutate(client,db,boundary,action,status):
    b=boundary; ident=legacy(db,b,status); before=state(db,b)
    denied(client.post(f'{PREFIX}/transfers/{ident}/{action}',headers=b['approve_headers'],json={}))
    assert state(db,b)==before
    assert db.session.get(InventoryTransfer,ident).status==status

@pytest.mark.parametrize('actor',['headers','foreign_headers'])
def test_legacy_foreign_transfer_hidden_from_both_parties(client,db,boundary,actor):
    b=boundary; ident=legacy(db,b,InventoryTransferStatus.PENDING)
    denied(client.get(f'{PREFIX}/transfers/{ident}',headers=b[actor]))
    result=data(client.get(PREFIX+'/transfers',headers=b[actor]),200)
    assert result['total']==0 and result['items']==[]

def test_same_organization_transfer_lifecycle_and_pagination(client,db,boundary):
    b=boundary; legacy(db,b,InventoryTransferStatus.PENDING)
    row=data(create(client,b,'sibling'),201); ident=row['id']
    for actor in ['headers','destination_headers']:
        result=data(client.get(PREFIX+'/transfers?per_page=1',headers=b[actor]),200)
        assert result['total']==1 and [x['id'] for x in result['items']]==[ident]
        assert data(client.get(f'{PREFIX}/transfers/{ident}',headers=b[actor]),200)['id']==ident
    denied(client.post(f'{PREFIX}/transfers/{ident}/approve',headers=b['headers'],json={}))
    denied(client.post(f'{PREFIX}/transfers/{ident}/approve',headers=b['destination_headers'],json={}))
    data(client.post(f'{PREFIX}/transfers/{ident}/approve',headers=b['approve_headers'],json={}),200)
    data(client.post(f'{PREFIX}/transfers/{ident}/complete',headers=b['headers'],json={}),200)
    db.session.expire_all()
    assert db.session.get(InventoryItem,b['item']).quantity_on_hand==93
    destination=db.session.query(InventoryItem).filter_by(clinic_id=b['sibling']).one()
    assert destination.quantity_on_hand==7
    moves=db.session.query(StockMovement).filter_by(reference_type='inventory_transfer', reference_id=ident).all()
    assert {m.movement_type.value for m in moves}=={'transfer_in','transfer_out'} and len(moves)==2
    assert all(m.quantity==7 for m in moves)
    assert client.post(f'{PREFIX}/transfers/{ident}/complete',headers=b['headers'],json={}).status_code==409
    assert db.session.query(StockMovement).filter_by(reference_type='inventory_transfer',reference_id=ident).count()==2

def test_same_organization_cancel_and_pharmacist_create(client,db,boundary):
    b=boundary; row=data(create(client,b,'sibling',b['pharmacist_headers']),201)
    result=data(client.post(f'{PREFIX}/transfers/{row["id"]}/cancel',headers=b['pharmacist_headers'],json={'reason':'test cancellation'}),200)
    assert result['status']=='cancelled'; assert db.session.get(InventoryItem,b['item']).quantity_on_hand==100

@pytest.mark.parametrize('which',['shared','outsider'])
@pytest.mark.parametrize('action',['update','deactivate','reactivate'])
def test_supplier_write_boundaries(client,db,boundary,which,action):
    b=boundary; ident=b[which]; row=db.session.get(InventorySupplier,ident)
    if action=='reactivate': row.is_active=False; db.session.commit()
    before=(row.name,row.is_active)
    if action=='update': response=client.patch(f'{PREFIX}/suppliers/{ident}',headers=b['headers'],json={'name':'Forbidden rename'})
    else: response=client.post(f'{PREFIX}/suppliers/{ident}/{action}',headers=b['headers'],json={})
    denied(response); db.session.expire_all(); row=db.session.get(InventorySupplier,ident)
    assert (row.name,row.is_active)==before

def test_shared_supplier_read_and_local_write_preserved(client,db,boundary):
    b=boundary
    result=data(client.get(PREFIX+'/suppliers',headers=b['headers']),200)
    assert {x['id'] for x in result['items']}=={b['local'],b['shared']}
    assert data(client.get(f'{PREFIX}/suppliers/{b["shared"]}',headers=b['headers']),200)['id']==b['shared']
    path=f'{PREFIX}/suppliers/{b["local"]}'
    assert data(client.patch(path,headers=b['headers'],json={'name':'New local name'}),200)['name']=='New local name'
    assert data(client.post(path+'/deactivate',headers=b['headers'],json={}),200)['is_active'] is False
    assert data(client.post(path+'/reactivate',headers=b['headers'],json={}),200)['is_active'] is True

@pytest.mark.parametrize('movement,ref',[('transfer_in',None),('transfer_out',None),('restock','inventory_transfer')])
@pytest.mark.parametrize('actor',['headers','pharmacist_headers'])
def test_direct_transfer_ledger_bypass_rejected(client,db,boundary,movement,ref,actor):
    b=boundary; before=state(db,b)
    payload={'item_id':b['item'],'movement_type':movement,'quantity':3}
    if ref: payload.update(reference_type=ref,reference_id=1)
    denied(client.post(PREFIX+'/movements',headers=b[actor],json=payload)); assert state(db,b)==before

def test_normal_stock_movement_preserved(client,db,boundary):
    b=boundary
    result=data(client.post(PREFIX+'/movements',headers=b['pharmacist_headers'],json={'item_id':b['item'],'movement_type':'restock','quantity':3}),201)
    assert result['movement_type']=='restock'; db.session.expire_all()
    assert db.session.get(InventoryItem,b['item']).quantity_on_hand==103
