#!/usr/bin/env python3
import http.client
import json
import os
import queue
import re
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlencode

PROXY = ("172.28.80.1", 7890)
FAPI_HOST = "fapi.binance.com"
CG_HOST = "api.coingecko.com"
OUT_DIR = os.environ.get("OI_DIR", "/home/tou/.hermes/oi_monitor")
CG_CACHE = os.path.join(OUT_DIR, "cg_cache.json")
OUT_FILE = os.path.join(OUT_DIR, "oi_scan.json")
CG_TTL = 6 * 3600
CG_PAGES = 8
TIMEOUT = 15
CONN_RETRIES = 7
CONN_SLEEP = 0.4
RATE_RETRIES = 2
RATE_SLEEP = 5


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


def fetch_tickers():
    data = http_get_json(FAPI_HOST, "/fapi/v1/ticker/24hr")
    tickers = {}
    for item in data:
        symbol = item.get("symbol", "")
        if not symbol.endswith("USDT") or "_" in symbol:
            continue
        tickers[symbol] = {
            "price": to_float(item.get("lastPrice")),
            "change_pct": to_float(item.get("priceChangePercent")),
            "vol24h_usd": to_float(item.get("quoteVolume")),
            "count": int(to_float(item.get("count")) or 0),
        }
    return tickers


def fetch_crypto_symbols():
    """只返回加密永续合约：{symbol: 子类字符串}。"""
    data = http_get_json(FAPI_HOST, "/fapi/v1/exchangeInfo")
    out = {}
    for s in data.get("symbols", []):
        if s.get("underlyingType") != "COIN":
            continue
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("status") != "TRADING":
            continue
        subs = s.get("underlyingSubType") or []
        out[s.get("symbol")] = "/".join(subs[:2]) if subs else ""
    return out


def fetch_funding():
    """全量资金费率：{symbol: 最近一次资金费率(float)}（一次请求拿全部）。"""
    data = http_get_json(FAPI_HOST, "/fapi/v1/premiumIndex")
    out = {}
    for item in data or []:
        sym = item.get("symbol")
        if not sym:
            continue
        r = to_float(item.get("lastFundingRate"))
        if r is not None:
            out[sym] = r
    return out


def fetch_vol_x(symbol):
    """1h 量能倍数 = 最后一根 1h 成交额 / 之前 72 根 1h 成交额的中位数。"""
    data = http_get_json(FAPI_HOST, "/fapi/v1/klines",
                         {"symbol": symbol, "interval": "1h", "limit": 73})
    vols = [to_float(r[7]) for r in (data or []) if len(r) > 7]
    vols = [v for v in vols if v]
    if len(vols) < 20:
        return None
    med = statistics.median(vols[:-1])
    if not med:
        return None
    return vols[-1] / med


SCORE_W = {"vol_x": 0.35, "oi": 0.30, "funding": 0.20, "lev": 0.15}


def score_row(vol_x, oi_chg_1h, funding, oi_over_fdv):
    p = {}
    if vol_x is None:
        p["vol_x"] = None
    elif vol_x >= 5:
        p["vol_x"] = 100
    elif vol_x >= 3:
        p["vol_x"] = 80
    elif vol_x >= 2:
        p["vol_x"] = 55
    elif vol_x >= 1.5:
        p["vol_x"] = 35
    elif vol_x >= 1:
        p["vol_x"] = 20
    else:
        p["vol_x"] = 5

    if oi_chg_1h is None:
        p["oi"] = None
    elif oi_chg_1h >= 0.05:
        p["oi"] = 100
    elif oi_chg_1h >= 0.03:
        p["oi"] = 80
    elif oi_chg_1h >= 0.02:
        p["oi"] = 65
    elif oi_chg_1h >= 0.01:
        p["oi"] = 40
    elif oi_chg_1h >= 0:
        p["oi"] = 20
    elif oi_chg_1h >= -0.02:
        p["oi"] = 10
    else:
        p["oi"] = 0

    if funding is None:
        p["funding"] = None
    elif funding <= -0.0005:
        p["funding"] = 100
    elif funding <= -0.0002:
        p["funding"] = 75
    elif funding < 0:
        p["funding"] = 50
    elif funding <= 0.0003:
        p["funding"] = 20
    else:
        p["funding"] = 5

    if oi_over_fdv is None:
        p["lev"] = None
    elif oi_over_fdv >= 0.5:
        p["lev"] = 100
    elif oi_over_fdv >= 0.3:
        p["lev"] = 70
    elif oi_over_fdv >= 0.15:
        p["lev"] = 40
    else:
        p["lev"] = 10

    num = den = 0.0
    for k, w in SCORE_W.items():
        v = p.get(k)
        if v is None:
            continue
        num += v * w
        den += w
    return (round(num / den) if den else None), p


