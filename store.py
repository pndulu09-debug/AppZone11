"""
Order storage.

- On Vercel: Upstash Redis (REST API) is used, so orders are stored permanently.
  Env vars (created automatically when you connect Upstash from Vercel Storage):
      UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN
      or  KV_REST_API_URL / KV_REST_API_TOKEN
- Termux / local: an orders.json file is used.
"""
import json
import os
import threading
import time

import requests

FAIL_WINDOW = 300   # login lock time (seconds)


class StoreError(Exception):
    pass


def _norm(order):
    if order and order.get("status") == "paid":     # old name
        order["status"] = "processing"
    return order


# ------------------------------------------------------------------ Redis
class RedisStore:
    kind = "redis"

    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": "Bearer " + token}

    def _cmd(self, *args):
        try:
            r = requests.post(self.url, headers=self.headers, json=list(args), timeout=8)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise StoreError(str(e))
        if r.status_code != 200 or "error" in data:
            raise StoreError(data.get("error", "redis error"))
        return data.get("result")

    def _pipe(self, cmds):
        try:
            r = requests.post(self.url + "/pipeline", headers=self.headers, json=cmds, timeout=8)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise StoreError(str(e))
        if r.status_code != 200 or not isinstance(data, list):
            raise StoreError("redis pipeline error")
        out = []
        for item in data:
            if "error" in item:
                raise StoreError(item["error"])
            out.append(item.get("result"))
        return out

    def ping(self):
        return self._cmd("PING") == "PONG"

    def save(self, order):
        self._pipe([
            ["HSET", "ff:orders", order["order_id"], json.dumps(order)],
            ["SADD", "ff:uid:" + order["uid"], order["order_id"]],
        ])

    def get(self, order_id):
        raw = self._cmd("HGET", "ff:orders", order_id)
        return _norm(json.loads(raw)) if raw else None

    def all(self):
        res = self._cmd("HGETALL", "ff:orders") or []
        if isinstance(res, dict):
            vals = list(res.values())
        else:
            vals = res[1::2]
        return [_norm(json.loads(v)) for v in vals]

    def by_uid(self, uid):
        ids = self._cmd("SMEMBERS", "ff:uid:" + uid) or []
        if not ids:
            return []
        rows = self._cmd("HMGET", "ff:orders", *ids) or []
        return [_norm(json.loads(v)) for v in rows if v]

    def claim_utr(self, utr, order_id):
        if self._cmd("HSETNX", "ff:utrs", utr, order_id) == 1:
            return True
        return self._cmd("HGET", "ff:utrs", utr) == order_id


    # ---- generic key/value helpers (coins, referrals, ads, locks) ----
    def kv_get(self, key):
        return self._cmd("GET", key)

    def kv_set(self, key, val, ttl=None):
        if ttl:
            self._cmd("SET", key, str(val), "EX", int(ttl))
        else:
            self._cmd("SET", key, str(val))

    def kv_setnx(self, key, val, ttl=None):
        args = ["SET", key, str(val), "NX"] + (["EX", int(ttl)] if ttl else [])
        return self._cmd(*args) == "OK"

    def kv_del(self, key):
        self._cmd("DEL", key)

    def kv_incr(self, key, n=1, ttl=None):
        res = int(self._cmd("INCRBY", key, int(n)))
        if ttl and res == n:
            self._cmd("EXPIRE", key, int(ttl))
        return res

    def kv_ttl(self, key):
        return max(int(self._cmd("TTL", key) or 0), 0)

    # free-like locks
    def free_claim(self, key, ttl):
        if self.kv_setnx("ff:free:" + key, 1, ttl):
            return True, 0
        return False, self.kv_ttl("ff:free:" + key)

    def free_release(self, key):
        self.kv_del("ff:free:" + key)

    # login lock
    def fail_get(self, ip):
        cnt, ttl = self._pipe([["GET", "ff:fail:" + ip], ["TTL", "ff:fail:" + ip]])
        return int(cnt or 0), max(int(ttl or 0), 0)

    def fail_add(self, ip):
        self._pipe([["INCR", "ff:fail:" + ip], ["EXPIRE", "ff:fail:" + ip, FAIL_WINDOW]])

    def fail_clear(self, ip):
        self._cmd("DEL", "ff:fail:" + ip)


