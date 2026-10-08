#!/usr/bin/env python3
"""广告诉求对照实验的统计工具：可行性规划、按样本规模选定检验方法、事先登记锁定、假设检验、学习记录。

只用标准库。周期 1–3 天；双侧检验；方法按计划样本量在登记时选定（Barnard 精确检验或两比例 z 检验）；
确认性假设 Holm 校正，探索性假设 Benjamini–Hochberg 校正；可事先登记一次中期检查（Haybittle–Peto）。
随机单位是用户、检验单位是展示时，按第一方访客点击分布算设计效应（ΣK²/ΣK），按 Kish 有效样本量检验；
gclid 关联率不够时改用登记的设计效应区间，确认性结论须在区间上端仍成立。

用法：
  python3 ab_stats.py plan --baseline 0.069 --lift 2.0 --budget 50 --cpc 1.0 --daily-units 250 [--confirmatory-count 2] [--metric conversion]
  python3 ab_stats.py lock prereg.json --out prereg.locked.json
  python3 ab_stats.py analyze prereg.locked.json results.json [--json] [--log learning-log.jsonl --lesson '…']
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from math import ceil, comb, erfc, exp, log, sqrt
import re
from statistics import NormalDist
import sys

N = NormalDist()
MAX_WINDOW_DAYS = 3
INTERIM_ALPHA = 0.001  # Haybittle–Peto：中期检查只在极强证据下允许提前停
SKEWNESS_FACTOR = 355  # Kohavi 偏度规则：n > 355·s² 时正态近似可靠
SPLITS = {
    'google_ads_experiment_cookie': '谷歌广告实验，按 cookie 50:50 分流',
    'google_ads_ad_variation': '谷歌广告变体，按 cookie 50:50 分流',
    'same_adgroup_even_rotation': '同广告组平均轮播（排名会混入，只能探索）',
}
METHODS = {'barnard': 'Barnard 精确检验（双侧）', 'z_pooled': '两比例 z 检验（双侧，合并方差）'}
HARD_FLAGS = {
    'arm_disapproved': '有一组广告被拒登或停投',
    'arm_limited': '有一组广告受限（含 Eligible (limited) 或限量投放通知）',
    'budget_limited_one_arm': '有一组受预算限制，投放不足',
    'search_terms_mismatch': '两组触发的搜索词分布超过登记阈值',
    'invalid_click_anomaly': '无效点击率超过登记阈值',
    'tracking_broken': '追踪中断或数据缺失',
    'ai_max_detected': '投放中出现 AI Max 匹配类型，不变项被破坏',
    'settings_changed': '投放中出现未登记的设置变更',
}
PLACEHOLDER = re.compile(r'<[^<>]*>|YYYY|TODO')
DE_MODE = {'measured': '实测', 'band': '区间上端', 'unit_matches': '单位一致'}
ZH = {
    'TEST_BETTER': '测试诉求显著更好', 'CONTROL_BETTER': '通用话术显著更好',
    'FRAGILE': '统计上脆弱：不校正时显著、校正同一用户重复后不显著，待确认实验',
    'NEUTRAL': '功效足够的中性结果：未检出差异', 'INCONCLUSIVE': '功效不足：不下结论',
    'INVALID': '实验无效：不下结论', 'SCREEN_PASS': '探索性筛选通过，待确认实验',
    'SCREEN_CONTROL_BETTER': '探索性筛选：通用话术更好，待确认实验',
    'CONTINUE': f'中期未达 p < {INTERIM_ALPHA} 的提前停止门槛：继续投到终点',
}


# ---------- 样本量、方法选择与规划 ----------

def n_per_arm(p0, lift, alpha=0.05, power=0.8, sides='two'):
    """每组所需单位数。lift 为比例倍数：2.0 表示从 p0 提升到 2·p0。"""
    p1 = p0 * lift
    if not 0 < p0 < p1 < 1:
        raise ValueError('需满足 0 < 基线 < 基线×倍数 < 1')
    za = N.inv_cdf(1 - alpha / 2) if sides == 'two' else N.inv_cdf(1 - alpha)
    zb, pb = N.inv_cdf(power), (p0 + p1) / 2
    num = za * sqrt(2 * pb * (1 - pb)) + zb * sqrt(p0 * (1 - p0) + p1 * (1 - p1))
    return ceil(num ** 2 / (p1 - p0) ** 2 - 1e-9)


def skewness_threshold(p):
    """伯努利指标正态近似可靠所需的每组样本量（Kohavi 偏度规则 n > 355·s²）。"""
    return ceil(SKEWNESS_FACTOR * (1 - 2 * p) ** 2 / (p * (1 - p)))


def choose_method(planned_n, p0, lift):
    """按计划样本量选检验方法，登记时锁定，分析时不得更换。"""
    p_low = min(p0, p0 * lift)
    expected_min = planned_n * min(p0, 1 - p0 * lift)
    threshold = skewness_threshold(p_low)
    if expected_min < 5:
        return {'method': 'barnard', 'reason': f'最小期望格子数约 {expected_min:.1f} < 5，属小样本'}
    if planned_n < threshold:
        return {'method': 'barnard', 'reason': f'每组 {planned_n} 低于正态近似门槛 {threshold}'}
    return {'method': 'z_pooled', 'reason': f'每组 {planned_n} 达到正态近似门槛 {threshold}'}


def _unit_cost(metric, p0, lift, cpc):
    # 点击率实验的单位是展示：一次展示的期望花费 = 平均点击率 × 单次点击价；转化实验的单位是访客 = 一次点击
    return cpc * p0 * (1 + lift) / 2 if metric == 'ctr' else cpc


def plan(baseline_rate, mde_lift_ratio, budget, cpc, alpha=0.05, power=0.8, sides='two',
         metric='ctr', daily_units=None, confirmatory_count=1, window_days=MAX_WINDOW_DAYS, design_effect=1.0):
    """confirmatory_count：同一实验里确认性假设的条数，规划时 α 按条数分摊（与 lock 一致）。"""
    a = alpha / max(confirmatory_count, 1)
    if design_effect < 1:
        raise ValueError('设计效应不能小于 1')

    def required(lift):
        n = ceil(n_per_arm(baseline_rate, lift, a, power, sides) * design_effect - 1e-9)
        return n, 2 * n * _unit_cost(metric, baseline_rate, lift, cpc)

    def fits(lift):
        n, cost = required(lift)
        return cost <= budget and (not daily_units or 2 * n <= daily_units * window_days)

    n, need = required(mde_lift_ratio)
    affordable = int(budget / (2 * _unit_cost(metric, baseline_rate, mde_lift_ratio, cpc)))
    lo, hi = 1.0001, (1 - 1e-6) / baseline_rate
    achievable = None
    if fits(hi):
        for _ in range(60):
            mid = (lo + hi) / 2
            if fits(mid):
                hi = mid
            else:
                lo = mid
        achievable = round(hi, 3)
    out = {'metric': metric, 'sides': sides, 'alpha': alpha, 'confirmatory_count': confirmatory_count,
           'alpha_per_hypothesis': round(a, 6), 'power': power, 'design_effect': design_effect,
           'n_per_arm': n, 'total_units': 2 * n, 'required_budget': ceil(need * 100 - 1e-9) / 100,
           'budget': budget, 'n_per_arm_affordable': affordable,
           'achievable_mde_lift_ratio': achievable,
           'achievable_limited_by': '预算和周期' if daily_units else '预算（没填每日量，未考虑周期）',
           'feasible': need <= budget}
    out.update(choose_method(ceil(n / design_effect), baseline_rate, mde_lift_ratio))
    if daily_units:
        out['days_needed'] = ceil(2 * n / daily_units)
        out['window_days'] = window_days
        out['fits_window'] = out['days_needed'] <= window_days
        out['min_daily_units_for_window'] = ceil(2 * n / window_days)
    return out


# ---------- 检验 ----------

def _pmf(n, p):
    """二项分布概率表（对数递推）。"""
    if p <= 0:
        return [1.0] + [0.0] * n
    if p >= 1:
        return [0.0] * n + [1.0]
    lp, lq = log(p), log(1 - p)
    cur = n * lq
    out = [exp(cur)]
    for k in range(1, n + 1):
        cur += log(n - k + 1) - log(k) + lp - lq
        out.append(exp(cur))
    return out


def _pooled_z(x1, n1, x2, n2):
    p = (x1 + x2) / (n1 + n2)
    if p in (0, 1):
        return 0.0
    return (x1 / n1 - x2 / n2) / sqrt(p * (1 - p) * (1 / n1 + 1 / n2))


def z_pooled_two_sided(x1, n1, x2, n2):
    return min(1.0, 2 * (1 - N.cdf(abs(_pooled_z(x1, n1, x2, n2)))))


def barnard_two_sided(x1, n1, x2, n2, grid=200):
    """Barnard 精确检验（合并方差 z 统计量，双侧），对干扰参数取上确界。"""
    zobs = abs(_pooled_z(x1, n1, x2, n2))
    if zobs == 0:
        return 1.0
    tol = 1e-9
    low, high = [], []
    for a in range(n1 + 1):
        # 固定 a 时 z 随 b 单调递减：二分找 z ≥ zobs 的最大 b，以及 z ≤ −zobs 的最小 b
        lo_b, hi_b, edge = 0, n2, -1
        while lo_b <= hi_b:
            mid = (lo_b + hi_b) // 2
            if _pooled_z(a, n1, mid, n2) >= zobs - tol:
                edge, lo_b = mid, mid + 1
            else:
                hi_b = mid - 1
        lo_b, hi_b, edge2 = 0, n2, n2 + 1
        while lo_b <= hi_b:
            mid = (lo_b + hi_b) // 2
            if _pooled_z(a, n1, mid, n2) <= -zobs + tol:
                edge2, hi_b = mid, mid - 1
            else:
                lo_b = mid + 1
        low.append(edge)
        high.append(max(edge2, edge + 1))

    def prob(pi):
        pmf1, pmf2 = _pmf(n1, pi), _pmf(n2, pi)
        cdf = [0.0] * (n2 + 2)
        for b in range(n2 + 1):
            cdf[b + 1] = cdf[b] + pmf2[b]
        total = 0.0
        for a in range(n1 + 1):
            w = pmf1[a]
            if w < 1e-300:
                continue
            left = cdf[low[a] + 1] if low[a] >= 0 else 0.0
            right = cdf[n2 + 1] - cdf[high[a]] if high[a] <= n2 else 0.0
            total += w * (left + right)
        return total

    pts = [(i + 0.5) / grid for i in range(grid)]
    vals = [prob(p) for p in pts]
    best = max(range(grid), key=vals.__getitem__)
    a, b = max(1e-6, pts[best] - 1 / grid), min(1 - 1e-6, pts[best] + 1 / grid)
    g = (sqrt(5) - 1) / 2
    for _ in range(40):
        c, d = b - g * (b - a), a + g * (b - a)
        if prob(c) > prob(d):
            b = d
        else:
            a = c
    return min(1.0, max(vals[best], prob((a + b) / 2)))


def fisher_two_sided(x1, n1, x2, n2):
    k, total = x1 + x2, n1 + n2
    denom = comb(total, k)
    probs = {x: comb(n1, x) * comb(n2, k - x) / denom for x in range(max(0, k - n2), min(k, n1) + 1)}
    obs = probs[x1]
    return min(1.0, sum(p for p in probs.values() if p <= obs * (1 + 1e-7)))


def run_test(method, x1, n1, x2, n2):
    return barnard_two_sided(x1, n1, x2, n2) if method == 'barnard' else z_pooled_two_sided(x1, n1, x2, n2)


def srm_p(n_test, n_control):
    e = (n_test + n_control) / 2
    chi = ((n_test - e) ** 2 + (n_control - e) ** 2) / e
    return erfc(sqrt(chi / 2))


def wilson(x, n, conf):
    z = N.inv_cdf(1 - (1 - conf) / 2)
    p = x / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return c - h, c + h


def diff_ci(x1, n1, x2, n2, conf=0.95):
    """Newcombe 混合得分区间：测试比例减对照比例。"""
    p1, p2 = x1 / n1, x2 / n2
    l1, u1 = wilson(x1, n1, conf)
    l2, u2 = wilson(x2, n2, conf)
    d = p1 - p2
    return d - sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)


def holm(pvalues, alpha):
    order = sorted(pvalues, key=pvalues.get)
    m, out, stopped = len(order), {}, False
    for i, key in enumerate(order):
        ok = not stopped and pvalues[key] <= alpha / (m - i)
        stopped = stopped or not ok
        out[key] = ok
    return out


def bh(pvalues, alpha):
    """Benjamini–Hochberg：控制错误发现率，用于探索性筛选。"""
    order = sorted(pvalues, key=pvalues.get)
    m, cutoff = len(order), 0
    for i, key in enumerate(order, 1):
        if pvalues[key] <= alpha * i / m:
            cutoff = i
    return {key: i <= cutoff for i, key in enumerate(order, 1)}


def daily_direction(days, direction):
    """按天稳健性：与总体方向一致的天数需过半；附双侧符号检验 p 值。"""
    signs = []
    for d in days:
        diff = d['test_x'] / d['test_n'] - d['control_x'] / d['control_n'] if d['test_n'] and d['control_n'] else 0
        if diff:
            signs.append(1 if diff > 0 else -1)
    k, n = sum(1 for x in signs if x == direction), len(signs)
    tail = sum(comb(n, i) for i in range(max(k, n - k), n + 1)) / 2 ** n if n else 1.0
    return {'days': len(days), 'nonzero': n, 'agree': k, 'sign_test_p': round(min(1.0, 2 * tail), 4),
            'consistent': n > 0 and k * 2 > n}


def design_effect_from_histograms(hists):
    """合并两组的访客点击直方图 {点击次数: 访客数}，返回 ΣK²/ΣK（复合计数相对独立点击的方差膨胀倍数）。"""
    s1 = s2 = 0
    for h in hists:
        for k, v in h.items():
            k, v = int(k), int(v)
            if k < 1 or v < 0:
                raise ValueError('访客点击直方图的键须为 ≥ 1 的点击次数，值须为非负访客数')
            s1 += k * v
            s2 += k * k * v
    return s2 / s1 if s1 else None


def deflate(x, n, de):
    """Kish 有效样本量：计数都除以设计效应后取整。"""
    ne = max(1, round(n / de))
    return min(round(x / de), ne), ne


# ---------- 登记 ----------

def _canonical(reg):
    return json.dumps({k: v for k, v in reg.items() if k != 'prereg_hash'},
                      sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _has_placeholder(value):
    if isinstance(value, str):
        return bool(PLACEHOLDER.search(value))
    if isinstance(value, dict):
        return any(_has_placeholder(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_placeholder(v) for v in value)
    return False


def plan_count(reg, h):
    """规划用的确认性假设条数：确认性假设按条数做 Bonferroni 保守分摊，探索性假设不分摊。"""
    if h['role'] != 'confirmatory':
        return 1
    return max(sum(1 for x in reg['hypotheses'] if x['role'] == 'confirmatory'), 1)


CONSENT_REGIONS = {'GB', 'UK', 'CH', 'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU',
                   'IE', 'IT', 'LV', 'LT', 'LU', 'MT', 'NL', 'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE', 'IS', 'LI', 'NO'}


DECISION_VERDICTS = ('TEST_BETTER', 'CONTROL_BETTER', 'FRAGILE', 'NEUTRAL', 'INCONCLUSIVE', 'INVALID',
                     'SCREEN_PASS', 'SCREEN_CONTROL_BETTER')
NEXT_STEPS = ('act', 'confirm', 'redo')


def check_decisions(reg):
    """决策映射（ad-campaign-build 第 5 步）：每种结论事先写好行动和下一步类型，随登记一起锁定。"""
    d = reg.get('decisions')
    if not isinstance(d, dict):
        raise ValueError('登记缺少 decisions：每种结论的行动必须在开投前写好（ad-campaign-build 第 5 步）')
    for v in DECISION_VERDICTS:
        x = d.get(v)
        if not isinstance(x, dict) or x.get('step') not in NEXT_STEPS or not str(x.get('action', '')).strip():
            raise ValueError(f'decisions.{v} 要写 action（做什么）和 step（{"/".join(NEXT_STEPS)}）')


def check_design_effect(reg):
    d = reg['design_effect']
    if not isinstance(d, dict) or d.get('source') != 'first_party_click_histogram':
        raise ValueError('design_effect.source 只支持 first_party_click_histogram（落地页 gclid 关联第一方访客）')
    floor, band, rate = d.get('floor'), d.get('band'), d.get('min_match_rate')
    if isinstance(floor, bool) or not isinstance(floor, (int, float)) or floor < 1:
        raise ValueError('design_effect.floor 必须是 ≥ 1 的数值（规划用设计效应，没有历史数据时用 1.25）')
    if (not isinstance(band, list) or len(band) < 2 or band != sorted(band)
            or any(isinstance(b, bool) or not isinstance(b, (int, float)) or b < 1 for b in band)):
        raise ValueError('design_effect.band 必须是升序、≥ 1 的数值列表，如 [1.0, 1.5, 2.0]')
    if band[-1] < floor:
        raise ValueError('design_effect.band 的上端不能低于 floor')
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < rate <= 1:
        raise ValueError('design_effect.min_match_rate 必须是用户确认的 0–1 数值（gclid 关联率门槛）')


def de_for(reg, h):
    """展示为检验单位时才有用户内相关；访客为单位的转化指标与随机单位一致，设计效应为 1。"""
    return reg['design_effect']['floor'] if h['unit'] == 'impression' else 1.0


def check_guardrails(reg):
    g = reg.get('guardrails')
    if not isinstance(g, dict):
        raise ValueError('登记缺少 guardrails：护栏阈值必须由用户确认后填写')
    need = ['search_terms_overlap_min', 'invalid_click_rate_max', 'both_arms_visitor_share_max']
    if CONSENT_REGIONS & {c.upper() for c in reg['audience']['locations']}:
        need.append('consent_rate_diff_max')
    for k in need:
        v = g.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
            raise ValueError(f'guardrails.{k} 必须是用户确认的 0–1 之间的数值（两组都出现过的访客占比上限必填；投欧洲经济区、英国、瑞士时同意率差异也必填）')


def lock(reg):
    required = ('experiment_id', 'alpha', 'power', 'sides', 'multiple_comparison', 'srm_alert_p', 'split_method',
                'interim_looks', 'budget', 'cpc', 'window_days', 'design_effect', 'audience', 'hypotheses')
    missing = [k for k in required if k not in reg]
    if missing:
        raise ValueError(f'登记缺少字段：{missing}')
    if reg['sides'] != 'two' or reg['multiple_comparison'] != 'holm':
        raise ValueError('只支持 sides=two、multiple_comparison=holm（探索性假设自动用 Benjamini–Hochberg）')
    if reg['split_method'] not in SPLITS:
        raise ValueError(f'分流方式只能是：{list(SPLITS)}')
    if reg['interim_looks'] not in (0, 1):
        raise ValueError('中期检查只允许 0 或 1 次')
    if reg['interim_looks'] == 1 and not (isinstance(reg.get('interim_after_days'), int)
                                          and 1 <= reg['interim_after_days'] < reg['window_days']):
        raise ValueError('登记了中期检查就必须写 interim_after_days：第几个完整日结束后检查，须小于 window_days')
    check_decisions(reg)
    if not 1 <= reg['window_days'] <= MAX_WINDOW_DAYS:
        raise ValueError(f'投放周期必须是 1–{MAX_WINDOW_DAYS} 个完整日')
    check_design_effect(reg)
    aud = reg['audience']
    for k in ('devices', 'locations', 'location_mode', 'schedule', 'exclusions', 'evidence', 'confirmed_by', 'confirmed_at'):
        if k not in aud or (k != 'exclusions' and aud[k] in ('', None, [])):
            raise ValueError(f'audience 缺少 {k}：人群与设备选择必须有依据并经用户确认')
    if aud['location_mode'] != 'presence':
        raise ValueError('地区模式必须是 presence（只投所在地）')
    if not set(aud['devices']) <= {'desktop', 'mobile', 'tablet'}:
        raise ValueError('devices 只能是 desktop、mobile、tablet')
    ids = set()
    for h in reg['hypotheses']:
        for k in ('id', 'group', 'role', 'metric', 'unit', 'baseline_rate', 'baseline_source',
                  'mde_lift_ratio', 'planned_n_per_arm'):
            if k not in h or h[k] in ('', None):
                raise ValueError(f'假设 {h.get("id")} 缺少字段 {k}')
        if h['role'] not in ('confirmatory', 'exploratory'):
            raise ValueError('role 只能是 confirmatory 或 exploratory')
        if h['id'] in ids:
            raise ValueError(f'假设编号重复：{h["id"]}')
        ids.add(h['id'])
    check_guardrails(reg)
    if _has_placeholder(reg):
        raise ValueError('登记里还有未替换的占位文字（<…>、YYYY、TODO）')
    by_id = {h['id']: h for h in reg['hypotheses']}
    for h in reg['hypotheses']:
        if h.get('traffic_from') and h['traffic_from'] not in by_id:
            raise ValueError(f'{h["id"]} 的 traffic_from 指向不存在的假设')
    independent = [h for h in reg['hypotheses'] if not h.get('traffic_from') and 'budget' not in h]
    share = reg['budget'] / max(len(independent), 1)

    def budget_of(h):
        if 'budget' in h:
            return h['budget']
        if h.get('traffic_from'):
            return budget_of(by_id[h['traffic_from']])  # 同一批流量上的下游指标，共用上游预算
        return share

    out = dict(reg)
    feas, methods, warnings = {}, {}, []
    for h in reg['hypotheses']:
        f = plan(h['baseline_rate'], h['mde_lift_ratio'], budget_of(h), reg['cpc'], reg['alpha'], reg['power'],
                 'two', 'ctr' if h['metric'] == 'ctr' else 'conversion', h.get('daily_units'),
                 plan_count(reg, h), reg['window_days'], de_for(reg, h))
        f['budget'] = budget_of(h)
        f['underpowered_registration'] = h['planned_n_per_arm'] < f['n_per_arm']
        if 'days_needed' in f:
            if not f['fits_window']:
                warnings.append(f"{h['id']} 按每日量需要 {f['days_needed']} 天，超过登记周期 {reg['window_days']} 天"
                                f"（3 天内投完每天至少要 {f['min_daily_units_for_window']}）")
        elif not h.get('traffic_from'):
            warnings.append(f"{h['id']} 没填 daily_units，无法检查周期内能否投够样本")
        if f['underpowered_registration']:
            warnings.append(f"{h['id']} 登记样本 {h['planned_n_per_arm']} 低于功效要求 {f['n_per_arm']}"
                            f"（确认性假设共 {plan_count(reg, h)} 条，α 已分摊；plan 要加 --confirmatory-count）")
        chosen = choose_method(ceil(h['planned_n_per_arm'] / de_for(reg, h)), h['baseline_rate'], h['mde_lift_ratio'])
        methods[h['id']] = chosen['method']
        f['method_reason'] = chosen['reason']
        feas[h['id']] = f
    out.update(feasibility=feas, methods=methods, warnings=warnings,
               locked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    out['prereg_hash'] = hashlib.sha256(_canonical(out).encode()).hexdigest()
    return out


# ---------- 分析 ----------

def analyze(locked, data):
    digest = hashlib.sha256(_canonical(locked).encode()).hexdigest()
    if locked.get('prereg_hash') != digest:
        raise ValueError('登记在锁定后被修改，拒绝分析')
    if data.get('prereg_hash') != digest or data.get('experiment_id') != locked['experiment_id']:
        raise ValueError('结果数据与登记不对应')
    look = data.get('look', 'final')
    if look not in ('final', 'interim') or (look == 'interim' and locked['interim_looks'] != 1):
        raise ValueError('这次分析没有事先登记（只允许登记过的一次中期检查和终点）')
    registered = {h['id']: h for h in locked['hypotheses']}
    unknown = [r['id'] for r in data['results'] if r['id'] not in registered]
    if unknown:
        raise ValueError(f'结果里有未登记的假设：{unknown}（事后新增或合并的检验不允许）')
    days_run = data.get('days_run')
    if days_run is None or not data.get('launch_at'):
        raise ValueError('结果数据缺少 days_run（实际投放天数）或 launch_at（开投时间）')
    if look == 'final' and days_run < locked['window_days'] and not data.get('budget_exhausted'):
        raise ValueError('还没到登记的终点（结束日期或预算花完），不能做终点分析')
    if look == 'interim' and days_run != locked['interim_after_days']:
        raise ValueError(f"中期检查只能在登记的第 {locked['interim_after_days']} 天结束后做一次，这份数据是第 {days_run} 天")
    for r in data['results']:
        bad = [f for f in r.get('quality_flags', []) if f not in HARD_FLAGS]
        if bad:
            raise ValueError(f'{r["id"]} 有不认识的质量标记：{bad}，只能用 {list(HARD_FLAGS)}')
        daily = r.get('daily') or []
        if len({d.get('date') for d in daily}) != len(daily):
            raise ValueError(f'{r["id"]} 按天数据有重复日期')
        if daily:
            if len(daily) != days_run:
                raise ValueError(f'{r["id"]} 按天数据 {len(daily)} 行，与 days_run={days_run} 不一致')
            for k in ('test_n', 'test_x', 'control_n', 'control_x'):
                if sum(d[k] for d in daily) != r[k]:
                    raise ValueError(f'{r["id"]} 按天数据的 {k} 合计与总数不一致')
    alpha = locked['alpha']
    a_look = INTERIM_ALPHA if look == 'interim' else alpha

    reasons = []
    if locked['split_method'] == 'same_adgroup_even_rotation':
        reasons.append('分流方式是同广告组平均轮播，排名差异会混入结果')
    if datetime.fromisoformat(locked['locked_at']) > datetime.fromisoformat(data['launch_at']):
        reasons.append('登记在开投之后才锁定')
    views = int(data.get('unregistered_views', 0))
    if data.get('stopped_early'):
        reasons.append('在中途查看效果数据之后提前停止')
    by_id = {r['id']: r for r in data['results']}
    for hid, h in registered.items():
        if h['role'] != 'confirmatory':
            continue
        f = locked['feasibility'][hid]
        if not f['feasible']:
            reasons.append(f'{hid} 锁定时预算不足以买到所需样本')
        if f['underpowered_registration']:
            reasons.append(f'{hid} 登记的样本量 {h["planned_n_per_arm"]} 低于功效要求 {f["n_per_arm"]}')
    exploratory = bool(reasons)
    family = [hid for hid, h in registered.items() if h['role'] == 'confirmatory' and not exploratory]

    rows, p_conf, p_expl = {}, {hid: 1.0 for hid in family}, {}
    n_conf, n_expl = {hid: 1.0 for hid in family}, {}
    for hid, h in registered.items():
        row = {'id': hid, 'group': h['group'], 'role': h['role'], 'metric': h['metric'], 'unit': h['unit'],
               'method': locked['methods'][hid], 'correction': 'holm' if hid in family else 'bh',
               'planned_n_per_arm': h['planned_n_per_arm'],
               'underpowered_design': locked['feasibility'][hid]['underpowered_registration']}
        rows[hid] = row
        r = by_id.get(hid)
        if r is None:
            row.update(verdict='INCONCLUSIVE', reason='没有结果数据')
            continue
        tn, tx, cn, cx = r['test_n'], r['test_x'], r['control_n'], r['control_x']
        row.update(test_n=tn, test_x=tx, control_n=cn, control_x=cx,
                   test_rate=round(tx / tn, 4), control_rate=round(cx / cn, 4), srm_p=round(srm_p(tn, cn), 4))
        row['srm_alert'] = row['srm_p'] < locked['srm_alert_p']
        flags = [f for f in r.get('quality_flags', []) if f in HARD_FLAGS]
        if flags:
            row.update(verdict='INVALID', reason='；'.join(HARD_FLAGS[f] for f in flags))
            continue
        # 50% 分流只保证进入同样多的竞价，展示数不会正好相等：按两组合计检查样本量，严重失衡交给失衡告警
        if look == 'final' and tn + cn < 2 * h['planned_n_per_arm']:
            row.update(verdict='INCONCLUSIVE', reason=f"到终点时两组合计 {tn + cn} 未达到登记的 {2 * h['planned_n_per_arm']}")
            continue
        both = r.get('visitors_in_both_arms')
        if both:
            visitors = r.get('visitors_total') or sum(
                int(v) for k in ('test_histogram', 'control_histogram') for v in (r.get(k) or {}).values())
            if not visitors:
                raise ValueError(f'{hid} 填了 visitors_in_both_arms，但没有访客总数：填 visitors_total 或两组直方图')
            if both / visitors > locked['guardrails']['both_arms_visitor_share_max']:
                row.update(verdict='INVALID', reason=f'两组都出现过的访客占 {both / visitors:.1%}，超过登记上限，分流被破坏')
                continue
        p_naive = run_test(row['method'], tx, tn, cx, cn)
        de_cfg = locked['design_effect']
        # 每组按自己的设计效应折算有效样本（Kish）；方差膨胀来自哪组的重复点击，就只放大那一组
        if h['unit'] != 'impression':
            de_mode, de_t, de_c = 'unit_matches', 1.0, 1.0
        else:
            rates = r.get('gclid_match_rate') or {}
            th, ch = r.get('test_histogram'), r.get('control_histogram')
            mt = design_effect_from_histograms([th]) if th else None
            mc = design_effect_from_histograms([ch]) if ch else None
            if mt or mc:
                row['de_measured'] = {'test': mt and round(mt, 3), 'control': mc and round(mc, 3)}
            matched = (mt and mc and all(isinstance(rates.get(k), (int, float)) and rates[k] >= de_cfg['min_match_rate']
                                         for k in ('test', 'control')))
            if matched:
                de_mode, base = 'measured', de_cfg['floor']
            else:
                # 关联不够时用区间上端；部分关联算出的值更大时取更大的，缺数据不能换来更轻的校正
                de_mode, base = 'band', de_cfg['band'][-1]
            de_t, de_c = max(base, mt or 1.0), max(base, mc or 1.0)
        ax, an_ = deflate(tx, tn, de_t)
        bx, bn = deflate(cx, cn, de_c)
        p = run_test(row['method'], ax, an_, bx, bn) if max(de_t, de_c) > 1 else p_naive
        if de_mode == 'band':
            row['p_band'] = {str(d): round(run_test(row['method'], *deflate(tx, tn, max(d, mt or 1.0) if d == de_cfg['band'][-1] else d),
                                                    *deflate(cx, cn, max(d, mc or 1.0) if d == de_cfg['band'][-1] else d))
                                           if d > 1 else p_naive, 4) for d in de_cfg['band']}
        de_used = max(de_t, de_c)
        row['de_by_arm'] = {'test': round(de_t, 3), 'control': round(de_c, 3)}
        row['effective_n'] = an_ + bn
        conf = 1 - a_look / max(len(family), 1) if hid in family else 1 - a_look
        lo, hi = diff_ci(ax, an_, bx, bn, conf=conf)
        row.update(p_naive=round(p_naive, 4), p_value=round(p, 4), de_mode=de_mode, de_used=round(de_used, 3),
                   diff=round(tx / tn - cx / cn, 4), diff_ci=[round(lo, 4), round(hi, 4)], ci_level=round(conf, 4))
        if r.get('daily'):
            row['daily_check'] = daily_direction(r['daily'], 1 if tx / tn >= cx / cn else -1)
        (p_conf if hid in family else p_expl)[hid] = p
        (n_conf if hid in family else n_expl)[hid] = p_naive
    sig, sig_naive = {}, {}
    if p_conf:
        sig.update(holm(p_conf, a_look))
        sig_naive.update(holm(n_conf, a_look))
    if p_expl:
        sig.update(bh(p_expl, a_look))
        sig_naive.update(bh(n_expl, a_look))
    for hid, row in rows.items():
        if 'verdict' in row:
            continue
        up = row['diff'] > 0
        if sig.get(hid):
            if hid in family:
                row['verdict'] = 'TEST_BETTER' if up else 'CONTROL_BETTER'
            else:
                row['verdict'] = 'SCREEN_PASS' if up else 'SCREEN_CONTROL_BETTER'
        elif look == 'interim':
            row.update(verdict='CONTINUE', reason='中期检查只决定停不停，不是结论')
        elif sig_naive.get(hid):
            row.update(verdict='FRAGILE', reason=f"不校正时显著，按设计效应 {row['de_used']} 校正后不显著")
        elif look == 'interim':
            row.update(verdict='CONTINUE', reason='中期检查只决定停不停，不是结论')
        elif row['underpowered_design'] or hid not in family:
            row.update(verdict='INCONCLUSIVE', reason='功效不足，未检出差异不代表没有差异')
        elif row['effective_n'] < 2 * h['planned_n_per_arm'] / (locked['design_effect']['floor']
                                                                  if h['unit'] == 'impression' else 1):
            row.update(verdict='INCONCLUSIVE', reason=f"校正后有效样本 {row['effective_n']} 低于功效要求，"
                                                      f"未检出差异不代表没有差异")
        else:
            row.update(verdict='NEUTRAL', reason=f"只排除了 {h['mde_lift_ratio']} 倍以上的差距，不代表没有差距")
    end = datetime.fromisoformat(data['launch_at']) + timedelta(days=days_run)
    notes = []
    if look == 'final':
        got = data.get('retrieved_at')
        if not got:
            notes.append('没填取数时间 retrieved_at')
        elif datetime.fromisoformat(got) < end + timedelta(days=3):
            notes.append('取数距结束不足 3 天，无效点击和转化可能还会回溯调整；正式结论前再取一次')
    if not data.get('git_commit'):
        notes.append('结果文件没填锁定文件的 git 提交号 git_commit，无法证明事先登记')
    return {'experiment_id': locked['experiment_id'], 'prereg_hash': digest, 'look': look,
            'alpha': alpha, 'alpha_this_look': a_look, 'family_size': len(family),
            'split_method': locked['split_method'], 'window_days': locked['window_days'], 'days_run': days_run,
            'exploratory': exploratory, 'exploratory_reasons': reasons, 'unregistered_views': views,
            'audience': locked['audience'], 'retrieved_at': data.get('retrieved_at'),
            'git_commit': data.get('git_commit'), 'notes': notes,
            'hypotheses': [rows[h] for h in registered]}


def render(rep):
    lines = [f"# 实验 {rep['experiment_id']} 检验结果（{'中期检查' if rep['look'] == 'interim' else '终点'}）",
             f"本次 α = {rep['alpha_this_look']}；确认性假设 Holm 校正（家族大小 {rep['family_size']}），探索性假设 "
             f"Benjamini–Hochberg 校正；分流：{SPLITS[rep['split_method']]}", '',
             f"登记哈希：{rep['prereg_hash']}；锁定文件 git 提交：{rep.get('git_commit') or '未提供'}", '']
    for n in rep.get('notes', []):
        lines += [f'注意：{n}', '']
    if rep.get('retrieved_at'):
        lines += [f"取数时间：{rep['retrieved_at']}", '']
    a = rep.get('audience') or {}
    if a:
        lines += [f"人群：设备 {', '.join(a['devices'])}；地区 {', '.join(a['locations'])}（只投所在地）；时段 {a['schedule']}；"
                  f"排除 {', '.join(a['exclusions']) or '无'}；周期 {rep['window_days']} 天（实际 {rep['days_run']} 天，"
                  f"覆盖不到完整一周）。**结论只对这批人、这几天成立；点击率更高只说明诉求更吸引点击，不说明用户愿意付费。**"
                  f"（确认人：{a['confirmed_by']}；确认时间：{a['confirmed_at']}）", '']
    if rep.get('unregistered_views'):
        tail = '本实验已因下列原因降为探索性。' if rep['exploratory'] else '结论不因此降级。'
        lines += [f"注：投放期间有 {rep['unregistered_views']} 次中途查看效果数据（未登记），之后没有据此停止或改设置，{tail}", '']
    if rep['family_size'] > 1:
        lines += [f"注：确认性假设的置信区间按 α/{rep['family_size']} 计算（同时成立的保守区间），比 Holm 判定更严；"
                  "因此可能出现 Holm 判显著而区间跨 0，结论以 Holm 判定为准。", '']
    if rep['exploratory']:
        lines += ['**本实验为探索性，结论只能用于决定下一轮测什么，不构成验证。** 原因：' + '；'.join(rep['exploratory_reasons']), '']
    lines += ['| 假设 | 组 | 角色 | 指标 | 方法 | 测试 | 对照 | 不校正 p | 设计效应 | 校正后 p | 差值（置信区间） | 按天方向（描述） | 失衡告警 | 结论 |',
              '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rep['hypotheses']:
        t = f"{r['test_x']}/{r['test_n']}" if 'test_n' in r else '—'
        c = f"{r['control_x']}/{r['control_n']}" if 'control_n' in r else '—'
        ci = f"{r['diff']:+.4f}（{r['diff_ci'][0]:+.4f}, {r['diff_ci'][1]:+.4f}；{r['ci_level']:.1%}）" if 'diff' in r else '—'
        srm = ('是（p=' + str(r['srm_p']) + '）') if r.get('srm_alert') else ('否' if 'srm_p' in r else '—')
        why = f"（{r['reason']}）" if r.get('reason') else ''
        dc = r.get('daily_check')
        day = f"{dc['agree']}/{dc['nonzero']} 天" if dc else '—'
        de = f"{r['de_used']}（{DE_MODE[r['de_mode']]}）" if 'de_used' in r else '—'
        if r.get('p_band'):
            de += '；区间 p：' + '，'.join(f'{k}→{v}' for k, v in r['p_band'].items())
        lines.append(f"| {r['id']} | {r['group']} | {r['role']} | {r['metric']} | {METHODS[r['method']]} | {t} | {c} | "
                     f"{r.get('p_naive', '—')} | {de} | {r.get('p_value', '—')} | {ci} | {day} | {srm} | {ZH[r['verdict']]}{why} |")
    return '\n'.join(lines)


RESULT_KEYS = ('verdict', 'reason', 'test_x', 'test_n', 'control_x', 'control_n', 'p_naive', 'p_value', 'de_mode',
               'de_used', 'de_measured', 'p_band', 'diff', 'diff_ci', 'daily_check')


def learning_log_entry(locked, rep, lesson):
    """一条学习记录 = 当时相信什么（假设与证据）+ 结果 + 学到什么 + 下一步，供 ad-campaign-build 第 1 步复盘。"""
    res = {r['id']: {k: r[k] for k in RESULT_KEYS if k in r} for r in rep['hypotheses']}
    return {'experiment_id': rep['experiment_id'], 'prereg_hash': rep['prereg_hash'], 'look': rep['look'],
            'exploratory': rep['exploratory'], 'exploratory_reasons': rep['exploratory_reasons'],
            'split_method': rep['split_method'], 'audience': rep['audience'],
            'window_days': rep['window_days'], 'days_run': rep['days_run'],
            'hypotheses': [{'id': h['id'], 'group': h['group'], 'role': h['role'], 'test_message': h.get('test_message'),
                            'evidence': h.get('evidence'), 'metric': h['metric'], 'baseline_rate': h['baseline_rate'],
                            'mde_lift_ratio': h['mde_lift_ratio'], 'method': locked['methods'][h['id']],
                            'result': res[h['id']]} for h in locked['hypotheses']],
            'lesson': lesson,
            'next_step': {r['id']: locked['decisions'][r['verdict']] for r in rep['hypotheses']},
            'recorded_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('plan')
    p.add_argument('--baseline', type=float, required=True)
    p.add_argument('--lift', type=float, required=True, help='比例倍数，2.0 表示翻倍')
    p.add_argument('--budget', type=float, required=True)
    p.add_argument('--cpc', type=float, required=True)
    p.add_argument('--alpha', type=float, default=0.05)
    p.add_argument('--power', type=float, default=0.8)
    p.add_argument('--metric', choices=('ctr', 'conversion'), default='ctr')
    p.add_argument('--daily-units', type=float, help='每天两组合计、按登记人群过滤后、按每日预算实际能买到的展示数（点击率）或访客数（转化）')
    p.add_argument('--confirmatory-count', type=int, default=1, help='同一实验里确认性假设的条数（α 按条数分摊）')
    p.add_argument('--window-days', type=int, default=MAX_WINDOW_DAYS)
    p.add_argument('--design-effect', type=float, default=1.0,
                   help='规划用设计效应（展示为单位时填登记的 design_effect.floor，没有历史数据用 1.25；访客为单位填 1）')
    lk = sub.add_parser('lock')
    lk.add_argument('prereg')
    lk.add_argument('--out', required=True, help='锁定文件路径；锁定失败时不写文件')
    an = sub.add_parser('analyze')
    an.add_argument('locked')
    an.add_argument('results')
    an.add_argument('--json', action='store_true')
    an.add_argument('--log', help='把学习记录追加到这个 jsonl 文件（需同时给 --lesson；下一步按登记的 decisions 自动填）')
    an.add_argument('--lesson', help='学到什么：一句话，只能基于脚本结论')
    a = ap.parse_args(argv)
    try:
        return run(ap, a)
    except (ValueError, KeyError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f'错误：{e}', file=sys.stderr)
        return 2


def run(ap, a):
    if a.cmd == 'plan':
        print(json.dumps(plan(a.baseline, a.lift, a.budget, a.cpc, a.alpha, a.power, 'two', a.metric, a.daily_units,
                              a.confirmatory_count, a.window_days, a.design_effect), ensure_ascii=False, indent=2))
    elif a.cmd == 'lock':
        locked = lock(json.load(open(a.prereg)))
        with open(a.out, 'w') as f:
            json.dump(locked, f, ensure_ascii=False, indent=2)
        print(f"已锁定：{a.out}，哈希 {locked['prereg_hash']}")
        for w in locked['warnings']:
            print(f'告警：{w}')
    else:
        if a.log and not a.lesson:
            ap.error('写学习记录必须同时给 --lesson')
        locked = json.load(open(a.locked))
        rep = analyze(locked, json.load(open(a.results)))
        if a.log and any(r['verdict'] == 'CONTINUE' for r in rep['hypotheses']):
            raise ValueError('中期检查没达到提前停止门槛，不是结论，不写学习记录；投到终点后再写')
        print(json.dumps(rep, ensure_ascii=False, indent=2) if a.json else render(rep))
        if a.log:
            with open(a.log, 'a') as f:
                f.write(json.dumps(learning_log_entry(locked, rep, a.lesson), ensure_ascii=False) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
