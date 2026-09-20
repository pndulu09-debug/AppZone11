import os
import re
import json
import hmac
import time
from urllib.parse import quote
from jinja2 import ChoiceLoader, FileSystemLoader
from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
import uuid
from ff_validator import check_uid
from store import make_store, StoreError, FAIL_WINDOW
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))

TEMPLATES_DIR = os.path.join(BASE, "templates")
PUBLIC_DIR = os.path.join(BASE, "public")

# templates/ me HTML, public/ me css + icons. static_folder=None -> app.py kabhi public nahi hogi.
app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=None)
# Dono structure chalte hain: templates/ folder ya sab files root me (flat, GitHub upload wala).
app.jinja_loader = ChoiceLoader([FileSystemLoader(TEMPLATES_DIR), FileSystemLoader(BASE)])


# ---- Admin login (env variables se badal sakte ho) ----
ADMIN_USER = os.environ.get("ADMIN_USER", "admindulu")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "9864")


SECRET_KEY = os.environ.get("SECRET_KEY", "dulu")
# password bhi key me mila diya: SECRET_KEY chhota ho tab bhi cookie forge karna mushkil rahe
app.secret_key = SECRET_KEY + ":" + ADMIN_PASSWORD
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 12)


def _serve_public(sub, filename, mimetype=None):
    """public/<sub>/ me dhundo, nahi mila to root me (flat structure)."""
    for folder in (os.path.join(PUBLIC_DIR, sub), BASE):
        if os.path.isfile(os.path.join(folder, filename)):
            return send_from_directory(folder, filename, mimetype=mimetype)
    abort(404)


@app.route("/css/<path:filename>")
def css(filename):
    if "/" in filename or not filename.lower().endswith(".css"):
        abort(404)
    return _serve_public("css", filename, "text/css")


@app.route("/icons/<path:filename>")
def icons(filename):
    # sirf .png files allow hain
    if "/" in filename or not filename.lower().endswith(".png"):
        abort(404)
    return _serve_public("icons", filename)


@app.route("/favicon.ico")
def favicon():
    return "", 204


apps = [
    {
        "name": "CapCut Ultra",
        "version": "v28.0.0",
        "size": "278.2 MB",
        "icon": "/icons/capcut.png",
        "description": "Supported All phones, Without VPN usable.",
        "download": "https://vplink.in/aFaBR"
    },
    {
        "name": "YouTube Premium",
        "version": "v21.07.247",
        "size": "187.6 MB",
        "icon": "/icons/youtube.png",
        "description": "All Ads Remove+ Extra Future.",
        "download": "https://vplink.in/wUDAn1"
    },
    {
        "name": "Truecaller Premium",
        "version": "Latest",
        "size": "Varies",
        "icon": "/icons/truecaller.png",
        "description": "Truecaller caller ID and spam blocking app.",
        "download": "https://vplink.in/kaAj"
    },
    {
        "name": "Telegram Premium",
        "version": "Latest",
        "size": "Varies",
        "icon": "/icons/telegram.png",
        "description": "Telegram messenger.",
        "download": "https://vplink.in/OIBgy3"
    },
    {
        "name": "TeraBox Premium",
        "version": "v4.24.0",
        "size": "Varies",
        "icon": "/icons/terabox.png",
        "description": "TeraBox cloud storage app.",
        "download": "https://vplink.in/hc8sKV"
    },
    {
        "name": "Getmodpc Services",
        "version": "Latest",
        "size": "Varies",
        "icon": "/icons/getmodepc.png",
        "description": "Getmodpc Services APK.",
        "download": "https://www.mediafire.com/file/grlgisfuewmpjtr/Getmodpc+Services.apk/file"
    }
]


# ---------------- FREE FIRE LIKE SERVICE ----------------
UPI_ID = os.environ.get("UPI_ID", "9864945072@fam")
PAYEE_NAME = os.environ.get("PAYEE_NAME", "APPZONE")

LIKES_PER_DAY = 220
_PKG_DEFS = [
    ("1d", "1 Day", 1, 15, True),     # hot=True -> "HOT" badge
    ("7d", "7 Days", 7, 60, False),
    ("15d", "15 Days", 15, 100, False),
    ("30d", "30 Days", 30, 180, False),
]
FF_PACKAGES = [
    {"id": i, "label": label, "days": d, "per_day": LIKES_PER_DAY,
     "likes": LIKES_PER_DAY * d, "price": price, "hot": hot}
    for i, label, d, price, hot in _PKG_DEFS
]

# Orders: Vercel par Upstash Redis me, local par orders.json me (store.py dekho).
store = make_store(BASE)
IS_VERCEL = bool(os.environ.get("VERCEL"))


