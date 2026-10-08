import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(__file__))
import ar


class Fake(BaseHTTPRequestHandler):
    script = {}      # (method, path 前缀) -> 依次返回的 (状态码, 头, 返回体) 列表
    seen = []

    def log_message(self, *a):
        pass

    def handle_any(self, method):
        Fake.seen.append((method, self.path, self.headers.get('Authorization')))
        if method == 'POST':
            self.rfile.read(int(self.headers.get('Content-Length') or 0))
        for (m, prefix), queue in Fake.script.items():
            if m == method and self.path.startswith(prefix) and queue:
                code, headers, body = queue.pop(0) if len(queue) > 1 else queue[0]
                break
        else:
            code, headers, body = 404, {}, {'status': 'error', 'code': 'not_found'}
        raw = json.dumps(body).encode()
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.handle_any('GET')

    def do_POST(self):
        self.handle_any('POST')


def ok(data):
    return (200, {}, {'status': 'success', 'data': data})


class Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(('127.0.0.1', 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        ar.BASE = f'http://127.0.0.1:{cls.srv.server_port}'
        ar.time.sleep = lambda s: None

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Fake.script, Fake.seen = {}, []
        self.dir = tempfile.mkdtemp()
        os.environ['APISROUTER_API_KEY'] = 'test-key'

    def run_cmd(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                ar.main(list(args))
                code = 0
            except SystemExit as e:
                code, msg = 1, str(e)
                out.write(msg)
        return code, out.getvalue()

    def buy(self):
        return self.run_cmd('buy', 'x.v1', '--input', '{"k":1}', '--max-usd', '1',
                            '--out', os.path.join(self.dir, 'o.json'), '--ledger', os.path.join(self.dir, 'l.jsonl'))

    def test_catalog_sends_key_when_present(self):
        Fake.script = {('GET', '/catalog'): [ok([{'operation_ref': 'x.v1', 'unit_price': '0.1', 'title': 't'}])]}
        self.run_cmd('search', 'x')
        self.assertEqual(Fake.seen[-1][2], 'Bearer test-key')

    def test_catalog_anonymous_without_key(self):
        os.environ['APISROUTER_API_KEY'] = ''
        home = os.environ.get('HOME')
        os.environ['HOME'] = self.dir
        try:
            Fake.script = {('GET', '/catalog'): [ok([])]}
            code, _ = self.run_cmd('search', 'x')
        finally:
            os.environ['HOME'] = home
        self.assertEqual(code, 0)
        self.assertIsNone(Fake.seen[-1][2])

    def test_conflict_with_existing_continues(self):
        Fake.script = {
            ('POST', '/quotes'): [ok({'quote_ref': 'q2', 'maximum_amount': '0.005'})],
            ('POST', '/requests'): [(409, {}, {'status': 'error', 'code': 'idempotency_conflict', 'summary': 's',
                                               'data': {'existing': {'request_ref': 'r1', 'status': 'executing'}}})],
            ('GET', '/requests/r1'): [ok({'request_ref': 'r1', 'status': 'completed', 'result_ref': 'res1'})],
            ('GET', '/results/res1'): [ok({'records': [{'items': [1, 2]}], 'total': 1})],
        }
        code, out = self.buy()
        self.assertEqual(code, 0, out)
        self.assertIn('不会重复扣费', out)
        self.assertEqual(sum(1 for m, p, _ in Fake.seen if m == 'POST' and p == '/requests'), 1)

    def test_conflict_without_existing_exits(self):
        Fake.script = {
            ('POST', '/quotes'): [ok({'quote_ref': 'q2', 'maximum_amount': '0.005'})],
            ('POST', '/requests'): [(409, {}, {'status': 'error', 'code': 'idempotency_conflict', 'data': {}})],
        }
        code, out = self.buy()
        self.assertEqual(code, 1)
        self.assertIn('旧服务端', out)

    def test_poll_429_waits_and_retries(self):
        Fake.script = {
            ('POST', '/quotes'): [ok({'quote_ref': 'q1', 'maximum_amount': '0.005'})],
            ('POST', '/requests'): [ok({'request_ref': 'r1', 'status': 'executing'})],
            ('GET', '/requests/r1'): [(429, {'Retry-After': '3'}, {'status': 'error', 'code': 'rate_limited'}),
                                      ok({'request_ref': 'r1', 'status': 'completed', 'result_ref': 'res1'})],
            ('GET', '/results/res1'): [ok({'records': [{'items': [1]}], 'total': 1})],
        }
        code, out = self.buy()
        self.assertEqual(code, 0, out)
        self.assertEqual(sum(1 for m, p, _ in Fake.seen if p == '/requests/r1'), 2)

    def test_resume_from_submitted_row_without_new_quote(self):
        ledger = os.path.join(self.dir, 'l.jsonl')
        h = ar.input_hash('x.v1', {'k': 1})
        with open(ledger, 'w') as f:
            f.write(json.dumps({'at': ar.time.time(), 'operation_ref': 'x.v1', 'input_hash': h, 'request_ref': 'r9',
                                'status': 'submitted', 'charged_max_usd': '0.005'}) + '\n')
        Fake.script = {
            ('GET', '/requests/r9'): [ok({'request_ref': 'r9', 'status': 'completed', 'result_ref': 'res9'})],
            ('GET', '/results/res9'): [ok({'records': [{'items': [1]}], 'total': 1})],
        }
        code, out = self.buy()
        self.assertEqual(code, 0, out)
        self.assertFalse(any(p == '/quotes' for _, p, _ in Fake.seen))
        rows = [json.loads(l) for l in open(ledger)]
        self.assertEqual(ar.ledger_spent(rows), ar.Decimal('0.005'))

    def test_submitted_row_written_before_polling(self):
        Fake.script = {
            ('POST', '/quotes'): [ok({'quote_ref': 'q1', 'maximum_amount': '0.005'})],
            ('POST', '/requests'): [ok({'request_ref': 'r1', 'status': 'executing'})],
            ('GET', '/requests/r1'): [(500, {}, {'status': 'error', 'code': 'internal'})],
        }
        code, out = self.buy()
        self.assertEqual(code, 1)
        rows = [json.loads(l) for l in open(os.path.join(self.dir, 'l.jsonl'))]
        self.assertEqual(rows[0]['request_ref'], 'r1')
        self.assertEqual(rows[0]['status'], 'submitted')


if __name__ == '__main__':
    unittest.main()
