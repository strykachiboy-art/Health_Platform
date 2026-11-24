"""Bounded server-side Orthanc MWL transport. Isolated testing profile only.
No request-supplied hosts, credentials, paths, redirects, or retries.
"""
import base64
import http.client
import json
import re

_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
_KEY = re.compile(r'CSP-RADC-B02A-[0-9a-f]{12}\Z')


class ArchiveUnconfirmed(RuntimeError):
    pass


def valid_id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


class StagedOrthanc:
    def __init__(self, *, port, username, password, archive_key):
        if (type(port) is not int or not 1 <= port <= 65535
                or not isinstance(archive_key, str) or not _KEY.fullmatch(archive_key)
                or not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_]{8,64}', username)
                or not isinstance(password, str) or not re.fullmatch(r'[A-Za-z0-9_-]{24,128}', password)):
            raise ArchiveUnconfirmed('Unapproved staged archive configuration')
        self.port = port
        self.archive_key = archive_key
        self._auth = 'Basic ' + base64.b64encode((username + ':' + password).encode('ascii')).decode('ascii')

    def _request(self, method, path, body=None, *, missing=False):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=8)
        try:
            data = None if body is None else json.dumps(body, ensure_ascii=True).encode('ascii')
            conn.request(method, path, body=data, headers={'Authorization': self._auth,
                'Content-Type': 'application/json', 'Accept': 'application/json'})
            res = conn.getresponse()
            if res.status == 404 and missing:
                return None
            if res.status not in (200, 201, 204):
                raise ArchiveUnconfirmed('Archive response unconfirmed')
            raw = res.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ArchiveUnconfirmed('Archive response exceeds bound')
            return json.loads(raw) if raw else {}
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise ArchiveUnconfirmed('Archive response unconfirmed') from exc
        finally:
            conn.close()

    def check(self):
        system = self._request('GET', '/system')
        if not isinstance(system, dict) or system.get('Name') != self.archive_key:
            raise ArchiveUnconfirmed('Unexpected staged archive identity')

    def list(self):
        self.check()
        result = self._request('GET', '/worklists/?format=Simplify')
        if not isinstance(result, list) or len(result) > 100:
            raise ArchiveUnconfirmed('Archive inventory requires reconciliation')
        return result

    def create(self, tags):
        self.check()
        result = self._request('POST', '/worklists/create', {'Tags': tags})
        if not isinstance(result, dict) or not valid_id(result.get('ID')):
            raise ArchiveUnconfirmed('Worklist identity unconfirmed')
        return result['ID']

    def get(self, external_id):
        if not valid_id(external_id):
            raise ArchiveUnconfirmed('Invalid external identity')
        self.check()
        return self._request('GET', '/worklists/' + external_id + '?format=Simplify', missing=True)

    def delete(self, external_id):
        if not valid_id(external_id):
            raise ArchiveUnconfirmed('Invalid external identity')
        self.check()
        self._request('DELETE', '/worklists/' + external_id)
