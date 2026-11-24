"""Ephemeral synthetic-only RAD5 same-origin OHIF/DICOMweb gateway.

NOT wired into Flask and NOT approved for real clinical images. Loopback only.
Only A and B synthetic identities are granted one exact StudyInstanceUID each.
"""
from __future__ import annotations

import base64
import binascii
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import re
from urllib.parse import urlsplit

from rad5_proxy_contract import ScopeDenied, allowed_dicomweb_target, map_peer_access

CAP = 18 * 1024 * 1024


class TestGateway(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port, *, orthanc_port, ohif_port, orthanc_auth, users, studies):
        if not (isinstance(port, int) and 0 <= port <= 65535):
            raise ValueError('Local gateway port required')
        if set(users) != {'A', 'B'} or set(studies) != {'A', 'B'}:
            raise ValueError('Only two synthetic identities')
        self.orthanc_port = orthanc_port
        self.ohif_port = ohif_port
        self.orthanc_auth = orthanc_auth
        self.users = users
        self.studies = studies
        for p in (orthanc_port, ohif_port):
            if not isinstance(p, int) or not 1 <= p <= 65535:
                raise ValueError('Invalid local backend port')
        super().__init__(('127.0.0.1', port), TestHandler)


class TestHandler(BaseHTTPRequestHandler):
    server: TestGateway
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        # Never log Authorization, study UIDs, or query strings.
        pass

    def _send(self, status, content=b'', ctype='text/plain', extra=None):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        for k,v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if content:
            self.wfile.write(content)

    def _who(self):
        raw = self.headers.get('Authorization', '')
        if not raw.startswith('Basic ') or len(raw) > 280:
            return None
        try:
            data = base64.b64decode(raw[6:], validate=True).decode('ascii')
        except (ValueError, UnicodeError, binascii.Error):
            return None
        if data.count(':') != 1:
            return None
        name,password = data.split(':',1)
        for subject,expected in self.server.users.items():
            if hmac.compare_digest(name, 'rad5-'+subject) and hmac.compare_digest(password, expected):
                return subject
        return None

    def _safe_path(self):
        if len(self.path) > 1024 or any(ord(c)<32 for c in self.path):
            raise ScopeDenied('Invalid path')
        if '%' in self.path or '\\' in self.path or '//' in self.path or '#' in self.path:
            raise ScopeDenied('Ambiguous path')
        parts=urlsplit(self.path)
        if parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith('/'):
            raise ScopeDenied('Nonlocal URL')
        if any(p in ('.','..') for p in parts.path.split('/')):
            raise ScopeDenied('Traversal')
        return parts

    def do_POST(self):
        self._send(405)

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST
    do_OPTIONS = do_POST

    def do_GET(self):
        who=self._who()
        if who is None:
            self._send(401,b'Authentication required', extra={
                'WWW-Authenticate':'Basic realm="RAD5 SYNTHETIC ONLY", charset="UTF-8"'})
            return
        try:
            target=self._safe_path()
            path=target.path
            if path.startswith('/dicom-web/'):
                study=map_peer_access(who,self.server.studies)
                safe=allowed_dicomweb_target(self.path,study)
                self._forward(self.server.orthanc_port,safe,dicom=True)
            else:
                # Never expose a native Orthanc admin, runtime auth or debug endpoint.
                if (path.startswith(('/orthanc','/pacs','/system','/instances','/patients',
                                     '/plugins','/auth','/tools','/modalities','/dicom-web'))):
                    raise ScopeDenied('Forbidden route')
                if path=='/':
                    safe='/'
                else:
                    safe=self.path
                self._forward(self.server.ohif_port,safe,dicom=False)
        except ScopeDenied:
            self._send(404,b'Resource unavailable')
        except (OSError, ValueError, TimeoutError):
            self._send(502,b'Upstream unavailable')

    def _forward(self,port,path,*,dicom):
        conn=HTTPConnection('127.0.0.1',port,timeout=16)
        headers={
            'Accept':(self.headers.get('Accept') or '*/*')[:256],
            'Accept-Encoding':'identity',
        }
        if dicom:
            headers['Authorization']=self.server.orthanc_auth
            # Request absolute DICOMweb links for this gateway origin.
            host=self.headers.get('Host','')
            if not re.fullmatch(r'(?:127\.0\.0\.1|localhost):[0-9]{1,5}',host):
                raise ScopeDenied('Invalid gateway Host')
            headers['Host']=host
            headers['X-Forwarded-Host']=host
            headers['X-Forwarded-Proto']='http'
        try:
            conn.request('GET',path,headers=headers)
            resp=conn.getresponse()
            status=resp.status
            content=resp.read(CAP+1)
            ctype=resp.getheader('Content-Type','application/octet-stream')
        finally:
            conn.close()
        if len(content)>CAP:
            raise ScopeDenied('Oversized source response')
        if status >= 300:
            # Never pass through upstream redirects or potentially sensitive errors.
            self._send(404 if status==404 else 502,b'Resource unavailable')
            return
        if status!=200:
            self._send(502,b'Unexpected upstream status')
            return
        if dicom and ('json' in ctype.lower()):
            # Prevent direct Orthanc URLs from escaping into browser metadata.
            try:
                parsed=json.loads(content)
                forwarded=f'http://127.0.0.1:{self.server.server_port}/dicom-web/'
                rendered=json.dumps(parsed,separators=(',',':'),ensure_ascii=True).encode()
                for origin in ('127.0.0.1','localhost'):
                    original=f'http://{origin}:{port}/dicom-web/'
                    rendered=rendered.replace(original.encode(),forwarded.encode())
                if (f':{port}/'.encode() in rendered or
                        b'"http://orthanc:' in rendered):
                    raise ScopeDenied('Unscoped private archive URL')
                content=rendered
            except (TypeError,ValueError,UnicodeError) as exc:
                raise ScopeDenied('Invalid upstream JSON') from exc
        self._send(200,content,ctype)
