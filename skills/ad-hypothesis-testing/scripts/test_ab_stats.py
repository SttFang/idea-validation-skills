import importlib.util
from pathlib import Path
import time
import unittest


MODULE = Path(__file__).with_name('ab_stats.py')
FUTURE = '2999-01-01T00:00:00+00:00'
PAST = '2000-01-01T00:00:00+00:00'


def load():
    spec = importlib.util.spec_from_file_location('ab_stats', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hyp(**over):
    h = {'id': 'H1', 'group': 'faceless-channel', 'role': 'confirmatory', 'metric': 'ctr', 'unit': 'impression',
         'baseline_rate': 0.069, 'baseline_source': 'example: same keyword group, prior 3 days',
         'mde_lift_ratio': 2.0, 'planned_n_per_arm': 310, 'daily_units': 220}
    h.update(over)
    return h


def prereg(hyps=None, **over):
    base = {'experiment_id': 'kw-msg-2026-10', 'alpha': 0.05, 'power': 0.8, 'sides': 'two',
            'multiple_comparison': 'holm', 'srm_alert_p': 0.01,
            'split_method': 'google_ads_experiment_cookie', 'interim_looks': 0,
            'budget': 300, 'cpc': 1.0, 'window_days': 3,
            'design_effect': {'source': 'first_party_click_histogram', 'floor': 1.0, 'band': [1.0, 1.5, 2.0],
                              'min_match_rate': 0.7},
            'audience': {'devices': ['desktop'], 'locations': ['US'], 'location_mode': 'presence',
                         'schedule': 'all hours', 'exclusions': ['existing users', 'team IPs'],
                         'evidence': 'example: prior sign-ups by device, desktop 17/17 payers, mobile 0/20',
                         'confirmed_by': 'founder', 'confirmed_at': '2026-10-05'},
            'guardrails': {'search_terms_overlap_min': 0.6, 'invalid_click_rate_max': 0.15,
                           'both_arms_visitor_share_max': 0.05},
            'decisions': {v: {'action': f'{v} 时的行动', 'step': 'confirm' if v in ('FRAGILE', 'SCREEN_PASS') else 'act'}
                          for v in ('TEST_BETTER', 'CONTROL_BETTER', 'FRAGILE', 'NEUTRAL', 'INCONCLUSIVE', 'INVALID',
                                    'SCREEN_PASS', 'SCREEN_CONTROL_BETTER')},
            'hypotheses': hyps or [hyp()]}
    base.update(over)
    return base


def daily(days=3, t=(107, 15), c=(106, 6)):
    return [{'date': f'2026-10-{i + 1:02d}', 'test_n': t[0], 'test_x': t[1], 'control_n': c[0], 'control_x': c[1]}
            for i in range(days)]


KEYS = ('test_n', 'test_x', 'control_n', 'control_x')


def split(r, days):
    out = [{'date': f'2026-10-{i + 1:02d}'} for i in range(days)]
    for k in KEYS:
        q, rem = divmod(r[k], days)
        for i, d in enumerate(out):
            d[k] = q + (1 if i < rem else 0)
    return out


def result(days=3, **over):
    """按天数据默认由总数均分；显式给 daily 时总数取按天合计。"""
    explicit = over.pop('daily', None)
    r = {'id': 'H1', 'test_n': 320, 'test_x': 45, 'control_n': 318, 'control_x': 18}
    r.update(over)
    if explicit is None:
        r['daily'] = split(r, days)
    else:
        r['daily'] = explicit
        r.update({k: sum(d[k] for d in explicit) for k in KEYS})
    # 默认每个点击来自不同访客，关联率 100%：设计效应实测为 1
    r.setdefault('test_histogram', {'1': r['test_x']})
    r.setdefault('control_histogram', {'1': r['control_x']})
    r.setdefault('gclid_match_rate', {'test': 1.0, 'control': 1.0})
    return r


def data(locked, results, **over):
    d = {'experiment_id': locked['experiment_id'], 'prereg_hash': locked['prereg_hash'],
         'launch_at': FUTURE, 'look': 'final', 'days_run': 3, 'results': results}
    d.update(over)
    return d


class PlanTest(unittest.TestCase):
    def test_sample_size(self):
        m = load()
        self.assertEqual(m.n_per_arm(0.069, 2.0, alpha=0.05, power=0.8, sides='two'), 305)
        self.assertEqual(m.n_per_arm(0.069, 2.0, alpha=0.05, power=0.8, sides='one'), 240)

    def test_plan_is_internally_consistent(self):
        m = load()
        for budget in (30, 50, 75, 160, 600):
            p = m.plan(baseline_rate=0.069, mde_lift_ratio=2.0, budget=budget, cpc=1.0)
            self.assertEqual(p['feasible'], p['n_per_arm_affordable'] >= p['n_per_arm'])
            if p['achievable_mde_lift_ratio'] is not None:
                self.assertEqual(p['feasible'], p['achievable_mde_lift_ratio'] <= 2.0 + 1e-9)

    def test_plan_conversion_metric_costs_per_visitor(self):
        m = load()
        p = m.plan(baseline_rate=0.10, mde_lift_ratio=2.0, budget=100, cpc=1.0, metric='conversion')
        self.assertEqual(p['n_per_arm'], m.n_per_arm(0.10, 2.0))
        self.assertAlmostEqual(p['required_budget'], 2 * p['n_per_arm'] * 1.0, places=2)

    def test_plan_reports_days_needed_and_method(self):
        m = load()
        p = m.plan(baseline_rate=0.069, mde_lift_ratio=2.0, budget=500, cpc=1.0, daily_units=200)
        self.assertEqual(p['days_needed'], -(-2 * p['n_per_arm'] // 200))
        self.assertEqual(p['method'], 'barnard')


class MethodSelectionTest(unittest.TestCase):
    def test_small_and_medium_samples_use_exact_test(self):
        m = load()
        self.assertEqual(m.choose_method(60, 0.069, 2.0)['method'], 'barnard')
        self.assertEqual(m.choose_method(800, 0.069, 1.5)['method'], 'barnard')

    def test_large_samples_use_z_test(self):
        m = load()
        threshold = m.skewness_threshold(0.06)
        self.assertGreater(threshold, 4000)
        self.assertEqual(m.choose_method(threshold + 10, 0.06, 1.2)['method'], 'z_pooled')

    def test_method_is_locked_and_used(self):
        m = load()
        locked = m.lock(prereg())
        self.assertEqual(locked['methods']['H1'], 'barnard')
        rep = m.analyze(locked, data(locked, [result()]))
        self.assertEqual(rep['hypotheses'][0]['method'], 'barnard')

    def test_z_test_path(self):
        m = load()
        big = m.skewness_threshold(0.06) + 500
        locked = m.lock(prereg([hyp(baseline_rate=0.06, mde_lift_ratio=1.25, planned_n_per_arm=big, daily_units=big)],
                               budget=10 ** 6))
        self.assertEqual(locked['methods']['H1'], 'z_pooled')
        rep = m.analyze(locked, data(locked, [result(test_n=big, test_x=int(big * 0.075), control_n=big,
                                                     control_x=int(big * 0.06))]))
        self.assertEqual(rep['hypotheses'][0]['method'], 'z_pooled')
        self.assertEqual(rep['hypotheses'][0]['verdict'], 'TEST_BETTER')


class BarnardTest(unittest.TestCase):
    def test_matches_scipy_documented_example(self):
        m = load()
        self.assertAlmostEqual(m.barnard_two_sided(7, 15, 12, 15), 0.068, delta=0.002)

    def test_symmetric_and_bounded(self):
        m = load()
        a = m.barnard_two_sided(12, 120, 4, 140)
        b = m.barnard_two_sided(4, 140, 12, 120)
        self.assertAlmostEqual(a, b, places=6)
        self.assertTrue(0 < a < 1)
        self.assertAlmostEqual(m.barnard_two_sided(10, 100, 10, 100), 1.0, places=6)

    def test_more_powerful_than_fisher_two_sided(self):
        m = load()
        self.assertLessEqual(m.barnard_two_sided(38, 410, 24, 395), m.fisher_two_sided(38, 410, 24, 395) + 1e-9)

    def test_fast_enough_for_thousands(self):
        m = load()
        start = time.time()
        m.barnard_two_sided(150, 2500, 120, 2480)
        self.assertLess(time.time() - start, 20)


class HelpersTest(unittest.TestCase):
    def test_srm_holm_bh(self):
        m = load()
        self.assertLess(m.srm_p(160, 210), 0.01)
        self.assertGreater(m.srm_p(410, 395), 0.5)
        self.assertEqual(m.holm({'A': 0.001, 'B': 0.02, 'C': 0.04}, 0.05), {'A': True, 'B': True, 'C': True})
        self.assertEqual(set(m.holm({'A': 0.04, 'B': 0.06}, 0.05).values()), {False})
        self.assertEqual(m.bh({'A': 0.01, 'B': 0.04}, 0.05), {'A': True, 'B': True})
        self.assertEqual(m.bh({'A': 0.03, 'B': 0.2}, 0.05), {'A': False, 'B': False})

    def test_newcombe_interval(self):
        m = load()
        lo, hi = m.diff_ci(38, 410, 24, 395, conf=0.95)
        self.assertTrue(lo < 38 / 410 - 24 / 395 < hi)


class LockTest(unittest.TestCase):
    def test_rejects_placeholders_but_not_normal_words(self):
        m = load()
        with self.assertRaises(ValueError):
            m.lock(prereg([hyp(group='<痛点关键词组>')]))
        m.lock(prereg([hyp(group='名称里带普通字的组')]))
        h = hyp()
        h.pop('baseline_source')
        with self.assertRaises(ValueError):
            m.lock(prereg([h]))

    def test_rejects_unknown_split(self):
        m = load()
        with self.assertRaises(ValueError):
            m.lock(prereg(split_method='magic'))

    def test_window_must_be_one_to_three_days(self):
        m = load()
        for days in (0, 4, 14):
            with self.assertRaises(ValueError):
                m.lock(prereg(window_days=days))
        for days in (1, 2, 3):
            m.lock(prereg(window_days=days))

    def test_detects_tampering(self):
        m = load()
        locked = m.lock(prereg())
        with self.assertRaises(ValueError):
            m.analyze(dict(locked, alpha=0.10), data(locked, [result()]))
        with self.assertRaises(ValueError):
            m.analyze(dict(locked, locked_at=PAST), data(locked, [result()]))

    def test_requires_confirmed_audience(self):
        m = load()
        reg = prereg()
        reg.pop('audience')
        with self.assertRaises(ValueError):
            m.lock(reg)
        reg = prereg()
        reg['audience'] = dict(reg['audience'], confirmed_by='')
        with self.assertRaises(ValueError):
            m.lock(reg)
        reg = prereg()
        reg['audience'] = dict(reg['audience'], location_mode='presence_or_interest')
        with self.assertRaises(ValueError):
            m.lock(reg)

    def test_feasibility_budget_sharing_and_family(self):
        m = load()
        locked = m.lock(prereg([hyp(planned_n_per_arm=150)], budget=20))
        self.assertFalse(locked['feasibility']['H1']['feasible'])
        self.assertTrue(locked['feasibility']['H1']['underpowered_registration'])
        reg = prereg([hyp(), hyp(id='H2', role='exploratory', metric='conversion', unit='visitor',
                                 baseline_rate=0.2, planned_n_per_arm=20, traffic_from='H1')], budget=300)
        locked = m.lock(reg)
        self.assertEqual(locked['feasibility']['H2']['budget'], 300)
        two = m.lock(prereg([hyp(), hyp(id='H2', group='other')], budget=10000))
        self.assertEqual(two['feasibility']['H1']['n_per_arm'], m.n_per_arm(0.069, 2.0, alpha=0.025))

    def test_warns_when_daily_units_missing_or_window_too_short(self):
        m = load()
        h = hyp()
        h.pop('daily_units')
        locked = m.lock(prereg([h]))
        self.assertTrue(any('daily_units' in w for w in locked['warnings']))
        locked = m.lock(prereg([hyp(daily_units=50)]))
        self.assertFalse(locked['feasibility']['H1']['fits_window'])


class AnalyzeTest(unittest.TestCase):
    def verdict(self, reg, results, **over):
        m = load()
        locked = m.lock(reg)
        return m.analyze(locked, data(locked, results, **over))

    def test_test_better(self):
        rep = self.verdict(prereg(), [result()])
        self.assertEqual(rep['hypotheses'][0]['verdict'], 'TEST_BETTER')
        self.assertFalse(rep['exploratory'])

    def test_control_better(self):
        rep = self.verdict(prereg(), [result(test_x=18, control_x=45, daily=daily(t=(107, 6), c=(106, 15)))])
        self.assertEqual(rep['hypotheses'][0]['verdict'], 'CONTROL_BETTER')

    def test_neutral(self):
        rep = self.verdict(prereg(), [result(test_x=23, control_x=22)])
        self.assertEqual(rep['hypotheses'][0]['verdict'], 'NEUTRAL')

    def test_inconclusive_below_sample(self):
        rep = self.verdict(prereg(), [result(test_n=150, control_n=152, test_x=15, control_x=9)])
        self.assertEqual(rep['hypotheses'][0]['verdict'], 'INCONCLUSIVE')

    def test_final_before_endpoint_refused_unless_budget_exhausted(self):
        m = load()
        locked = m.lock(prereg())
        with self.assertRaises(ValueError):
            m.analyze(locked, data(locked, [result(days=2)], days_run=2))
        rep = m.analyze(locked, data(locked, [result(days=2)], days_run=2, budget_exhausted=True))
        self.assertEqual(rep['hypotheses'][0]['verdict'], 'TEST_BETTER')

    def test_hard_facts_invalid_srm_only_warns(self):
        for flag in ('budget_limited_one_arm', 'arm_limited', 'ai_max_detected', 'settings_changed'):
            rep = self.verdict(prereg(), [result(quality_flags=[flag])])
            self.assertEqual(rep['hypotheses'][0]['verdict'], 'INVALID')
        rep = self.verdict(prereg(), [result(test_n=330, control_n=400, test_x=46, control_x=22)])
        self.assertNotEqual(rep['hypotheses'][0]['verdict'], 'INVALID')
        self.assertTrue(rep['hypotheses'][0]['srm_alert'])

    def test_repeat_clickers_make_result_fragile(self):
        # 测试组 45 次点击里有 3 个访客各点了 6 次：ΣK²/ΣK = (27 + 108) / 45 = 3.0；对照组各不相同：1.0
        heavy = {'1': 27, '6': 3}
        rep = self.verdict(prereg(), [result(test_histogram=heavy)])
        h = rep['hypotheses'][0]
        self.assertEqual(h['de_mode'], 'measured')
        self.assertEqual(h['de_by_arm'], {'test': 3.0, 'control': 1.0})
        self.assertLess(h['p_naive'], 0.05)
        self.assertGreater(h['p_value'], h['p_naive'])
        self.assertIn(h['verdict'], ('FRAGILE', 'TEST_BETTER'))
        rep = self.verdict(prereg(), [result(test_x=34, control_x=18, test_histogram={'1': 22, '6': 2})])
        self.assertEqual(rep['hypotheses'][0]['verdict'], 'FRAGILE')

    def test_floor_applies_when_measured_is_lower(self):
        reg = prereg()
        reg['design_effect']['floor'] = 1.25
        rep = self.verdict(reg, [result()])
        self.assertAlmostEqual(rep['hypotheses'][0]['de_used'], 1.25)

    def test_low_match_rate_falls_back_to_band_upper_end(self):
        rep = self.verdict(prereg(), [result(gclid_match_rate={'test': 0.5, 'control': 0.9})])
        h = rep['hypotheses'][0]
        self.assertEqual(h['de_mode'], 'band')
        self.assertAlmostEqual(h['de_used'], 2.0)
        self.assertEqual(set(h['p_band']), {'1.0', '1.5', '2.0'})
        self.assertAlmostEqual(h['p_band']['1.0'], h['p_naive'], places=4)
        r = result()
        r.pop('test_histogram')
        self.assertEqual(self.verdict(prereg(), [r])['hypotheses'][0]['de_mode'], 'band')

    def test_visitors_in_both_arms_invalidates(self):
        rep = self.verdict(prereg(), [result(visitors_in_both_arms=5)])
        self.assertEqual(rep['hypotheses'][0]['verdict'], 'INVALID')
        r = result(visitors_in_both_arms=5)
        r.pop('test_histogram'), r.pop('control_histogram')
        with self.assertRaises(ValueError):
            self.verdict(prereg(), [r])
        r['visitors_total'] = 60
        self.assertEqual(self.verdict(prereg(), [r])['hypotheses'][0]['verdict'], 'INVALID')

    def test_band_never_milder_than_partial_measurement(self):
        rep = self.verdict(prereg(), [result(test_histogram={'1': 27, '6': 3}, gclid_match_rate={'test': 0.5, 'control': 0.5})])
        h = rep['hypotheses'][0]
        self.assertEqual(h['de_mode'], 'band')
        self.assertEqual(h['de_by_arm'], {'test': 3.0, 'control': 2.0})

    def test_neutral_needs_enough_effective_sample(self):
        rep = self.verdict(prereg(), [result(test_x=23, control_x=22, test_histogram={'1': 3, '5': 4})])
        self.assertEqual(rep['hypotheses'][0]['verdict'], 'INCONCLUSIVE')

    def test_required_budget_rounds_up(self):
        m = load()
        p = m.plan(baseline_rate=0.058, mde_lift_ratio=2.0, budget=100, cpc=1.0, daily_units=400, design_effect=1.25)
        again = m.plan(baseline_rate=0.058, mde_lift_ratio=2.0, budget=p['required_budget'], cpc=1.0, design_effect=1.25)
        self.assertTrue(again['feasible'])

    def test_visitor_unit_needs_no_design_effect(self):
        reg = prereg([hyp(), hyp(id='H2', group='landing', role='exploratory', metric='conversion', unit='visitor',
                                 baseline_rate=0.2, planned_n_per_arm=10, traffic_from='H1')])
        rep = self.verdict(reg, [result(), result(id='H2', test_n=40, test_x=12, control_n=40, control_x=3,
                                                  gclid_match_rate={})])
        h2 = [h for h in rep['hypotheses'] if h['id'] == 'H2'][0]
        self.assertEqual((h2['de_mode'], h2['de_used']), ('unit_matches', 1.0))

    def test_design_effect_registration_is_validated(self):
        m = load()
        for bad in ({'source': 'daily'}, {'source': 'first_party_click_histogram', 'floor': 0.9, 'band': [1, 2], 'min_match_rate': 0.7},
                    {'source': 'first_party_click_histogram', 'floor': 1.25, 'band': [2, 1], 'min_match_rate': 0.7},
                    {'source': 'first_party_click_histogram', 'floor': 1.25, 'band': [1, 2], 'min_match_rate': '高'}):
            with self.assertRaises(ValueError):
                m.lock(prereg(design_effect=bad))

    def test_plan_inflates_sample_by_design_effect(self):
        m = load()
        base = m.plan(baseline_rate=0.069, mde_lift_ratio=2.0, budget=500, cpc=1.0)
        de = m.plan(baseline_rate=0.069, mde_lift_ratio=2.0, budget=500, cpc=1.0, design_effect=1.25)
        self.assertEqual(de['n_per_arm'], -(-base['n_per_arm'] * 125 // 100))
        reg = prereg([hyp(planned_n_per_arm=base['n_per_arm'])])
        reg['design_effect']['floor'] = 1.25
        self.assertTrue(m.lock(reg)['feasibility']['H1']['underpowered_registration'])
        self.assertEqual(m.design_effect_from_histograms([{'1': 15, '5': 1}]), 2.0)

    def test_missing_daily_is_only_descriptive(self):
        r = result()
        r.pop('daily')
        rep = self.verdict(prereg(), [r])
        self.assertFalse(rep['exploratory'])
        self.assertNotIn('daily_check', rep['hypotheses'][0])

    def test_exploratory_triggers(self):
        self.assertTrue(self.verdict(prereg(split_method='same_adgroup_even_rotation'), [result()])['exploratory'])
        self.assertTrue(self.verdict(prereg(), [result()], launch_at=PAST)['exploratory'])
        self.assertTrue(self.verdict(prereg(budget=20), [result()])['exploratory'])
        self.assertTrue(self.verdict(prereg(), [result()], unregistered_views=1, stopped_early=True)['exploratory'])
        rep = self.verdict(prereg(), [result()], unregistered_views=1, stopped_early=False)
        self.assertFalse(rep['exploratory'])

    def test_exploratory_hypotheses_use_bh_outside_family(self):
        reg = prereg([hyp(), hyp(id='H2', group='landing', role='exploratory', metric='conversion', unit='visitor',
                                 baseline_rate=0.2, planned_n_per_arm=10, traffic_from='H1')])
        rep = self.verdict(reg, [result(), result(id='H2', test_n=40, test_x=12, control_n=12, control_x=1)])
        self.assertEqual(rep['family_size'], 1)
        h2 = [h for h in rep['hypotheses'] if h['id'] == 'H2'][0]
        self.assertEqual(h2['correction'], 'bh')

    def test_interim_uses_haybittle_peto(self):
        reg = prereg(interim_looks=1, interim_after_days=1)
        rep = self.verdict(reg, [result(days=1, test_x=30, control_x=18)], look='interim', days_run=1)
        self.assertAlmostEqual(rep['alpha_this_look'], 0.001)
        self.assertEqual(rep['hypotheses'][0]['verdict'], 'CONTINUE')
        self.assertIn('继续投到终点', load().render(rep))
        rep = self.verdict(reg, [result()])
        self.assertAlmostEqual(rep['alpha_this_look'], 0.05)

    def test_refusals(self):
        m = load()
        locked = m.lock(prereg())
        with self.assertRaises(ValueError):
            m.analyze(locked, data(locked, [result(), result(id='POOLED')]))
        with self.assertRaises(ValueError):
            m.analyze(locked, data(locked, [result()], look='interim'))

    def test_render_wording_and_learning_log(self):
        m = load()
        locked = m.lock(prereg())
        rep = m.analyze(locked, data(locked, [result()], unregistered_views=1, retrieved_at='2026-10-09T10:00:00+08:00'))
        text = m.render(rep)
        self.assertIn('测试诉求显著更好', text)
        self.assertIn('desktop', text)
        self.assertIn('结论只对', text)
        self.assertIn('2026-10-09', text)
        self.assertIn('中途查看效果数据', text)
        self.assertNotIn('偷看', text)
        self.assertIn('不说明用户愿意付费', text)
        self.assertIn('3/3 天', text)
        self.assertIn('校正后 p', text)
        entry = m.learning_log_entry(locked, rep, '原创性诉求点击率更高')
        h = entry['hypotheses'][0]
        self.assertEqual(h['result']['verdict'], 'TEST_BETTER')
        self.assertEqual(h['result']['test_x'], 45)
        self.assertIn('p_value', h['result'])
        self.assertEqual(entry['lesson'], '原创性诉求点击率更高')
        self.assertEqual(entry['next_step']['H1']['step'], 'act')

    def test_log_requires_lesson_and_next_step(self):
        m = load()
        import contextlib, io
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            m.main(['analyze', 'a.json', 'b.json', '--log', 'x.jsonl'])  # 缺 --lesson

    def test_rejects_inconsistent_daily_and_unknown_flags(self):
        m = load()
        locked = m.lock(prereg())
        r = result()
        r['test_x'] += 1
        with self.assertRaises(ValueError):
            m.analyze(locked, data(locked, [r]))
        with self.assertRaises(ValueError):
            m.analyze(locked, data(locked, [result(days=2)]))
        with self.assertRaises(ValueError):
            m.analyze(locked, data(locked, [result(quality_flags=['ad_limited'])]))


    def test_unequal_arms_with_full_total_are_not_underpowered(self):
        rep = self.verdict(prereg(), [result(test_n=305, control_n=315)])
        self.assertNotEqual(rep['hypotheses'][0]['verdict'], 'INCONCLUSIVE')

    def test_decisions_and_interim_day_are_locked(self):
        m = load()
        reg = prereg()
        reg.pop('decisions')
        with self.assertRaises(ValueError):
            m.lock(reg)
        reg = prereg()
        reg['decisions']['FRAGILE'] = {'action': 'x', 'step': 'win'}
        with self.assertRaises(ValueError):
            m.lock(reg)
        with self.assertRaises(ValueError):
            m.lock(prereg(interim_looks=1))
        with self.assertRaises(ValueError):
            m.lock(prereg(interim_looks=1, interim_after_days=3))

    def test_interim_only_on_registered_day_and_not_logged(self):
        import contextlib, io, json, tempfile, os
        m = load()
        locked = m.lock(prereg(interim_looks=1, interim_after_days=1))
        with self.assertRaises(ValueError):
            m.analyze(locked, data(locked, [result(days=2)], look='interim', days_run=2))
        with tempfile.TemporaryDirectory() as d:
            lp, rp, log = (os.path.join(d, x) for x in ('l.json', 'r.json', 'log.jsonl'))
            json.dump(locked, open(lp, 'w'))
            json.dump(data(locked, [result(days=1, test_x=30, control_x=18)], look='interim', days_run=1), open(rp, 'w'))
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(['analyze', lp, rp, '--log', log, '--lesson', 'x']), 2)
            self.assertFalse(os.path.exists(log))

    def test_notes_for_missing_commit_and_early_retrieval(self):
        m = load()
        locked = m.lock(prereg())
        rep = m.analyze(locked, data(locked, [result()], launch_at='2026-10-06T00:00:00+00:00',
                                     retrieved_at='2026-10-10T00:00:00+00:00'))
        self.assertTrue(any('不足 3 天' in n for n in rep['notes']))
        self.assertTrue(any('git_commit' in n for n in rep['notes']))
        rep = m.analyze(locked, data(locked, [result()], launch_at='2026-10-06T00:00:00+00:00',
                                     retrieved_at='2026-10-13T00:00:00+00:00', git_commit='abc123'))
        self.assertEqual(rep['notes'], [])
        r = result()
        r['daily'][1]['date'] = r['daily'][0]['date']
        with self.assertRaises(ValueError):
            m.analyze(locked, data(locked, [r]))

    def test_no_false_daily_units_warning_and_failed_lock_writes_nothing(self):
        import contextlib, io, json, tempfile, os
        m = load()
        self.assertEqual(m.lock(prereg())['warnings'], [])
        with tempfile.TemporaryDirectory() as d:
            src, out = os.path.join(d, 'p.json'), os.path.join(d, 'p.locked.json')
            reg = prereg()
            reg.pop('guardrails')
            json.dump(reg, open(src, 'w'))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(m.main(['lock', src, '--out', out]), 2)
            self.assertFalse(os.path.exists(out))


class GuardrailAndAlphaTest(unittest.TestCase):
    def test_guardrails_must_be_confirmed_numbers(self):
        m = load()
        for g in (None, {'search_terms_overlap_min': 0.6}, {'search_terms_overlap_min': 'high', 'invalid_click_rate_max': 0.15},
                  {'search_terms_overlap_min': 0.6, 'invalid_click_rate_max': None}):
            reg = prereg()
            if g is None:
                reg.pop('guardrails')
            else:
                reg['guardrails'] = g
            with self.assertRaises(ValueError):
                m.lock(reg)

    def test_consent_guardrail_required_for_uk_eu(self):
        m = load()
        reg = prereg()
        reg['audience'] = dict(reg['audience'], locations=['US', 'GB'])
        with self.assertRaises(ValueError):
            m.lock(reg)
        reg['guardrails'] = dict(reg['guardrails'], consent_rate_diff_max=0.05)
        m.lock(reg)

    def test_plan_splits_alpha_like_lock(self):
        m = load()
        p = m.plan(baseline_rate=0.058, mde_lift_ratio=1.8, budget=1500, cpc=1.0, daily_units=400, confirmatory_count=3)
        locked = m.lock(prereg([hyp(id=f'H{i}', group=f'g{i}', baseline_rate=0.058, mde_lift_ratio=1.8,
                                    planned_n_per_arm=p['n_per_arm'], daily_units=400) for i in (1, 2, 3)], budget=4500))
        self.assertEqual(locked['feasibility']['H1']['n_per_arm'], p['n_per_arm'])
        self.assertFalse(locked['feasibility']['H1']['underpowered_registration'])
        self.assertEqual(p['n_per_arm'], m.n_per_arm(0.058, 1.8, alpha=0.05 / 3))

    def test_achievable_lift_respects_window(self):
        m = load()
        p = m.plan(baseline_rate=0.058, mde_lift_ratio=1.5, budget=350, cpc=4.0, daily_units=335)
        n = m.n_per_arm(0.058, p['achievable_mde_lift_ratio'])
        self.assertLessEqual(2 * n, 335 * 3)
        budget_only = m.plan(baseline_rate=0.058, mde_lift_ratio=1.5, budget=350, cpc=4.0)
        self.assertGreater(p['achievable_mde_lift_ratio'], budget_only['achievable_mde_lift_ratio'])
        self.assertEqual(p['min_daily_units_for_window'], -(-2 * p['n_per_arm'] // 3))


if __name__ == '__main__':
    unittest.main()
