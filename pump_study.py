#!/usr/bin/env python3
"""小时级「暴涨前」信号回测 v2。

数据限制（实测）：
  - klines 1h limit=1000   -> 1000 小时（约 41 天）
  - openInterestHist 1h limit=500 -> 只有 500 小时（约 21 天，币安只留约 30 天）
  - fundingRate limit=500  -> 500 条（8 小时一条，约 167 天）

所以 K 线与 OI 的交集约 500 小时/symbol，够做小时级统计。
"""
import http.client
import json
import os
import queue
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlencode

PROXY = ("172.28.80.1", 7890)
FAPI_HOST = "fapi.binance.com"
OUT_DIR = "/home/tou/.hermes/oi_monitor"
IN_FILE = os.path.join(OUT_DIR, "oi_scan.json")
OUT_FILE = os.path.join(OUT_DIR, "pump_study.json")

TIMEOUT = 15
CONN_RETRIES = 7
CONN_SLEEP = 0.4
RATE_RETRIES = 2
RATE_SLEEP = 5

UNIVERSE_SIZE = 200
MAX_WORKERS = 8
VOL_WINDOW = 72
EVENT_THRESHOLDS = (0.05, 0.08)
CASE_LIMIT = 15

HOUR_MS = 3600000
DAY_MS = 86400000

SIGNAL_NAMES = ["S1_oi_price_flat", "S2_funding_neg", "S3_vol_spike", "S4_oi_up"]
COMBO_NAMES = ["S1&S2", "S1&S3"]


def threshold_key(threshold):
    return f"{threshold:.2f}"


class FetchError(Exception):
    pass


POOL_SIZE = 6        # 每个 host 最多复用几条长连接
POOL_IDLE_MAX = 60   # 闲置超过这么多秒的连接丢弃

_POOLS = {}
_POOL_LOCK = threading.Lock()


def _pool(host):
    with _POOL_LOCK:
        q = _POOLS.get(host)
        if q is None:
            q = queue.LifoQueue(maxsize=POOL_SIZE)
            _POOLS[host] = q
        return q


def _new_conn(host):
    last = None
    for _ in range(CONN_RETRIES):
        conn = http.client.HTTPSConnection(PROXY[0], PROXY[1], timeout=TIMEOUT)
        try:
            conn.set_tunnel(host, 443)
            return conn
        except (OSError, http.client.HTTPException) as exc:
            last = exc
            try:
                conn.close()
            except Exception:
                pass
            time.sleep(CONN_SLEEP)
    raise FetchError(f"conn: {type(last).__name__}: {last}")


def _acquire(host):
    q = _pool(host)
    while True:
        try:
            conn, ts = q.get_nowait()
        except queue.Empty:
            return _new_conn(host)
        if time.time() - ts < POOL_IDLE_MAX:
            return conn
        try:
            conn.close()
        except Exception:
            pass


def _release(host, conn):
    try:
        _pool(host).put_nowait((conn, time.time()))
    except queue.Full:
        try:
            conn.close()
        except Exception:
            pass


def http_get_json(host, path, params=None):
    if params:
        path = path + "?" + urlencode(params)
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Host": host}
    conn_fail = 0
    rate_fail = 0
    while True:
        conn = _acquire(host)
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            body = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            conn_fail += 1
            try:
                conn.close()
            except Exception:
                pass
            if conn_fail >= CONN_RETRIES:
                raise FetchError(f"conn: {type(exc).__name__}: {exc}")
            time.sleep(CONN_SLEEP)
            continue
        if status == 200:
            _release(host, conn)
            try:
                return json.loads(body)
            except ValueError as exc:
                raise FetchError(f"bad json: {exc}")
        if status == 429:
            try:
                conn.close()
            except Exception:
                pass
            rate_fail += 1
            if rate_fail > RATE_RETRIES:
                raise FetchError("http 429")
            time.sleep(RATE_SLEEP)
            continue
        try:
            conn.close()
        except Exception:
            pass
        raise FetchError(f"http {status}: {body[:200].decode('utf-8', 'replace')}")


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def hour_key(ms):
    return ms // HOUR_MS


def hour_str(key):
    return datetime.fromtimestamp(key * 3600, tz=timezone.utc).strftime("%Y-%m-%d %H:00")


