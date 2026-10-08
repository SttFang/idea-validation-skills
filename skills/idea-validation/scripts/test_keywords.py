import contextlib
import csv
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import keywords as kw


class Test(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def csv(self, name, rows):
        p = os.path.join(self.d, name)
        with open(p, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
        return p

    def run_cmd(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = kw.main(list(args))
        return code, buf.getvalue()

    def test_variants_count_once(self):
        p = self.csv('k.csv', [dict(keyword='sync claude', volume='90', trend='1,2'),
                               dict(keyword='claude sync', volume='90', trend='1,2'),
                               dict(keyword='other', volume='90', trend='3,4')])
        self.run_cmd('variants', p)
        rows = kw.read_csv(p)
        self.assertEqual(sum(r['count_volume'] == '是' for r in rows), 2)

    def test_cluster_by_shared_urls(self):
        serp = {'a': {'urls': ['u1', 'u2', 'u3', 'u4']}, 'b': {'urls': ['u1', 'u2', 'u3', 'x']},
                'c': {'urls': ['u1', 'y', 'z', 'w']}}
        sp = os.path.join(self.d, 's.json'); json.dump(serp, open(sp, 'w'))
        p = self.csv('k.csv', [dict(keyword='a', volume='30'), dict(keyword='b', volume='20'), dict(keyword='c', volume='10')])
        self.run_cmd('cluster', p, '--serp', sp)
        cl = {r['keyword']: r['cluster'] for r in kw.read_csv(p)}
        self.assertEqual(cl['b'], 'a')
        self.assertEqual(cl['c'], 'c')

    def test_metrics_precision_coverage_and_chapman(self):
        rows = [dict(keyword='how to sync claude settings', scenario='多设备', stage='问题', seed_class='问题',
                     source='社区原话', intent_fit='匹配', collision='否', cluster='c1'),
                dict(keyword='claude md doctor', scenario='多设备', stage='方案', seed_class='品类',
                     source='模板', intent_fit='不匹配', collision='是', cluster='c2'),
                dict(keyword='share skills with team', scenario='团队', stage='方案', seed_class='身份任务',
                     source='模板', intent_fit='混合', collision='否', cluster='c3')]
        p = self.csv('k.csv', rows)
        r = self.csv('r.csv', [dict(phrase='sync claude settings across machines', source='hn', upstream='hn'),
                               dict(phrase='team skill library', source='reddit', upstream='reddit')])
        code, out = self.run_cmd('metrics', p, '--reference', r, '--json')
        m = json.loads(out)
        self.assertAlmostEqual(m['intent_precision'], 0.5)
        self.assertEqual(m['kept'], 2)
        self.assertEqual(m['collisions'], 1)
        self.assertAlmostEqual(m['cell_coverage'], 2 / 8)
        self.assertAlmostEqual(m['recall'], 1.0)
        self.assertAlmostEqual(m['chapman_total'], round((2 + 1) * (2 + 1) / (2 + 1) - 1, 1))


if __name__ == '__main__':
    unittest.main()
