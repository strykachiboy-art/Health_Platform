import pytest
from itsdangerous import URLSafeTimedSerializer
from app.modules.unified_search.contracts import SearchFilters, encode_cursor, decode_cursor, available_resources


@pytest.mark.parametrize('body', [None, [], {}, {'q': ' '}, {'q': 'a'}, {'q': 'a'*121}, {'q': 'ab\n'},
    {'q': '#0'}, {'q': '#2147483648'}, {'q': '#01'}, {'q': 'Ada', 'clinic_id': 1},
    {'q': 'Ada', 'organization_id': 'other'}, {'q': 'Ada', 'limit': True}, {'q': 'Ada', 'limit': 51},
    {'q': 'Ada', 'resource_types': []}, {'q': 'Ada', 'resource_types': ['patient', 'patient']},
    {'q': 'Ada', 'resource_types': ['unknown']}, {'q': 'Ada', 'resource_types': [{}]},
    {'q': 'Ada', 'status': 'unknown'}, {'q': 'Ada', 'cursor': 'x'*2049}])
def test_invalid_filters(body):
    with pytest.raises(ValueError):
        SearchFilters.parse(body)


def test_exact_and_normalized_query():
    f = SearchFilters.parse({'q': '  #1 ', 'resource_types': ['lab_order', 'patient'], 'limit': 5})
    assert f.query == '#1' and f.exact_id == 1 and f.resource_types == ('patient', 'lab_order') and f.limit == 5
    assert SearchFilters.parse({'q': 'A_B%'}).exact_id is None


def test_cursor_private_and_bound():
    f = SearchFilters.parse({'q': 'SYNTHETIC PRIVATE NAME', 'limit': 2})
    kwargs = dict(user_id=1, tenant_id='tenant', clinic_id=1, role='doctor', token_version=0, jti='session-a', kinds=('patient',))
    secret = 'synthetic-secret'
    b = f.binding(secret, **kwargs)
    token = encode_cursor(secret, b, 0, 7)
    assert decode_cursor(secret, b, token) == (0, 7)
    assert URLSafeTimedSerializer(secret, salt='unified-clinical-search-v1').loads(token) == [b, 0, 7]
    for changed in [dict(jti='session-b'), dict(clinic_id=2), dict(role='nurse'), dict(tenant_id='other'), dict(user_id=2), dict(token_version=1)]:
        with pytest.raises(ValueError):
            decode_cursor(secret, f.binding(secret, **(kwargs | changed)), token)
    for bad in [token+'x', 'bad']:
        with pytest.raises(ValueError):
            decode_cursor(secret, b, bad)
    with pytest.raises(ValueError):
        decode_cursor(secret, SearchFilters.parse({'q': 'Different', 'limit': 2}).binding(secret, **kwargs), token)


@pytest.mark.parametrize('role,expected', [('doctor',11),('nurse',10),('admin',9),('receptionist',3),
    ('lab_technician',3),('pharmacist',2),('paramedic',1),('emt',1),('super_admin',0),('patient',0),('accountant',0)])
def test_role_matrix(role,expected):
    assert len(available_resources(role)) == expected
