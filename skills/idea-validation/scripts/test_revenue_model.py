import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import revenue_model as m


def p(lo, mid, hi, src='x'):
    return {'low': lo, 'mid': mid, 'high': hi, 'source': src}


def base():
    return {'currency': 'USD', 'params': {
        'monthly_searches': p(1000, 2000, 4000),
        'click_share': p(0.02, 0.05, 0.1),
        'visit_to_signup': p(0.05, 0.1, 0.2),
        'signup_to_paid': p(0.02, 0.05, 0.1),
        'price_per_month': p(10, 20, 30),
        'months_retained': p(1, 3, 6),
    }}


class Test(unittest.TestCase):
    def test_bottom_up_mid(self):
        out = m.run(base())['scenarios']['mid']['bottom_up']
        # 2000 × 0.05 × 0.1 × 0.05 = 0.5 人/月
        self.assertAlmostEqual(out['new_paid_per_month'], 0.5)
        self.assertAlmostEqual(out['steady_monthly_revenue'], 0.5 * 20 * 3)
        # 12 个月累计：第 1、2 月爬坡，之后每月 30
        self.assertAlmostEqual(out['revenue_12m'], 0.5 * 20 * (1 + 2 + 3 * 10))

    def test_one_off_price(self):
        model = base()
        model['params']['months_retained'] = p(1, 1, 1)
        out = m.run(model)['scenarios']['mid']['bottom_up']
        self.assertAlmostEqual(out['revenue_12m'], 0.5 * 20 * 12)

    def test_acquisition(self):
        model = base()
        model['params']['cpc'] = p(1, 2, 4)
        a = m.run(model)['scenarios']['mid']['acquisition']
        self.assertAlmostEqual(a['cac'], 2 / (0.1 * 0.05))
        self.assertAlmostEqual(a['ltv'], 60)
        self.assertAlmostEqual(a['ltv_to_cac'], 60 / 400)

    def test_top_down_optional(self):
        out = m.run(base())['scenarios']['mid']
        self.assertIsNone(out['top_down'])
        self.assertIsNone(out['acquisition'])
        model = base()
        model['params'].update(competitor_monthly_visits=p(1e4, 2e4, 4e4), visit_to_paid=p(0.01, 0.02, 0.03),
                               your_share=p(0.01, 0.05, 0.1))
        t = m.run(model)['scenarios']['mid']['top_down']
        self.assertAlmostEqual(t['competitor_monthly_revenue'], 2e4 * 0.02 * 20)
        self.assertAlmostEqual(t['your_monthly_revenue'], 2e4 * 0.02 * 20 * 0.05)

    def test_sensitivity_sorted(self):
        rows = m.run(base())['sensitivity']
        swings = [r['swing'] for r in rows]
        self.assertEqual(swings, sorted(swings, reverse=True))
        self.assertEqual(len(rows), len(m.BOTTOM_UP))

    def test_missing_source_is_assumption(self):
        model = base()
        model['params']['signup_to_paid']['source'] = ''
        self.assertEqual(m.run(model)['assumptions'], ['signup_to_paid'])

    def test_target(self):
        model = base()
        model['target_monthly_revenue'] = 25
        t = m.run(model)['target']
        self.assertEqual(t, {'low': False, 'mid': True, 'high': True})

    def test_rejects_bad_order(self):
        model = base()
        model['params']['click_share'] = p(0.1, 0.05, 0.2)
        with self.assertRaises(m.ModelError):
            m.run(model)

    def test_rejects_rate_over_one(self):
        model = base()
        model['params']['click_share'] = p(0.1, 0.5, 1.5)
        with self.assertRaises(m.ModelError):
            m.run(model)

    def test_rejects_missing_and_unknown(self):
        model = base()
        del model['params']['price_per_month']
        with self.assertRaises(m.ModelError):
            m.run(model)
        model = base()
        model['params']['foo'] = p(1, 2, 3)
        with self.assertRaises(m.ModelError):
            m.run(model)

    def test_template_placeholders_rejected_cleanly(self):
        import json
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, 'model-template.json'), encoding='utf-8') as f:
            tpl = json.load(f)
        out = m.run(tpl)  # 全零模板也能跑通，只是收入为 0
        self.assertEqual(out['scenarios']['mid']['bottom_up']['revenue_12m'], 0)

    def test_render_runs(self):
        model = base()
        model['params']['cpc'] = p(1, 2, 4)
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            m.render(model, m.run(model))


if __name__ == '__main__':
    unittest.main()