def storage_status():
    """(ok, text) - admin panel me dikhane ke liye."""
    if store.kind == "redis":
        try:
            store.ping()
            return True, "Redis connected"
        except StoreError as e:
            return False, "Redis connect nahi ho raha: " + str(e)[:80]
    if IS_VERCEL:
        return False, "Redis NOT connected - Vercel par orders gayab ho jayenge"
    return True, "Local file (orders.json)"


@app.errorhandler(StoreError)
def _store_down(e):
    if request.path.startswith("/api/") or request.path.startswith("/admin/order"):
        return jsonify({"ok": False, "message": "Server busy, thodi der baad try karo."}), 503
    return Response("Database error. Thodi der baad try karo.", 503)


def _upi_url(order):
    return ("upi://pay?pa=" + quote(UPI_ID, safe="@") + "&pn=" + quote(PAYEE_NAME) +
            "&am=" + str(order["amount"]) + "&cu=INR&tn=" + quote("Order " + order["order_id"]))


@app.route("/free-fire")
def free_fire():
    return render_template("freefire.html", packages=FF_PACKAGES, upi_id=UPI_ID)


@app.route("/api/free-fire/check-uid", methods=["POST"])
def ff_check_uid():
    data = request.get_json(silent=True) or {}
    res = check_uid(str(data.get("uid", "")))
    code = {"ok": 200, "unconfigured": 200, "invalid": 400, "not_found": 404}.get(res["status"], 503)
    return jsonify({"ok": res["status"] in ("ok", "unconfigured"), **res}), code