def fetch_oi(symbol):
    try:
        data = http_get_json(
            FAPI_HOST,
            "/futures/data/openInterestHist",
            {"symbol": symbol, "period": "5m", "limit": 25},
        )
        if not isinstance(data, list) or not data:
            return symbol, None, None, None, None, "empty response"
        points = sorted(
            (p for p in data if isinstance(p, dict)),
            key=lambda p: to_float(p.get("timestamp")) or 0.0,
        )
        if not points:
            return symbol, None, None, None, None, "empty response"
        last = points[-1]
        oi_usd = to_float(last.get("sumOpenInterestValue"))
        if oi_usd is None:
            return symbol, None, last.get("timestamp"), None, None, "no sumOpenInterestValue"
        oi_chg_1h = None
        if len(points) >= 13:
            base_val = to_float(points[-13].get("sumOpenInterestValue"))
        elif len(points) >= 2:
            base_val = to_float(points[0].get("sumOpenInterestValue"))
        else:
            base_val = None
        if base_val:
            oi_chg_1h = (oi_usd - base_val) / base_val
        oi_chg_24h = None
        if len(points) >= 2:
            first_val = to_float(points[0].get("sumOpenInterestValue"))
            if first_val:
                oi_chg_24h = (oi_usd - first_val) / first_val
        return symbol, oi_usd, last.get("timestamp"), oi_chg_1h, oi_chg_24h, None
    except FetchError as exc:
        return symbol, None, None, None, None, str(exc)
    except Exception as exc:
        return symbol, None, None, None, None, f"{type(exc).__name__}: {exc}"


def fetch_oi_and_vol(symbol):
    symbol, oi_usd, oi_ts, oi_chg_1h, oi_chg_24h, err = fetch_oi(symbol)
    vol_x = None
    try:
        vol_x = fetch_vol_x(symbol)
    except Exception:
        vol_x = None
    return symbol, oi_usd, oi_ts, oi_chg_1h, oi_chg_24h, vol_x, err


def fetch_all_oi(symbols):
    results = {}
    total = len(symbols)
    with ThreadPoolExecutor(max_workers=10) as pool:
        for done, (symbol, oi_usd, oi_ts, oi_chg_1h, oi_chg_24h, vol_x, err) in enumerate(pool.map(fetch_oi_and_vol, symbols), 1):
            results[symbol] = (oi_usd, oi_ts, oi_chg_1h, oi_chg_24h, vol_x, err)
            if done % 100 == 0 or done == total:
                failed = sum(1 for value in results.values() if value[5])
                print(f"[oi] {done}/{total} done, failed={failed}", flush=True)
    return results


def load_cg_cache():
    try:
        with open(CG_CACHE, "r", encoding="utf-8") as fh:
            cache = json.load(fh)
        if isinstance(cache, dict) and isinstance(cache.get("map"), dict):
            return cache
    except (OSError, ValueError):
        pass
    return None


def fetch_cg_map():
    cg_map = {}
    for page in range(1, CG_PAGES + 1):
        if page > 1:
            time.sleep(2)
        data = http_get_json(
            CG_HOST,
            "/api/v3/coins/markets",
            {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": page},
        )
        if not isinstance(data, list):
            raise FetchError(f"coingecko page {page}: unexpected payload")
        for coin in data:
            sym = (coin.get("symbol") or "").upper()
            if not sym:
                continue
            mcap = to_float(coin.get("market_cap"))
            prev = cg_map.get(sym)
            if prev is not None and (prev["mcap"] or 0) >= (mcap or 0):
                continue
            cg_map[sym] = {
                "mcap": mcap,
                "fdv": to_float(coin.get("fully_diluted_valuation")),
                "name": coin.get("name"),
                "id": coin.get("id"),
            }
        print(f"[cg] page {page}: {len(data)} coins, map={len(cg_map)}", flush=True)
        if len(data) < 250:
            break
    return cg_map


def get_cg_map():
    cache = load_cg_cache()
    now = time.time()
    if cache and now - float(cache.get("ts", 0)) < CG_TTL:
        print(f"[cg] using cache ({len(cache['map'])} symbols)", flush=True)
        return cache["map"], True, False
    try:
        cg_map = fetch_cg_map()
        if not cg_map:
            raise FetchError("coingecko returned no coins")
    except Exception as exc:
        if cache:
            print(f"[cg] fetch failed ({exc}); using stale cache", flush=True)
            return cache["map"], True, True
        print(f"[cg] fetch failed ({exc}); no cache available", flush=True)
        return {}, False, False
    tmp = CG_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"ts": int(now), "map": cg_map}, fh, ensure_ascii=False)
    os.replace(tmp, CG_CACHE)
    return cg_map, False, False


