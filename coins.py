"""
Coins, referral and ad-reward system (Blueprint).

Flow:
  * The Android app sends a hashed device id in the X-Device header.
  * Watch ad  -> /api/coins/ad/start returns a vplink short link. The link's destination is
                 /ad/done/<one-time-token>; after the viewer finishes the ad page, that URL credits the coins.
  * Referral  -> a user enters a friend's code once; both users get coins.
  * Redeem    -> coins are spent to create a "1 Day / 220 likes" order that the admin fulfils.
Everything is stored in the same store as orders (Upstash Redis on Vercel).
"""
import hashlib
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import Blueprint, jsonify, render_template, request, Response

from ff_validator import check_uid

bp = Blueprint("coins", __name__)
store = None
_available = lambda: True
_one_day = {}

DAY = 24 * 60 * 60
AD_REWARD = int(os.environ.get("AD_REWARD", 10))          # coins per completed ad
AD_DAILY_LIMIT = int(os.environ.get("AD_DAILY_LIMIT", 10))  # ads per device per 24h
AD_MIN_SECONDS = int(os.environ.get("AD_MIN_SECONDS", 20))  # fastest a real ad can be completed
REFER_REWARD = int(os.environ.get("REFER_REWARD", 50))     # coins for the referrer
REFER_BONUS = int(os.environ.get("REFER_BONUS", 25))       # coins for the new user
REFER_WINDOW = 3 * DAY                                    # code must be used within 3 days of install
REDEEM_COST = int(os.environ.get("REDEEM_COST", 100))      # coins for one redeem
REDEEM_LIKES = 220

