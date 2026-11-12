import http.client
import json
import socket
import threading
import time

from tests.helpers import TempDirTest, fq


class ServerTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.store = fq.Store(self.tmp / 'db.sqlite3')
        self.service = fq.Service(self.store)
        self.service.set_workspace(fq.WorkspaceState(printer='Printer A', slots=['Generic PLA', 'Generic PLA'],
                                                     profiles=['Generic PLA', 'Generic PETG']))
        self.spool = self.store.add_spool('Generic PETG', 'Printer A', 'PETG', '<i>brand</i>')
        self.server = fq.FilamentHttpServer(self.service)
        self.host, self.port = self.server.start('127.0.0.1', 0)

    def tearDown(self):
        self.assertTrue(self.server.stop())
        super().tearDown()

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        hdrs = {'Content-Type': 'application/json'} if body is not None else {}
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp, payload

    def login(self):
        resp, _ = self.request('POST', '/api/auth', {'pin': self.service.sessions.pin})
        self.assertEqual(resp.status, 200)
        cookie = resp.getheader('Set-Cookie')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Max-Age=', cookie)
        return {'Cookie': cookie.split(';')[0]}

    def test_phone_page_has_security_headers(self):
        resp, body = self.request('GET', '/')
        self.assertEqual(resp.status, 200)
        self.assertIn(b'Filament QR', body)
        self.assertEqual(resp.getheader('X-Frame-Options'), 'DENY')
        self.assertIn("default-src 'none'", resp.getheader('Content-Security-Policy'))

    def test_api_requires_session(self):
        for method, path in (('GET', '/api/status'), ('POST', '/api/scan'), ('POST', '/api/decode')):
            resp, _ = self.request(method, path, {} if method == 'POST' else None)
            self.assertEqual(resp.status, 401, path)
        resp, _ = self.request('POST', '/api/auth', {'pin': 'wrong'})
        self.assertEqual(resp.status, 403)

    def test_scan_queues_a_durable_request(self):
        auth = self.login()
        resp, body = self.request('POST', '/api/scan', {'code': self.spool['code'][3:], 'slot': 1}, auth)
        self.assertEqual(resp.status, 200)
        pending = json.loads(body)['pending']
        self.assertEqual((pending['slot'], pending['profile'], pending['status']), (1, 'Generic PETG', 'queued'))
        self.assertTrue(self.service.queue_changed.is_set())
        self.assertEqual(len(self.store.list_pending()), 1)

        resp, body = self.request('GET', '/api/status', headers=auth)
        status = json.loads(body)
        self.assertEqual(status['slots'], ['Generic PLA', 'Generic PLA'])
        self.assertEqual(len(status['pending']), 1)

    def test_scan_validation(self):
        auth = self.login()
        cases = [({'code': 'FQ-000000'}, 404), ({'code': 'nope'}, 400),
                 ({'code': self.spool['code'], 'slot': 7}, 400), ({'code': self.spool['code'], 'slot': 'x'}, 400),
                 ({'code': self.spool['code'], 'slot': -1}, 400)]
        for body, expected in cases:
            resp, _ = self.request('POST', '/api/scan', body, auth)
            self.assertEqual(resp.status, expected, body)
        self.assertEqual(self.store.list_pending(), [])

    def test_malformed_bodies(self):
        auth = self.login()
        resp, _ = self.request('POST', '/api/scan', raw=b'{not json', headers=dict(auth, **{'Content-Type': 'x'}))
        self.assertEqual(resp.status, 400)
        resp, _ = self.request('POST', '/api/scan', raw=b'[1,2]', headers=auth)
        self.assertEqual(resp.status, 400)
        resp, _ = self.request('POST', '/api/scan', raw=b'x' * (fq.MAX_JSON_BODY + 1), headers=auth)
        self.assertEqual(resp.status, 400)

    def test_bad_content_length_is_rejected(self):
        for header in (b'Content-Length: -5', b'Content-Length: abc', b''):
            with socket.create_connection((self.host, self.port), timeout=5) as sock:
                sock.sendall(b'POST /api/auth HTTP/1.0\r\n' + header + b'\r\n\r\n')
                reply = sock.recv(4096)
            self.assertIn(b' 400 ', reply.split(b'\r\n')[0], header)

    def test_logout_and_pin_rotation_invalidate_sessions(self):
        auth = self.login()
        resp, _ = self.request('POST', '/api/logout', {}, auth)
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.request('GET', '/api/status', headers=auth)[0].status, 401)

        auth = self.login()
        self.service.sessions.rotate_pin()
        self.assertEqual(self.request('GET', '/api/status', headers=auth)[0].status, 401)

    def test_label_escapes_text_and_requires_auth_off_loopback(self):
        resp, body = self.request('GET', '/label/' + self.spool['code'])
        self.assertEqual(resp.status, 200)
        self.assertIn(b'&lt;i&gt;brand', body)
        self.assertNotIn(b'<i>brand', body)
        self.assertEqual(self.request('GET', '/label/FQ-000000')[0].status, 404)
        self.assertEqual(self.request('GET', '/label/..%2F..%2Fetc')[0].status, 400)

    def test_unknown_route(self):
        self.assertEqual(self.request('GET', '/nope')[0].status, 404)

    def test_excess_connections_get_503_promptly(self):
        idle = []
        try:
            for _ in range(fq.MAX_HTTP_WORKERS):
                sock = socket.create_connection((self.host, self.port), timeout=5)
                sock.sendall(b'GET / HTTP/1.0\r\n')  # incomplete request keeps a worker busy
                idle.append(sock)
            time.sleep(0.3)
            started = time.monotonic()
            resp, _ = self.request('GET', '/')
            self.assertEqual(resp.status, 503)
            self.assertLess(time.monotonic() - started, 2)
        finally:
            for sock in idle:
                sock.close()

    def test_busy_decoder_returns_429(self):
        auth = self.login()
        for _ in range(fq.MAX_CONCURRENT_DECODES):
            self.service.decode_slots.acquire()
        try:
            resp, _ = self.request('POST', '/api/decode', raw=b'img', headers=auth)
            self.assertEqual(resp.status, 429)
        finally:
            for _ in range(fq.MAX_CONCURRENT_DECODES):
                self.service.decode_slots.release()

    def test_stop_is_bounded_with_inflight_request_and_idempotent(self):
        sock = socket.create_connection((self.host, self.port), timeout=5)
        sock.sendall(b'POST /api/auth HTTP/1.0\r\nContent-Length: 100\r\n\r\n')
        started = time.monotonic()
        self.server.stop(timeout=fq.REQUEST_TIMEOUT + 2)
        self.assertLess(time.monotonic() - started, fq.REQUEST_TIMEOUT + 3)
        sock.close()
        self.assertTrue(self.server.stop())
        with self.assertRaises(OSError):
            socket.create_connection((self.host, self.port), timeout=1).close()
        self.server.start('127.0.0.1', 0)  # tearDown stops it again


class BindConflictTest(TempDirTest):
    def test_occupied_port_gives_actionable_error(self):
        blocker = socket.socket()
        blocker.bind(('127.0.0.1', 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        server = fq.FilamentHttpServer(fq.Service(fq.Store(self.tmp / 'db.sqlite3')))
        try:
            with self.assertRaises(RuntimeError) as ctx:
                server.start('127.0.0.1', port)
            self.assertIn(str(port), str(ctx.exception))
        finally:
            blocker.close()
            server.stop()