def match_symbol(base, cg_map):
    if base in cg_map:
        return cg_map[base], "exact"
    stripped = re.sub(r"^[0-9]+", "", base)
    if stripped and stripped != base:
        if stripped in cg_map:
            return cg_map[stripped], "stripped"
        unit = re.sub(r"^[MK]", "", stripped)
        if unit and unit != stripped and unit in cg_map:
            return cg_map[unit], "stripped"
    return None, "missing"


def ratio(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def sort_key(row):
    oi_fdv = row["oi_over_fdv"]
    vol_mcap = row["vol_over_mcap"]
    return (
        oi_fdv is None,
        -(oi_fdv or 0.0),
        vol_mcap is None,
        -(vol_mcap or 0.0),
    )


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    tickers = fetch_tickers()
    crypto = fetch_crypto_symbols()
    funding = fetch_funding()
    tickers = {sym: row for sym, row in tickers.items() if sym in crypto}
    crypto = {sym: sub for sym, sub in crypto.items() if sym in tickers}
    print("[filter] 加密永续合约 %d 个（已剔除股票/指数/商品等 TradFi 合约）" % len(tickers))
    symbols = sorted(tickers)
    print(f"[tickers] {len(symbols)} USDT perpetual symbols", flush=True)

    oi_results = fetch_all_oi(symbols)
    cg_map, cg_cached, cg_stale = get_cg_map()

    rows = []
    missing = []
    for symbol in symbols:
        tick = tickers[symbol]
        base = symbol[: -len("USDT")]
        oi_usd, oi_ts, oi_chg_1h, oi_chg_24h, vol_x_1h, oi_error = oi_results.get(symbol, (None, None, None, None, None, "not fetched"))
        if oi_usd is None:
            missing.append(symbol)
        info, match_kind = match_symbol(base, cg_map)
        mcap = info.get("mcap") if info else None
        fdv = info.get("fdv") if info else None
        vol = tick["vol24h_usd"]
        oi_over_fdv = ratio(oi_usd, fdv)
        score, score_parts = score_row(vol_x_1h, oi_chg_1h, funding.get(symbol), oi_over_fdv)
        rows.append({
            "symbol": symbol,
            "base": base,
            "name": info.get("name") if info else None,
            "price": tick["price"],
            "change_pct": tick["change_pct"],
            "oi_usd": oi_usd,
            "vol24h_usd": vol,
            "mcap_usd": mcap,
            "fdv_usd": fdv,
            "oi_over_fdv": oi_over_fdv,
            "vol_over_mcap": ratio(vol, mcap),
            "oi_gt_fdv": (oi_usd > fdv) if (oi_usd is not None and fdv) else None,
            "match_kind": match_kind,
            "oi_error": oi_error,
            "funding_rate": funding.get(symbol),
            "oi_chg_1h": oi_chg_1h,
            "oi_chg_24h": oi_chg_24h,
            "vol_x_1h": vol_x_1h,
            "score": score,
            "score_parts": score_parts,
            "oi_ts": int(oi_ts) if oi_ts is not None else None,
            "subtype": crypto.get(symbol),
            "asset_class": "crypto",
        })

    rows.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0), row["oi_over_fdv"] is None, -(row["oi_over_fdv"] or 0.0)))

    counts = {
        "symbols": len(rows),
        "funding_ok": len(funding),
        "oi_ok": sum(1 for row in rows if row["oi_usd"] is not None),
        "oi_failed": sum(1 for row in rows if row["oi_usd"] is None),
        "matched": sum(1 for row in rows if row["match_kind"] != "missing"),
        "unmatched": sum(1 for row in rows if row["match_kind"] == "missing"),
        "oi_gt_fdv": sum(1 for row in rows if row["oi_gt_fdv"] is True),
        "vol_over_mcap_gt1": sum(
            1 for row in rows if row["vol_over_mcap"] is not None and row["vol_over_mcap"] > 1
        ),
        "vol_x_ok": sum(1 for row in rows if row["vol_x_1h"] is not None),
        "scored": sum(1 for row in rows if row["score"] is not None),
        "crypto_symbols": len(tickers),
    }

    output = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {
            "tickers": "binance fapi/v1/ticker/24hr",
            "oi": "binance futures/data/openInterestHist",
            "supply": "coingecko coins/markets",
            "cg_cached": cg_cached,
            "cg_stale": cg_stale,
        },
        "counts": counts,
        "rows": rows,
        "missing": missing,
    }
    tmp = OUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT_FILE)

    print(
        "[done] " + " ".join(f"{key}={value}" for key, value in counts.items())
        + f" cg_cached={cg_cached} cg_stale={cg_stale} -> {OUT_FILE}",
        flush=True,
    )


if __name__ == "__main__":
    main()
