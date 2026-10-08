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


def api_key(required=True):
    key = os.environ.get('APISROUTER_API_KEY', '').strip()
    if not key:
        path = os.path.expanduser('~/.config/apisrouter/api-key')
        if os.path.exists(path):
            key = open(path).read().strip()
    if not key and not required:
        return None
    if not key:
        sys.exit('缺少密钥：设置 APISROUTER_API_KEY，或写入 ~/.config/apisrouter/api-key（chmod 600）。'
                 '密钥在 https://apisrouter.com 控制台创建，需普通密钥（不能是限定模型的密钥）。')
    return key


RATE_LIMIT_RETRIES = 6


def retry_after(e, attempt):
    """按服务端的 Retry-After 等待；旧服务端没有这个头时退避，最多等 60 秒。"""
    try:
        return min(60.0, max(1.0, float(e.headers.get('Retry-After'))))
    except (TypeError, ValueError):
        return min(60.0, 5.0 * (attempt + 1))


def call(method, path, body=None, auth=True, retries=3, return_error=False):
    """网络中断时重试，遇到限流（429）按 Retry-After 等待后重试。
    下单请求带同一个报价和幂等键，服务端保证重试不会重复扣费。
    auth：True 必须带密钥；'optional' 有密钥就带（目录查询用，便于按调用方计量和限流），没有就匿名。
    return_error：改为返回 (是否成功, 成功时的 data / 失败时的完整返回体)，而不是失败就退出（buy 处理幂等冲突要用）。"""
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    key = api_key(required=(auth is True)) if auth else None
    if key:
        headers['Authorization'] = 'Bearer ' + key
    data = json.dumps(body).encode() if body is not None else None
    attempt = limited = 0
    while True:
        req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                doc = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and limited < RATE_LIMIT_RETRIES:
                wait = retry_after(e, limited)
                limited += 1
                print(f'被限流（429），{wait:.0f} 秒后重试……', file=sys.stderr)
                time.sleep(wait)
                continue
            try:
                doc = json.load(e)
            except Exception:
                sys.exit(f'{method} {path} 失败：HTTP {e.code}')
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            attempt += 1
            if attempt >= retries:
                sys.exit(f'{method} {path} 网络失败（已重试 {retries} 次）：{e}。'
                         '如果是下单这一步，重跑同一命令即可接上已有请求，不要换输入重买。')
            time.sleep(2 * attempt)
    if return_error:
        ok = doc.get('status') == 'success'
        return ok, (doc.get('data') if ok else doc)
    if doc.get('status') != 'success':
        detail = json.dumps({k: doc.get(k) for k in ('code', 'summary', 'next_actions')}, ensure_ascii=False)
        sys.exit(f'{method} {path} 失败：{detail}。字段写错时先运行 python3 ar.py describe <接口> 对照必填项和示例输入。')
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
    for o in call('GET', f'/catalog?{q}', auth='optional') or []:
        print(f"{o['operation_ref']}\t${o['unit_price']}\t{o.get('title_zh') or o['title']}")


def cmd_describe(a):
    o = call('GET', f'/catalog/{a.ref}', auth='optional')
    print(f"{o['operation_ref']}  {o.get('title_zh') or o['title']}")
    print(f"单价 ${o['unit_price']} / {o.get('unit_zh') or o['unit']}；空结果{'也收费' if o.get('empty_result_policy') == 'charge' else '不收费'}")
    print('必填：', ', '.join(k for k, v in o['input_fields'].items() if v.get('required')))
    print('选填：', ', '.join(k for k, v in o['input_fields'].items() if not v.get('required')))
    for k, v in o['input_fields'].items():
        limits = {x: v[x] for x in ('max_items', 'max_length', 'max_characters', 'max_words', 'min_number', 'max_number', 'enum') if x in v}
        if limits and set(limits) - {'max_length', 'min_number', 'max_number'}:
            print(f'  {k} 限制：', json.dumps(limits, ensure_ascii=False))
    print('示例输入：', json.dumps(o.get('example_input'), ensure_ascii=False))
    print('提示：枚举值目录里不一定列出；已知限制见 data-sources.md「已知参数限制」。')


def fetch_results(result_ref):
    records, offset = [], 0
    while True:
        page = call('GET', f'/results/{result_ref}?offset={offset}&limit=1000')
        records += page.get('records') or []
        offset += len(page.get('records') or [])
        if not page.get('records') or offset >= page.get('total', 0):
            return records


def count_items(records):
    n = 0
    for r in records:
        if isinstance(r, dict) and isinstance(r.get('item_count'), int):
            n += r['item_count']
        elif isinstance(r, dict) and isinstance(r.get('items'), list):
            n += len(r['items'])
        else:
            n += 1
    return n


def save(out, ref, inp, records):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'operation_ref': ref, 'input': inp, 'records': records}, f, ensure_ascii=False, indent=1)
    print(f'已保存 {out}（{len(records)} 段结果，约 {count_items(records)} 条）')