def load_universe(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data.get("rows") or []
    ranked = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        vol = row.get("vol24h_usd")
        if not symbol or vol is None:
            continue
        ranked.append((float(vol), symbol))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [symbol for _, symbol in ranked[:UNIVERSE_SIZE]]


def fetch_klines(symbol):
    data = http_get_json(
        FAPI_HOST,
        "/fapi/v1/klines",
        {"symbol": symbol, "interval": "1h", "limit": 1000},
    )
    out = {}
    for row in data or []:
        if not isinstance(row, (list, tuple)) or len(row) < 8:
            continue
        ts = to_int(row[0])
        close = to_float(row[4])
        quote_volume = to_float(row[7])
        if ts is None or close is None or quote_volume is None:
            continue
        out[hour_key(ts)] = {"close": close, "quote_volume": quote_volume}
    return out


def fetch_oi(symbol):
    data = http_get_json(
        FAPI_HOST,
        "/futures/data/openInterestHist",
        {"symbol": symbol, "period": "1h", "limit": 500},
    )
    out = {}
    for row in data or []:
        if not isinstance(row, dict):
            continue
        ts = to_int(row.get("timestamp"))
        oi_value = to_float(row.get("sumOpenInterestValue"))
        if ts is None or oi_value is None:
            continue
        out[hour_key(ts)] = oi_value
    return out


def fetch_funding(symbol):
    """按 UTC 日加总成日费率：{day_key: sum_rate}。"""
    data = http_get_json(
        FAPI_HOST,
        "/fapi/v1/fundingRate",
        {"symbol": symbol, "limit": 500},
    )
    out = {}
    for row in data or []:
        if not isinstance(row, dict):
            continue
        ts = to_int(row.get("fundingTime"))
        rate = to_float(row.get("fundingRate"))
        if ts is None or rate is None:
            continue
        key = ts // DAY_MS
        out[key] = out.get(key, 0.0) + rate
    return out


def build_series(symbol):
    klines = fetch_klines(symbol)
    oi = fetch_oi(symbol)
    funding_daily = fetch_funding(symbol)

    # 只保留 K 线 与 OI 都有 的小时；funding 用该小时所属 UTC 日的值
    common = sorted(set(klines) & set(oi))
    if not common:
        raise FetchError("no aligned hours")

    series = {}
    volumes = []
    for key in common:
        kline = klines[key]
        quote_volume = kline["quote_volume"]
        vol_x = None
        if len(volumes) >= VOL_WINDOW:
            baseline = statistics.median(volumes[-VOL_WINDOW:])
            if baseline > 0:
                vol_x = quote_volume / baseline
        volumes.append(quote_volume)
        fund_day = funding_daily.get((key * HOUR_MS) // DAY_MS)
        series[key] = {
            "close": kline["close"],
            "oi": oi[key],
            "fund": fund_day,
            "vol_x": vol_x,
        }

    keys = sorted(series)
    for index, key in enumerate(keys):
        if index == 0:
            continue
        entry = series[key]
        prev = series[keys[index - 1]]
        if prev["close"]:
            entry["ret"] = entry["close"] / prev["close"] - 1
        if prev["oi"]:
            entry["oi_chg"] = entry["oi"] / prev["oi"] - 1

    available = [
        key for key in keys
        if series[key].get("ret") is not None
        and series[key].get("oi_chg") is not None
        and series[key].get("vol_x") is not None
    ]
    if not available:
        raise FetchError("no derivable hours")
    return series, available


def check_signal(name, entry):
    if name == "S1_oi_price_flat":
        return (
            entry.get("oi_chg") is not None
            and entry.get("ret") is not None
            and entry["oi_chg"] >= 0.02
            and abs(entry["ret"]) <= 0.01
        )
    if name == "S2_funding_neg":
        return entry.get("fund") is not None and entry["fund"] < 0
    if name == "S3_vol_spike":
        return entry.get("vol_x") is not None and entry["vol_x"] >= 3
    if name == "S4_oi_up":
        return entry.get("oi_chg") is not None and entry["oi_chg"] >= 0.02
    raise ValueError(name)


def combos_for(entry):
    s1 = check_signal("S1_oi_price_flat", entry)
    s2 = check_signal("S2_funding_neg", entry)
    s3 = check_signal("S3_vol_spike", entry)
    return {"S1&S2": s1 and s2, "S1&S3": s1 and s3}


def collect_stats(series_map, threshold):
    event_count = 0
    all_hours = 0
    hits = {name: 0 for name in SIGNAL_NAMES}
    combo_hits = {name: 0 for name in COMBO_NAMES}
    base_hits = {name: 0 for name in SIGNAL_NAMES}
    base_combo_hits = {name: 0 for name in COMBO_NAMES}
    none_hits = 0
    cases = []

    for symbol, (series, available) in series_map.items():
        for key in available:
            entry = series[key]
            prev = series.get(key - 1)
            if prev is None or prev.get("ret") is None or prev.get("oi_chg") is None:
                continue
            if prev.get("vol_x") is None:
                continue
            all_hours += 1
            for name in SIGNAL_NAMES:
                if check_signal(name, prev):
                    base_hits[name] += 1
            for name, value in combos_for(prev).items():
                if value:
                    base_combo_hits[name] += 1
            if entry["ret"] < threshold:
                continue

            event_count += 1
            triggered = False
            for name in SIGNAL_NAMES:
                if check_signal(name, prev):
                    hits[name] += 1
                    triggered = True
            for name, value in combos_for(prev).items():
                if value:
                    combo_hits[name] += 1
            if not triggered:
                none_hits += 1

            cases.append({
                "symbol": symbol,
                "key": key,
                "ts_iso": datetime.fromtimestamp(key * 3600, tz=timezone.utc).isoformat(timespec="seconds"),
                "ret": entry["ret"],
                "prev_oi_chg": prev["oi_chg"],
                "prev_ret": prev["ret"],
                "prev_fund": prev["fund"],
                "prev_vol_x": prev["vol_x"],
            })

    return {
        "event_count": event_count,
        "all_hours": all_hours,
        "hits": hits,
        "base_hits": base_hits,
        "combo_hits": combo_hits,
        "base_combo_hits": base_combo_hits,
        "none_hits": none_hits,
        "cases": cases,
    }


def lift_of(hit_rate, base):
    if base is None or base == 0 or hit_rate is None:
        return None
    return hit_rate / base


def render_group(names, hit_counts, base_counts, event_count, all_hours):
    out = {}
    for name in names:
        hit = hit_counts[name]
        base_count = base_counts[name]
        hit_rate = (hit / event_count) if event_count else None
        base = (base_count / all_hours) if all_hours else None
        out[name] = {
            "hit": hit,
            "hit_rate": hit_rate,
            "base": base,
            "lift": lift_of(hit_rate, base),
        }
    return out


def fmt(value, digits=4):
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def print_table(thresholds):
    for key in sorted(thresholds, key=lambda item: float(item)):
        block = thresholds[key]
        print(
            f"\n== R={key}  events={block['events']}  "
            f"all_hours={block['all_hours']}  none_rate={fmt(block['none_rate'])} =="
        )
        print(f"{'signal':<18}{'hit_rate':>10}{'base':>10}{'lift':>10}{'hit':>8}")
        for name, row in list(block["signals"].items()) + list(block["combos"].items()):
            print(
                f"{name:<18}{fmt(row['hit_rate']):>10}{fmt(row['base']):>10}"
                f"{fmt(row['lift'], 3):>10}{row['hit']:>8}"
            )


def main():
    started = time.time()
    symbols = load_universe(IN_FILE)
    print(f"[universe] requested={len(symbols)}", flush=True)

    series_map = {}
    skipped = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(build_series, symbol): symbol for symbol in symbols}
        for done, future in enumerate(futures, 1):
            symbol = futures[future]
            try:
                series_map[symbol] = future.result()
            except Exception as exc:
                skipped += 1
                print(f"[skip] {symbol}: {type(exc).__name__}: {exc}", flush=True)
            if done % 25 == 0 or done == len(symbols):
                print(f"[fetch] {done}/{len(symbols)} skipped={skipped}", flush=True)

    all_cases = []
    thresholds_out = {}
    for threshold in EVENT_THRESHOLDS:
        raw = collect_stats(series_map, threshold)
        all_cases.extend(raw["cases"])
        event_count = raw["event_count"]
        thresholds_out[threshold_key(threshold)] = {
            "events": event_count,
            "all_hours": raw["all_hours"],
            "signals": render_group(
                SIGNAL_NAMES, raw["hits"], raw["base_hits"], event_count, raw["all_hours"]
            ),
            "combos": render_group(
                COMBO_NAMES, raw["combo_hits"], raw["base_combo_hits"], event_count, raw["all_hours"]
            ),
            "none_rate": (raw["none_hits"] / event_count) if event_count else None,
        }

    all_cases.sort(key=lambda item: (item["key"], item["symbol"]))
    cases = all_cases[-CASE_LIMIT:]
    cases.reverse()
    cases = [
        {
            "symbol": item["symbol"],
            "ts_iso": item["ts_iso"],
            "ret": item["ret"],
            "prev_oi_chg": item["prev_oi_chg"],
            "prev_ret": item["prev_ret"],
            "prev_fund": item["prev_fund"],
            "prev_vol_x": item["prev_vol_x"],
        }
        for item in cases
    ]

    hours_total = 0
    first_key = None
    last_key = None
    for series, available in series_map.values():
        hours_total += len(available)
        for key in available:
            if first_key is None or key < first_key:
                first_key = key
            if last_key is None or key > last_key:
                last_key = key

    output = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "universe": {
            "symbols_requested": len(symbols),
            "symbols_used": len(series_map),
            "skipped": skipped,
            "hours_total": hours_total,
            "window": {
                "first": hour_str(first_key) if first_key is not None else None,
                "last": hour_str(last_key) if last_key is not None else None,
            },
        },
        "thresholds": thresholds_out,
        "cases": cases,
        "notes": [
            "币安 openInterestHist 只保留最近约 30 天 → OI 类信号只能在这个窗口内回测（小时级约 21 天）",
            "信号一律取事件前一小时的数据；事件是下一小时涨幅",
            "这是条件频率统计，不是收益回测，不代表能赚钱",
            "数据源：币安 fapi klines(1h) / openInterestHist(1h) / fundingRate",
        ],
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = OUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT_FILE)

    print_table(thresholds_out)
    print(
        f"\n[done] used={len(series_map)} skipped={skipped} hours_total={hours_total} "
        f"elapsed={time.time() - started:.1f}s -> {OUT_FILE}",
        flush=True,
    )


if __name__ == "__main__":
    main()
