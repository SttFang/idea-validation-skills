#!/usr/bin/env python3
"""想法能赚多少钱：按区间估算收入、获客成本，并找出最该先验证的假设。

用法：python3 revenue_model.py model.json [--json]

model.json 里每个参数都写成 {"low": .., "mid": .., "high": .., "source": "出处"}，
没有出处的参数会被标为「假设」；接口实测得到的参数加 "measured": true。
other_monthly_visits（社区、开源、直达等非搜索渠道的月访问）可选，不填按 0。字段见 SKILL.md「算钱」与 model-template.json。

输出：
  1. 自下而上（搜索漏斗）：每月新增付费用户、稳态月收入、12 个月累计收入
  2. 付费获客：获客成本、单用户终身收入、两者之比
  3. 自上而下（竞品对标，可选）：竞品估算收入、你拿到目标份额时的收入
  4. 敏感度：其他参数取中值、只让一个参数在低值和高值之间变，12 个月累计收入的摆幅，从大到小
  5. 与你给的目标月收入比较（可选）
"""
import argparse
import json
import sys

LEVELS = ('low', 'mid', 'high')
RATES = {'click_share', 'visit_to_signup', 'signup_to_paid', 'visit_to_paid', 'your_share'}
OPTIONAL_DEFAULT_ZERO = ('other_monthly_visits',)
BOTTOM_UP = ('monthly_searches', 'click_share', 'visit_to_signup', 'signup_to_paid', 'price_per_month',
             'months_retained')
LABELS = {
    'monthly_searches': '月搜索量（去重后合计）',
    'other_monthly_visits': '非搜索渠道月访问（社区、开源、直达）',
    'click_share': '能拿到的点击份额',
    'visit_to_signup': '访问到注册',
    'signup_to_paid': '注册到付费',
    'price_per_month': '每月客单价',
    'months_retained': '平均付费月数',
    'cpc': '单次点击价',
    'competitor_monthly_visits': '竞品月访问',
    'visit_to_paid': '竞品访问到付费',
    'your_share': '你能拿到的份额',
}
HORIZON = 12


class ModelError(ValueError):
    pass


def check(model):
    params = model.get('params') or {}
    missing = [k for k in BOTTOM_UP if k not in params]
    if missing:
        raise ModelError('缺少参数：' + '、'.join(missing))
    for k, p in params.items():
        if k not in LABELS:
            raise ModelError(f'未知参数 {k}')
        if p.get('missing'):
            # 没有可靠数据（例如中文市场没有搜索量）：按 0 计入，并在报告里标出来
            for l in LEVELS:
                p[l] = 0
            p['source'] = ''
        vals = [p.get(l) for l in LEVELS]
        if any(not isinstance(v, (int, float)) for v in vals):
            raise ModelError(f'{k} 的 low/mid/high 必须都是数字')
        if not vals[0] <= vals[1] <= vals[2]:
            raise ModelError(f'{k} 必须满足 low ≤ mid ≤ high')
        if vals[0] < 0 or (k in RATES and vals[2] > 1):
            raise ModelError(f'{k} 超出范围（比例在 0–1 之间，其他不能为负）')
    if params['months_retained']['low'] < 1:
        raise ModelError('months_retained 至少为 1（一次性收费填 1）')
    return params


def bottom_up(v):
    visits = v['monthly_searches'] * v['click_share'] + v.get('other_monthly_visits', 0)
    new_paid = visits * v['visit_to_signup'] * v['signup_to_paid']
    life = v['months_retained']
    steady = new_paid * v['price_per_month'] * life
    cumulative = sum(new_paid * v['price_per_month'] * min(t, life) for t in range(1, HORIZON + 1))
    return {'new_paid_per_month': new_paid, 'steady_monthly_revenue': steady, 'revenue_12m': cumulative}


def pick(params, level, override=None):
    v = {k: p[level] for k, p in params.items()}
    if override:
        v.update(override)
    return v


def acquisition(v):
    if 'cpc' not in v:
        return None
    conv = v['visit_to_signup'] * v['signup_to_paid']
    ltv = v['price_per_month'] * v['months_retained']
    cac = v['cpc'] / conv if conv > 0 else float('inf')
    return {'cac': cac, 'ltv': ltv, 'ltv_to_cac': ltv / cac if cac else float('inf')}


def top_down(v):
    if not all(k in v for k in ('competitor_monthly_visits', 'visit_to_paid', 'your_share')):
        return None
    comp = v['competitor_monthly_visits'] * v['visit_to_paid'] * v['price_per_month']
    return {'competitor_monthly_revenue': comp, 'your_monthly_revenue': comp * v['your_share']}


def sensitivity(params):
    base = pick(params, 'mid')
    rows = []
    for k in BOTTOM_UP + tuple(x for x in OPTIONAL_DEFAULT_ZERO if x in params):
        lo = bottom_up({**base, k: params[k]['low']})['revenue_12m']
        hi = bottom_up({**base, k: params[k]['high']})['revenue_12m']
        rows.append({'param': k, 'low_12m': lo, 'high_12m': hi, 'swing': hi - lo})
    return sorted(rows, key=lambda r: -r['swing'])