VPLINK_API = os.environ.get("VPLINK_API", "https://vplink.in/api")
VPLINK_KEY = os.environ.get("VPLINK_API_KEY", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://duluappzoon.vercel.app").rstrip("/")


def init(app, store_obj, is_vercel, one_day_pkg):
    global store, _available, _one_day
    store = store_obj
    _one_day = one_day_pkg
    _available = lambda: not (is_vercel and store.kind != "redis")
    app.register_blueprint(bp)


def _config():
    return {"ads_limit": AD_DAILY_LIMIT, "ad_reward": AD_REWARD, "redeem_cost": REDEEM_COST,
            "redeem_likes": REDEEM_LIKES, "refer_reward": REFER_REWARD, "refer_bonus": REFER_BONUS}


def _err(msg, code=400):
    return jsonify({"ok": False, "message": msg}), code


def _dev():
    d = request.headers.get("X-Device", "").strip().lower()
    return d if re.fullmatch(r"[0-9a-f]{32}", d) else None


def _ref_code(dev):
    return hashlib.sha256(("ref|" + dev).encode()).hexdigest()[:8].upper()


def _ensure(dev):
    """Create the user on first sight and return their referral code."""
    code = _ref_code(dev)
    if store.kv_setnx("u:" + dev, int(time.time())):
        store.kv_set("refcode:" + code, dev)
    return code


def _bal(dev):
    return int(store.kv_get("coins:" + dev) or 0)


def _page(ok, title, msg):
    color = "#4cd964" if ok else "#ff6565"
    html = ("<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'><title>Dulu Store</title></head>"
            "<body style='margin:0;background:#0a0a0c;color:#fff;font-family:Arial,sans-serif;display:flex;"
            "align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:20px'>"
            f"<div><div style='font-size:64px'>{'✅' if ok else '⚠️'}</div>"
            f"<h2 style='color:{color};margin:12px 0'>{title}</h2>"
            f"<p style='color:#aaa;line-height:1.6'>{msg}</p></div></body></html>")
    return Response(html, mimetype="text/html")


# ---------------------------------------------------------------- user
@bp.route("/api/coins/me")
def coins_me():
    dev = _dev()
    if not dev:
        return _err("Device id is missing.")
    if not _available():
        return _err("Coins are temporarily unavailable.", 503)
    code = _ensure(dev)
    return jsonify({"ok": True, "coins": _bal(dev), "ref_code": code,
                    "referred": bool(store.kv_get("refd:" + dev)),
                    "referrals": int(store.kv_get("refcount:" + dev) or 0),
                    "ads_today": int(store.kv_get("ad:cnt:" + dev) or 0), **_config()})


# ---------------------------------------------------------------- ads (vplink)
def _shorten(dest):
    try:
        r = requests.get(VPLINK_API, params={"api": VPLINK_KEY, "url": dest}, timeout=15)
        try:
            j = r.json()
            url = j.get("shortenedUrl") or j.get("short_url") or j.get("url")
        except ValueError:
            url = r.text.strip()
        return url if isinstance(url, str) and url.startswith("http") else None
    except requests.RequestException:
        return None


@bp.route("/api/coins/ad/start", methods=["POST"])
def ad_start():
    dev = _dev()
    if not dev:
        return _err("Device id is missing.")
    if not _available() or not VPLINK_KEY:
        return _err("Ads are not available right now. Please try again later.", 503)
    _ensure(dev)
    if int(store.kv_get("ad:cnt:" + dev) or 0) >= AD_DAILY_LIMIT:
        return _err("Daily ad limit reached. Come back tomorrow.", 429)
    token = secrets.token_hex(12)
    short = _shorten(f"{PUBLIC_URL}/ad/done/{token}")
    if not short:
        return _err("Could not load the ad. Please try again.", 502)
    store.kv_set("adtok:" + token, f"{dev}:{int(time.time())}", 1800)
    return jsonify({"ok": True, "url": short})


@bp.route("/ad/done/<token>")
def ad_done(token):
    if not re.fullmatch(r"[0-9a-f]{24}", token):
        return _page(False, "Invalid link", "This reward link is not valid.")
    raw = store.kv_get("adtok:" + token)
    if not raw:
        return _page(False, "Link expired", "This reward link has expired or was already used.")
    dev, ts = raw.split(":")
    if time.time() - int(ts) < AD_MIN_SECONDS:
        return _page(False, "Please finish the ad", "Complete the ad page first. You will be redirected here "
                            "automatically, then reload this page.")
    if not store.kv_setnx("adused:" + token, 1, 3600):
        return _page(False, "Already claimed", "This reward was already claimed.")
    store.kv_del("adtok:" + token)
    if store.kv_incr("ad:cnt:" + dev, 1, DAY) > AD_DAILY_LIMIT:
        return _page(False, "Daily limit reached", "You have reached the daily ad limit. Come back tomorrow.")
    store.kv_incr("coins:" + dev, AD_REWARD)
    return _page(True, f"+{AD_REWARD} coins added", "Go back to the Dulu Store app to see your balance.")


# ---------------------------------------------------------------- referral
@bp.route("/api/coins/refer", methods=["POST"])
def refer():
    dev = _dev()
    if not dev:
        return _err("Device id is missing.")
    if not _available():
        return _err("Coins are temporarily unavailable.", 503)
    _ensure(dev)
    code = str((request.get_json(silent=True) or {}).get("code", "")).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{8}", code):
        return _err("Please enter a valid referral code.")
    owner = store.kv_get("refcode:" + code)
    if not owner:
        return _err("Referral code not found.", 404)
    if owner == dev:
        return _err("You cannot use your own referral code.")
    created = int(store.kv_get("u:" + dev) or 0)
    if time.time() - created > REFER_WINDOW:
        return _err("Referral codes can only be used within 3 days of installing the app.")
    if not store.kv_setnx("refd:" + dev, owner):
        return _err("You have already used a referral code.")
    store.kv_incr("coins:" + owner, REFER_REWARD)
    store.kv_incr("refcount:" + owner, 1)
    bal = store.kv_incr("coins:" + dev, REFER_BONUS)
    return jsonify({"ok": True, "coins": bal, "message": f"Referral applied! You received {REFER_BONUS} coins."})


# ---------------------------------------------------------------- redeem
@bp.route("/api/coins/redeem", methods=["POST"])
def redeem():
    dev = _dev()
    if not dev:
        return _err("Device id is missing.")
    if not _available():
        return _err("Redeeming is temporarily unavailable.", 503)
    _ensure(dev)
    uid = str((request.get_json(silent=True) or {}).get("uid", "")).strip()
    if not re.fullmatch(r"\d{7,12}", uid):
        return _err("Please enter a valid UID (7-12 digits).")
    res = check_uid(uid)
    if res["status"] not in ("ok", "unconfigured"):
        return _err(res["message"], 400 if res["status"] in ("invalid", "not_found") else 503)
    if _bal(dev) < REDEEM_COST:
        return _err(f"You need {REDEEM_COST} coins to redeem {REDEEM_LIKES} likes.")
    if not store.kv_setnx("redeem:uid:" + uid, 1, DAY):
        return _err("This UID already redeemed likes today. Please try again tomorrow.", 429)
    new = store.kv_incr("coins:" + dev, -REDEEM_COST)
    if new < 0:  # lost a race with another request
        store.kv_incr("coins:" + dev, REDEEM_COST)
        store.kv_del("redeem:uid:" + uid)
        return _err(f"You need {REDEEM_COST} coins to redeem {REDEEM_LIKES} likes.")
    order = {
        "order_id": "FF" + uuid.uuid4().hex[:10].upper(), "uid": uid, "nickname": res.get("nickname"),
        "verified_uid": res["status"] == "ok", "package_id": _one_day["id"],
        "label": _one_day["label"] + " (Coins)", "days": 1, "likes_per_day": _one_day["per_day"],
        "likes": _one_day["likes"], "amount": 0, "paid_with": "coins", "coins": REDEEM_COST,
        "utr": None, "status": "processing",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    try:
        store.save(order)
    except Exception:
        store.kv_incr("coins:" + dev, REDEEM_COST)   # refund if saving failed
        store.kv_del("redeem:uid:" + uid)
        raise
    return jsonify({"ok": True, "coins": new, "order_id": order["order_id"],
                    "message": f"Order placed! {REDEEM_LIKES} likes will be delivered to UID {uid} within 5-10 minutes."})


# ---------------------------------------------------------------- privacy policy
@bp.route("/privacy")
@bp.route("/privacy-policy")
def privacy():
    return render_template("privacy.html")