POLL_SECONDS = 600
FINAL = ('completed', 'failed', 'cancelled', 'awaiting_payment', 'outcome_unknown', 'awaiting_price_confirmation')


def append_ledger(path, row):
    if path:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')


def ledger_spent(ledger):
    """同一请求可能先记「已下单」再记「已完成」，按请求编号只算一次。"""
    per_ref = {}
    for r in ledger:
        key = r.get('request_ref') or id(r)
        per_ref[key] = max(per_ref.get(key, Decimal(0)), Decimal(r['charged_max_usd']))
    return sum(per_ref.values(), Decimal(0))


def cmd_buy(a):
    inp = json.loads(a.input)
    h = input_hash(a.ref, inp)
    ledger = read_ledger(a.ledger)
    now = time.time()
    recent = [r for r in ledger if r['input_hash'] == h and now - r['at'] < REUSE_SECONDS]
    done = [r for r in recent if r.get('result_ref')]
    if done:
        print('24 小时内买过同一输入，重读上次结果，不再付费。')
        return save(a.out, a.ref, inp, fetch_results(done[-1]['result_ref']))
    spent = ledger_spent(ledger)
    pending = [r for r in recent if r.get('request_ref')]
    if pending:
        # 上次下单后中断（断网、超时）：直接接着查同一个请求，不再报价下单
        row = pending[-1]
        ref, price, resumed = row['request_ref'], Decimal(row['charged_max_usd']), True
        print(f'上次已下单 {ref}，接着查结果，不会重复扣费。')
        req = call('GET', f'/requests/{ref}')
    else:
        quote = call('POST', '/quotes', {'operation_ref': a.ref, 'input': inp, 'quantity': 1})
        price = Decimal(quote['maximum_amount'])
        if price > Decimal(a.max_usd):
            sys.exit(f'报价 ${price} 超过本次上限 ${a.max_usd}，未购买。')
        if a.cap is not None and spent + price > Decimal(a.cap):
            sys.exit(f'累计 ${spent} + 本次 ${price} 超过总上限 ${a.cap}，未购买。')
        # 幂等键绑定本轮账本：不同实验、不同会话买同一输入不会互相冲突
        scope = hashlib.sha256((os.path.abspath(a.ledger or '') + h).encode()).hexdigest()
        idem = f'irc-{scope[:40]}-{int(now // REUSE_SECONDS)}'
        ok, doc = call('POST', '/requests', {'quote_ref': quote['quote_ref'], 'idempotency_key': idem,
                                             'max_charge_usd': str(price)}, return_error=True)
        if not ok:
            if doc.get('code') != 'idempotency_conflict':
                detail = json.dumps({k: doc.get(k) for k in ('code', 'summary', 'next_actions')}, ensure_ascii=False)
                sys.exit(f'POST /requests 失败：{detail}')
            existing = (doc.get('data') or {}).get('existing')
            if not existing or not existing.get('request_ref'):
                sys.exit('幂等冲突：同一输入今天已经下过单（可能是上次运行中断）。旧服务端不返回已有请求，'
                         '请查 APIsRouter 控制台的订单，用 python3 ar.py status <request_ref> 核对；不要换输入重买。')
            print(f"同一输入今天已经下过单，接着读取已有请求 {existing['request_ref']}，不会重复扣费。")
            req, resumed = existing, True
        else:
            req, resumed = doc, False
        ref = req['request_ref']
        # 先记「已下单」：之后即使断网，账本里也有扣费记录，重跑同一命令会直接接上
        append_ledger(a.ledger, {'at': now, 'operation_ref': a.ref, 'input_hash': h, 'request_ref': ref,
                                 'status': 'submitted', 'charged_max_usd': str(price), 'out': a.out})
    deadline = time.time() + POLL_SECONDS
    while req['status'] not in FINAL:
        if time.time() > deadline:
            sys.exit(f'请求 {ref} 仍在执行（已等 {POLL_SECONDS // 60} 分钟）。账本已记下单记录，稍后重跑同一命令会接着取结果。')
        time.sleep(2)
        req = call('GET', f'/requests/{ref}')
    if req['status'] == 'awaiting_payment':
        sys.exit(f'余额不足，请到 https://apisrouter.com/console/recharge 充值后运行：'
                 f'python3 ar.py resume {ref}（请求保留 24 小时）。')
    if req['status'] != 'completed':
        sys.exit(f"请求 {ref} 状态 {req['status']}，不要换新请求重买；稍后用 python3 ar.py status {ref} 查看。")
    append_ledger(a.ledger, {'at': now, 'operation_ref': a.ref, 'input_hash': h, 'request_ref': ref,
                             'order_ref': req.get('order_ref'), 'result_ref': req['result_ref'], 'status': 'completed',
                             'charged_max_usd': str(price), 'out': a.out})
    if resumed:
        print(f'已接续之前的请求 {ref}（未重复扣费）：最多 ${price}')
    else:
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
