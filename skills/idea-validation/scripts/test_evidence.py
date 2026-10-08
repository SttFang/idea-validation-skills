import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import evidence as ev


def row(i, author, codes, quote='I hate syncing my CLAUDE.md', src=None, **kw):
    d = {'id': f'E{i}', 'platform': 'reddit', 'community': 'r/ClaudeAI', 'url': f'https://x/{i}',
         'author': author, 'quote': quote, 'source_file': src, 'codes': codes}
    d.update(kw)
    return d


class Test(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.src = os.path.join(self.dir, 'raw.json')
        json.dump({'records': [{'items': [{'body': 'Honestly  I hate syncing my\nCLAUDE.md across 3 machines'}]}]},
                  open(self.src, 'w'))

    def write(self, rows):
        p = os.path.join(self.dir, 'e.jsonl')
        with open(p, 'w') as f:
            for r in rows:
                f.write(json.dumps(r) + '\n')
        return p

    def run_cmd(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ev.main(list(args))
        return code, buf.getvalue()

    def test_verify_passes_whitespace_and_case(self):
        p = self.write([row(1, 'a', ['sync'], quote='i hate syncing my CLAUDE.md', src=self.src)])
        code, out = self.run_cmd('verify', p)
        self.assertEqual(code, 0, out)

    def test_verify_rejects_fabricated_quote(self):
        p = self.write([row(1, 'a', ['sync'], quote='I would pay $20 for this', src=self.src)])
        code, out = self.run_cmd('verify', p)
        self.assertEqual(code, 1)
        self.assertIn('找不到', out)

    def test_verify_rejects_bad_enums_and_dup(self):
        p = self.write([row(1, 'a', ['s'], src=self.src, wtp_level='L9', stage='想要', severity=7),
                        row(1, 'b', ['s'], src=self.src)])
        code, out = self.run_cmd('verify', p)
        self.assertEqual(code, 1)
        for s in ('wtp_level', 'stage', 'severity', 'id 重复'):
            self.assertIn(s, out)

    def test_stats_counts_distinct_authors(self):
        rows = [row(i, 'same', ['sync'], src=self.src) for i in range(10)]
        rows += [row(20 + i, f'u{i}', ['sync'], src=self.src, community='r/cursor', stage='主动寻找', wtp_level='L4')
                 for i in range(4)]
        p = self.write(rows)
        code, out = self.run_cmd('stats', p)
        self.assertIn('| sync | 5 | 2 | 1 | 100% | L4 | 是 |', out)
        self.assertIn('每人一票', out)

    def test_saturation(self):
        rows, n = [], 0
        for i in range(60):
            codes = [f'c{i}'] if i < 25 else ['c1']
            rows.append(row(n, f'u{i}', codes, src=self.src)); n += 1
        code, out = self.run_cmd('saturation', self.write(rows), '--batch', '10')
        self.assertEqual(code, 0, out)
        rows = [row(i, f'u{i}', [f'c{i}'], src=self.src) for i in range(60)]
        code, out = self.run_cmd('saturation', self.write(rows), '--batch', '10')
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