def run(model):
    params = check(model)
    out = {'currency': model.get('currency', 'USD'), 'scenarios': {}, 'assumptions': []}
    for level in LEVELS:
        v = pick(params, level)
        out['scenarios'][level] = {'bottom_up': bottom_up(v), 'acquisition': acquisition(v),
                                   'top_down': top_down(v)}
    out['sensitivity'] = sensitivity(params)
    out['assumptions'] = [k for k, p in params.items() if not str(p.get('source', '')).strip()]
    out['missing'] = [k for k, p in params.items() if p.get('missing')]
    measured = {k for k, p in params.items() if p.get('measured')}
    # 实测值（如接口拉到的月搜索量）不能靠验证变大，最该先验证的是摆幅最大的非实测参数
    candidates = [r for r in out['sensitivity'] if r['param'] not in measured]
    out['top_to_validate'] = candidates[0]['param'] if candidates else None
    target = model.get('target_monthly_revenue')
    if target is not None:
        out['target'] = {l: out['scenarios'][l]['bottom_up']['steady_monthly_revenue'] >= target for l in LEVELS}
    return out


def fmt(x):
    if x == float('inf'):
        return '∞'
    return f'{x:,.0f}' if abs(x) >= 100 else f'{x:,.2f}'


def render(model, out):
    cur = out['currency']
    names = {'low': '全部取低', 'mid': '全部取中', 'high': '全部取高'}
    print(f'# 想法收入估算（{cur}）\n')
    print('| 情景 | 每月新增付费用户 | 稳态月收入 | 12 个月累计收入 |')
    print('|---|---|---|---|')
    for l in LEVELS:
        b = out['scenarios'][l]['bottom_up']
        print(f"| {names[l]} | {fmt(b['new_paid_per_month'])} | {fmt(b['steady_monthly_revenue'])} | {fmt(b['revenue_12m'])} |")
    if out['scenarios']['mid']['acquisition']:
        print('\n| 情景 | 获客成本 | 单用户终身收入 | 终身收入 ÷ 获客成本 |')
        print('|---|---|---|---|')
        for l in LEVELS:
            a = out['scenarios'][l]['acquisition']
            print(f"| {names[l]} | {fmt(a['cac'])} | {fmt(a['ltv'])} | {fmt(a['ltv_to_cac'])} |")
    if out['scenarios']['mid']['top_down']:
        print('\n| 情景 | 竞品估算月收入 | 你拿到目标份额时的月收入 |')
        print('|---|---|---|')
        for l in LEVELS:
            t = out['scenarios'][l]['top_down']
            print(f"| {names[l]} | {fmt(t['competitor_monthly_revenue'])} | {fmt(t['your_monthly_revenue'])} |")
    print('\n## 敏感度（其他取中值，只变这一项）\n')
    print('| 参数 | 取低时 12 个月收入 | 取高时 12 个月收入 | 摆幅 |')
    print('|---|---|---|---|')
    for r in out['sensitivity']:
        print(f"| {LABELS[r['param']]} | {fmt(r['low_12m'])} | {fmt(r['high_12m'])} | {fmt(r['swing'])} |")
    top = out['top_to_validate']
    if top:
        print(f'\n最该先验证的假设：**{LABELS[top]}**（非实测参数里摆幅最大）。')
    if out['sensitivity'][0]['param'] != top:
        print(f"摆幅最大的是实测参数「{LABELS[out['sensitivity'][0]['param']]}」：它决定上限，但不能靠验证改变。")
    if out.get('missing'):
        print('缺数据、按 0 计入的参数：' + '、'.join(LABELS[k] for k in out['missing']) + '。这一块收入被低估，结论里要写明。')
    if out['assumptions']:
        print('没有出处、只是假设的参数：' + '、'.join(LABELS[k] for k in out['assumptions']) + '。')
    acq = out['scenarios']['mid']['acquisition']
    if acq and acq['ltv_to_cac'] < 1:
        print('中值情景下，单用户终身收入低于获客成本：靠买搜索广告获客会亏钱。')
    if 'target' in out:
        hit = [names[l] for l in LEVELS if out['target'][l]]
        print(f"目标月收入 {fmt(model['target_monthly_revenue'])}：" + ('达到的情景：' + '、'.join(hit) if hit else '三种情景都达不到。'))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('models', nargs='+', help='一个或多个模型文件；多个时（如中文、英文各一个）先分别输出，再给合计')
    ap.add_argument('--json', action='store_true', help='输出 JSON 而不是表格')
    ap.add_argument('--target', type=float, help='合计的目标月收入（默认取第一个模型的 target_monthly_revenue）')
    a = ap.parse_args(argv)
    results = []
    for path in a.models:
        with open(path, encoding='utf-8') as f:
            model = json.load(f)
        try:
            out = run(model)
        except ModelError as e:
            print(f'{path} 模型不合格：{e}', file=sys.stderr)
            return 1
        results.append((path, model, out))
    if a.json:
        print(json.dumps({p: o for p, _, o in results}, ensure_ascii=False, indent=1))
        return 0
    for path, model, out in results:
        if len(results) > 1:
            print(f'\n# {model.get("market") or path}\n')
        render(model, out)
    if len(results) > 1:
        currencies = {o['currency'] for _, _, o in results}
        if len(currencies) > 1:
            print('\n各模型币种不同，不合计。请统一成同一币种后再合计。')
            return 0
        target = a.target if a.target is not None else results[0][1].get('target_monthly_revenue')
        print('\n# 合计\n\n| 情景 | 稳态月收入 | 12 个月累计收入 |\n|---|---|---|')
        names = {'low': '全部取低', 'mid': '全部取中', 'high': '全部取高'}
        for l in LEVELS:
            steady = sum(o['scenarios'][l]['bottom_up']['steady_monthly_revenue'] for _, _, o in results)
            cum = sum(o['scenarios'][l]['bottom_up']['revenue_12m'] for _, _, o in results)
            hit = '' if target is None else ('（达到目标）' if steady >= target else '')
            print(f'| {names[l]} | {fmt(steady)}{hit} | {fmt(cum)} |')
        if target is not None:
            print(f'\n合计目标月收入 {fmt(target)}。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
