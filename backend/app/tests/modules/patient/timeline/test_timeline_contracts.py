from datetime import datetime, timezone, timedelta
import pytest
from werkzeug.datastructures import MultiDict
from app.modules.patient.timeline.contracts import Filters, parse_time, encode_cursor, decode_cursor


@pytest.mark.parametrize('raw', [
    {'limit':'0'}, {'limit':'101'}, {'limit':'1.5'}, {'limit':'-1'}, {'limit':' 5'},
    {'categories':'appointments,appointments'}, {'categories':'unknown'}, {'categories':''},
    {'start':'2026-01-01'}, {'start':'2026-01-01T12:00:00'}, {'start':'2026-02-30T00:00:00Z'},
    {'start':'2026-01-02T00:00:00Z','end':'2026-01-01T00:00:00Z'},
    {'clinic_id':'1'}, {'patient_id':'1'}, {'organization_id':'x'}, {'cursor':''},
])
def test_invalid_filters(raw):
    with pytest.raises(ValueError):
        Filters.parse(MultiDict(raw))


def test_duplicate_filter_and_utc_normalization():
    with pytest.raises(ValueError):
        Filters.parse(MultiDict([('limit','1'),('limit','2')]))
    assert parse_time('2026-01-01T12:00:00+01:00') == datetime(2026,1,1,11,tzinfo=timezone.utc)


def test_cursor_bound_to_context_filters_and_version():
    q=Filters.parse(MultiDict({'limit':'2','categories':'vitals,appointments'}))
    scope=dict(user_id=1,organization_id='org',clinic_id=2,patient_id=3,role='doctor',token_version=4)
    b=q.binding(**scope); time=datetime(2026,1,1,tzinfo=timezone.utc)
    cursor=encode_cursor('synthetic-key',b,time,'vitals_recorded',7)
    assert decode_cursor('synthetic-key',b,cursor)==(time,'vitals_recorded',7)
    for key,value in [('patient_id',4),('clinic_id',3),('organization_id','other'),('user_id',2),('role','nurse'),('token_version',5)]:
        with pytest.raises(ValueError):
            decode_cursor('synthetic-key',q.binding(**(scope|{key:value})),cursor)
    with pytest.raises(ValueError):
        decode_cursor('synthetic-key',Filters.parse(MultiDict({'limit':'3','categories':'vitals,appointments'})).binding(**scope),cursor)
    with pytest.raises(ValueError):
        decode_cursor('another-key',b,cursor)
    with pytest.raises(ValueError):
        decode_cursor('synthetic-key',b,cursor+'tampered')