@app.route("/api/free-fire/order", methods=["POST"])
def create_ff_order():
    data = request.get_json(silent=True) or {}
    uid = str(data.get("uid", "")).strip()
    package_id = str(data.get("package_id", "")).strip()

    package = next((p for p in FF_PACKAGES if p["id"] == package_id), None)
    if not package:
        return jsonify({"ok": False, "message": "Invalid package."}), 400
    if IS_VERCEL and store.kind != "redis":
        # Bina database ke Vercel par order kho jate hain - isliye order lena band.
        return jsonify({"ok": False, "message": "Ordering temporarily unavailable. Please contact support."}), 503
    res = check_uid(uid)
    if res["status"] not in ("ok", "unconfigured"):
        code = 400 if res["status"] in ("invalid", "not_found") else 503
        return jsonify({"ok": False, "message": res["message"]}), code

    order_id = "FF" + uuid.uuid4().hex[:10].upper()
    order = {
        "order_id": order_id,
        "uid": uid,
        "nickname": res.get("nickname"),
        "verified_uid": res["status"] == "ok",
        "package_id": package_id,
        "label": package["label"],
        "days": package["days"],
        "likes_per_day": package["per_day"],
        "likes": package["likes"],
        "amount": package["price"],
        "utr": None,
        "status": "pending",   # pending -> submitted (UTR diya) -> processing (payment approve) -> processed / rejected
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    store.save(order)
    return jsonify({"ok": True, "order": order, "upi_url": _upi_url(order)})


@app.route("/api/free-fire/order/<order_id>")
def ff_order_status(order_id):
    order = store.get(order_id)
    if not order:
        return jsonify({"ok": False, "message": "Order not found."}), 404
    return jsonify({"ok": True, "order": {"order_id": order_id, "status": order["status"]}})


@app.route("/api/free-fire/history")
def ff_history():
    """Customer apna UID daal ke apne orders ka status dekhta hai (UTR/private data nahi dikhta)."""
    uid = request.args.get("uid", "").strip()
    if not re.fullmatch(r"\d{7,12}", uid):
        return jsonify({"ok": False, "message": "Sahi UID daalo (7-12 digits)."}), 400
    mine = [o for o in store.by_uid(uid) if o["status"] != "pending"]
    mine.sort(key=lambda o: o["created_at"], reverse=True)
    items = [{"order_id": o["order_id"], "label": o["label"], "likes": o["likes"],
              "amount": o["amount"], "status": o["status"], "created_at": o["created_at"]}
             for o in mine[:30]]
    return jsonify({"ok": True, "orders": items})


@app.route("/api/free-fire/order/<order_id>/qr.svg")
def ff_order_qr(order_id):
    order = store.get(order_id)
    if not order:
        abort(404)
    try:
        import io
        import qrcode
        import qrcode.image.svg
    except ImportError:
        abort(404)
    img = qrcode.make(_upi_url(order), image_factory=qrcode.image.svg.SvgPathImage, box_size=10)
    buf = io.BytesIO()
    img.save(buf)
    return Response(buf.getvalue(), mimetype="image/svg+xml")


@app.route("/api/free-fire/order/<order_id>/utr", methods=["POST"])
def ff_submit_utr(order_id):
    """Customer payment karke apna 12-digit UTR/Transaction ID bhejta hai. Admin check karke approve karta hai."""
    order = store.get(order_id)
    if not order:
        return jsonify({"ok": False, "message": "Order not found."}), 404
    data = request.get_json(silent=True) or {}
    utr = str(data.get("utr", "")).strip()
    if not re.fullmatch(r"\d{12}", utr):
        return jsonify({"ok": False, "message": "UTR / Transaction ID 12 digits ka hona chahiye."}), 400
    if order["status"] in ("processing", "processed", "rejected"):
        return jsonify({"ok": False, "message": "Ye order already process ho chuka hai."}), 400
    if not store.claim_utr(utr, order_id):
        return jsonify({"ok": False, "message": "Ye Transaction ID pehle hi use ho chuka hai."}), 400
    order["utr"] = utr
    order["status"] = "submitted"
    store.save(order)
    return jsonify({"ok": True, "status": "submitted"})


# ---------------- ADMIN (login + payment approve) ----------------
MAX_FAILS = 5


def _admin_ok():
    return session.get("admin") is True


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if _admin_ok():
        return redirect(url_for("admin"))
    error = None
    ip = request.remote_addr or "?"
    if request.method == "POST":
        fails, ttl = store.fail_get(ip)
        if fails >= MAX_FAILS:
            error = f"Too many wrong attempts. Try again in {ttl // 60 + 1} min."
        else:
            u = request.form.get("username", "")
            p = request.form.get("password", "")
            ok_u = hmac.compare_digest(u.encode(), ADMIN_USER.encode())
            ok_p = hmac.compare_digest(p.encode(), ADMIN_PASSWORD.encode())
            if ok_u and ok_p:
                store.fail_clear(ip)
                session.clear()
                session["admin"] = True
                session.permanent = True
                return redirect(url_for("admin"))
            store.fail_add(ip)
            if fails + 1 >= MAX_FAILS:
                error = "Too many wrong attempts. Locked for 5 minutes."
            else:
                error = "Wrong username or password."
    return render_template("admin_login.html", error=error), (401 if error else 200)


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
def admin():
    if not _admin_ok():
        return redirect(url_for("admin_login"))
    rank = {"submitted": 0, "processing": 1, "pending": 2, "processed": 3, "rejected": 4}
    all_orders = store.all()
    rows = sorted(all_orders, key=lambda o: (rank.get(o["status"], 9), o["created_at"]))
    count = lambda st: sum(1 for o in all_orders if o["status"] == st)
    stats = {"submitted": count("submitted"), "processing": count("processing"),
             "processed": count("processed"), "total": len(all_orders)}
    st_ok, st_text = storage_status()
    return render_template("admin.html", orders=rows, stats=stats, admin_user=ADMIN_USER,
                           st_ok=st_ok, st_text=st_text)


@app.route("/admin/order/<order_id>", methods=["POST"])
def admin_action(order_id):
    if not _admin_ok():
        return jsonify({"ok": False, "message": "Login required"}), 401
    order = store.get(order_id)
    data = request.get_json(silent=True)
    if not order or not data:
        return jsonify({"ok": False}), 400
    action = data.get("action")
    if action not in ("approve", "reject", "processed"):
        return jsonify({"ok": False}), 400
    order["status"] = {"approve": "processing", "reject": "rejected", "processed": "processed"}[action]
    store.save(order)
    return jsonify({"ok": True, "status": order["status"]})


@app.route("/api/payment/webhook", methods=["POST"])
def payment_webhook():
    """
    Payment-gateway webhook (baad me gateway lagane par).
    PAYMENT_WEBHOOK_SECRET set hona zaruri hai, warna koi bhi order paid kar sakta hai.
    Gateway ki signature verification yahan add karni hogi.
    """
    secret = os.environ.get("PAYMENT_WEBHOOK_SECRET", "")
    got = request.headers.get("X-Webhook-Secret", "")
    if not secret or not hmac.compare_digest(got, secret):
        return jsonify({"ok": False, "message": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    order = store.get(str(data.get("order_id", "")).strip())
    if not order:
        return jsonify({"ok": False, "message": "Unknown order."}), 404
    if str(data.get("status", "")).lower() == "paid" and order["status"] in ("pending", "submitted"):
        order["status"] = "processing"
        store.save(order)
    return jsonify({"ok": True, "status": order["status"]})


@app.route("/")
def home():
    return render_template("index.html", apps=apps)


@app.route("/app/<int:app_id>")
def app_details(app_id):
    if app_id < 0 or app_id >= len(apps):
        return render_template("404.html"), 404

    others = [
        {"id": i, **a}
        for i, a in enumerate(apps)
        if i != app_id
    ][:4]

    return render_template(
        "details.html",
        app=apps[app_id],
        others=others,
    )


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=os.environ.get("FLASK_DEBUG") == "1")