# ------------------------------------------------------------------ File
class FileStore:
    kind = "file"

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.orders = {}
        self.fails = {}
        self.kv = {}
        try:
            with open(path, encoding="utf-8") as f:
                self.orders = {k: _norm(v) for k, v in json.load(f).items()}
        except Exception:
            self.orders = {}

    def ping(self):
        return True

    def _flush(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.orders, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def save(self, order):
        with self.lock:
            self.orders[order["order_id"]] = order
            self._flush()

    def get(self, order_id):
        return self.orders.get(order_id)

    def all(self):
        return list(self.orders.values())

    def by_uid(self, uid):
        return [o for o in self.orders.values() if o["uid"] == uid]

    def claim_utr(self, utr, order_id):
        with self.lock:
            for o in self.orders.values():
                if o.get("utr") == utr and o["order_id"] != order_id:
                    return False
            return True


    # ---- generic key/value helpers (coins, referrals, ads, locks) ----
    def _kv_live(self, key):
        v = self.kv.get(key)
        if v and v[1] and v[1] < time.time():
            self.kv.pop(key, None)
            return None
        return v

    def kv_get(self, key):
        v = self._kv_live(key)
        return v[0] if v else None

    def kv_set(self, key, val, ttl=None):
        with self.lock:
            self.kv[key] = (str(val), time.time() + ttl if ttl else 0)

    def kv_setnx(self, key, val, ttl=None):
        with self.lock:
            if self._kv_live(key):
                return False
            self.kv[key] = (str(val), time.time() + ttl if ttl else 0)
            return True

    def kv_del(self, key):
        with self.lock:
            self.kv.pop(key, None)

    def kv_incr(self, key, n=1, ttl=None):
        with self.lock:
            v = self._kv_live(key)
            cur = int(v[0]) if v else 0
            exp = v[1] if v else (time.time() + ttl if ttl else 0)
            self.kv[key] = (str(cur + n), exp)
            return cur + n

    def kv_ttl(self, key):
        v = self._kv_live(key)
        return max(int(v[1] - time.time()), 0) if v and v[1] else 0

    def free_claim(self, key, ttl):
        if self.kv_setnx("ff:free:" + key, 1, ttl):
            return True, 0
        return False, self.kv_ttl("ff:free:" + key)

    def free_release(self, key):
        self.kv_del("ff:free:" + key)

    def fail_get(self, ip):
        cnt, until = self.fails.get(ip, (0, 0))
        if until and until < time.time():
            self.fails.pop(ip, None)
            return 0, 0
        return cnt, max(int(until - time.time()), 0)

    def fail_add(self, ip):
        cnt, _ = self.fails.get(ip, (0, 0))
        self.fails[ip] = (cnt + 1, time.time() + FAIL_WINDOW)

    def fail_clear(self, ip):
        self.fails.pop(ip, None)


def _find_redis_env():
    """Vercel/Upstash env names can differ (custom prefixes too), so check all of them."""
    env = os.environ
    for url_suffix, tok_suffix in (("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"),
                                   ("KV_REST_API_URL", "KV_REST_API_TOKEN")):
        for key, val in env.items():
            if key.endswith(url_suffix) and val:
                prefix = key[: -len(url_suffix)]
                tok = env.get(prefix + tok_suffix)
                if tok:
                    return val, tok
    return None


def make_store(base_dir):
    found = _find_redis_env()
    if found:
        return RedisStore(*found)
    return FileStore(os.path.join(base_dir, "orders.json"))
