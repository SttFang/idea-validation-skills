#!/usr/bin/env python3
"""APIsRouter 信息接口的最小客户端：查目录、看接口说明、按上限购买、读结果、记花费。

密钥：环境变量 APISROUTER_API_KEY，或文件 ~/.config/apisrouter/api-key（权限 600）。
不会打印密钥。

用法：
  python3 ar.py search similarweb
  python3 ar.py describe search.google-ads.keyword-volume.v1
  python3 ar.py buy search.google.autocomplete.v1 \
      --input '{"client":"gws-wiz-serp","keyword":"how to ...","language_code":"en","location_code":2840}' \
      --max-usd 0.01 --out data/autocomplete-us.json --ledger spend.jsonl --cap 5

buy 的保护：
  - 报价超过 --max-usd 就不买；
  - 账本 --ledger 里累计花费加上这次报价超过 --cap 就不买；
  - 同一接口、同一输入 24 小时内买过，直接重读上次的结果，不再付费。
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

BASE = os.environ.get('APISROUTER_BASE', 'https://api.apisrouter.com/v1/information')
REUSE_SECONDS = 24 * 3600


def api_key():
    key = os.environ.get('APISROUTER_API_KEY', '').strip()
    if not key:
        path = os.path.expanduser('~/.config/apisrouter/api-key')
        if os.path.exists(path):
            key = open(path).read().strip()
    if not key:
        sys.exit('缺少密钥：设置 APISROUTER_API_KEY，或写入 ~/.config/apisrouter/api-key（chmod 600）。'
                 '密钥在 https://apisrouter.com 控制台创建，需普通密钥（不能是限定模型的密钥）。')
    return key


def call(method, path, body=None, auth=True):
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if auth:
        headers['Authorization'] = 'Bearer ' + api_key()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            doc = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            doc = json.load(e)
        except Exception:
            sys.exit(f'{method} {path} 失败：HTTP {e.code}')
    if doc.get('status') != 'success':
        sys.exit(f"{method} {path} 失败：{doc.get('code')} {doc.get('next_actions') or ''}")
    return doc.get('data')


def input_hash(ref, inp):
    return hashlib.sha256(json.dumps([ref, inp], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_ledger(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def cmd_search(a):
    q = urllib.parse.urlencode({'q': a.query, 'limit': a.limit, 'offset': a.offset})
    for o in call('GET', f'/catalog?{q}', auth=False) or []:
        print(f"{o['operation_ref']}\t${o['unit_price']}\t{o.get('title_zh') or o['title']}")


def cmd_describe(a):
    o = call('GET', f'/catalog/{a.ref}', auth=False)
    print(f"{o['operation_ref']}  {o.get('title_zh') or o['title']}")
    print(f"单价 ${o['unit_price']} / {o.get('unit_zh') or o['unit']}；空结果{'也收费' if o.get('empty_result_policy') == 'charge' else '不收费'}")
    print('必填：', ', '.join(k for k, v in o['input_fields'].items() if v.get('required')))
    print('选填：', ', '.join(k for k, v in o['input_fields'].items() if not v.get('required')))
    print('示例输入：', json.dumps(o.get('example_input'), ensure_ascii=False))


def fetch_results(result_ref):
    records, offset = [], 0
    while True:
        page = call('GET', f'/results/{result_ref}?offset={offset}&limit=1000')
        records += page.get('records') or []
        offset += len(page.get('records') or [])
        if not page.get('records') or offset >= page.get('total', 0):
            return records


def save(out, ref, inp, records):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'operation_ref': ref, 'input': inp, 'records': records}, f, ensure_ascii=False, indent=1)
    print(f'已保存 {out}（{sum(r.get("item_count", 0) for r in records)} 条）')


def cmd_buy(a):
    inp = json.loads(a.input)
    h = input_hash(a.ref, inp)
    ledger = read_ledger(a.ledger)
    now = time.time()
    for row in reversed(ledger):
        if row['input_hash'] == h and row.get('result_ref') and now - row['at'] < REUSE_SECONDS:
            print('24 小时内买过同一输入，重读上次结果，不再付费。')
            return save(a.out, a.ref, inp, fetch_results(row['result_ref']))
    spent = sum(Decimal(r['charged_max_usd']) for r in ledger)
    quote = call('POST', '/quotes', {'operation_ref': a.ref, 'input': inp, 'quantity': 1})
    price = Decimal(quote['maximum_amount'])
    if price > Decimal(a.max_usd):
        sys.exit(f'报价 ${price} 超过本次上限 ${a.max_usd}，未购买。')
    if a.cap is not None and spent + price > Decimal(a.cap):
        sys.exit(f'累计 ${spent} + 本次 ${price} 超过总上限 ${a.cap}，未购买。')
    idem = f'irc-{h[:40]}-{int(now // REUSE_SECONDS)}'
    req = call('POST', '/requests', {'quote_ref': quote['quote_ref'], 'idempotency_key': idem,
                                     'max_charge_usd': str(price)})
    ref = req['request_ref']
    for _ in range(120):
        if req['status'] in ('completed', 'failed', 'cancelled', 'awaiting_payment', 'outcome_unknown',
                             'awaiting_price_confirmation'):
            break
        time.sleep(2)
        req = call('GET', f'/requests/{ref}')
    if req['status'] == 'awaiting_payment':
        sys.exit(f'余额不足，请到 https://apisrouter.com/console/recharge 充值后运行：'
                 f'python3 ar.py resume {ref}（请求保留 24 小时）。')
    if req['status'] != 'completed':
        sys.exit(f"请求 {ref} 状态 {req['status']}，不要换新请求重买；稍后用 python3 ar.py status {ref} 查看。")
    if a.ledger:
        with open(a.ledger, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'at': now, 'operation_ref': a.ref, 'input_hash': h, 'request_ref': ref,
                                'order_ref': req.get('order_ref'), 'result_ref': req['result_ref'],
                                'charged_max_usd': str(price), 'out': a.out}, ensure_ascii=False) + '\n')
    print(f'已购买 {a.ref}，最多 ${price}；累计最多 ${spent + price}')
    save(a.out, a.ref, inp, fetch_results(req['result_ref']))


def cmd_status(a):
    print(json.dumps(call('GET', f'/requests/{a.request_ref}'), ensure_ascii=False, indent=1))


def cmd_resume(a):
    print(json.dumps(call('POST', f'/requests/{a.request_ref}/resume', {}), ensure_ascii=False, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('search'); s.add_argument('query'); s.add_argument('--limit', type=int, default=20)
    s.add_argument('--offset', type=int, default=0); s.set_defaults(fn=cmd_search)
    d = sub.add_parser('describe'); d.add_argument('ref'); d.set_defaults(fn=cmd_describe)
    b = sub.add_parser('buy'); b.add_argument('ref'); b.add_argument('--input', required=True)
    b.add_argument('--max-usd', required=True); b.add_argument('--out', required=True)
    b.add_argument('--ledger', default='spend.jsonl'); b.add_argument('--cap')
    b.set_defaults(fn=cmd_buy)
    for name, fn in (('status', cmd_status), ('resume', cmd_resume)):
        p = sub.add_parser(name); p.add_argument('request_ref'); p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == '__main__':
    main()
