"""
Free Fire UID validator (INDIA server only).

Game server (client.ind.freefiremobile.com) se player info nikalta hai.
Sirf India server ke UID milenge - dusre server ka UID "not_found" aayega.

Zaruri: ek valid JWT token chahiye (guest account ka), env var me do:
    export FF_JWT_TOKEN="eyJ..."
Token kuch ghante me expire hota hai, expire hone par naya daalna padega.
Optional: FF_RELEASE_VERSION (game update ke saath badalta hai, e.g. OB52)
"""
import os
import re
import time
import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import uid_generator_pb2
import like_count_pb2

URL = "https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
AES_KEY = b"Yg&tc%DEuh6%Zc^8"
AES_IV = b"6oyZDr22E3ychjM%"

_cache = {}          # uid -> (time, result)
CACHE_SECONDS = 300


def _encrypt(data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    enc = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV)).encryptor()
    return enc.update(padded) + enc.finalize()


def valid_format(uid: str) -> bool:
    return bool(re.fullmatch(r"\d{7,12}", uid))


def check_uid(uid: str) -> dict:
    """
    'status' values:
      ok            -> UID real hai (India server), nickname + likes ke saath
      not_found     -> UID India server par nahi mila
      invalid       -> format galat
      unconfigured  -> FF_JWT_TOKEN set nahi hai (sirf format check hua)
      error         -> network/token problem
    """
    uid = str(uid).strip()
    if not valid_format(uid):
        return {"status": "invalid", "message": "UID 7-12 digits ka hona chahiye."}

    token = os.environ.get("FF_JWT_TOKEN", "").strip()
    if not token:
        return {"status": "unconfigured",
                "message": "Server par UID verification set nahi hai (sirf format check hua)."}

    hit = _cache.get(uid)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]

    msg = uid_generator_pb2.uid_generator()
    msg.saturn_ = int(uid)
    msg.garena = 1
    payload = _encrypt(msg.SerializeToString())

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 13; Pixel 7 Build/TQ3A.230901.001)",
        "Content-Type": "application/octet-stream",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Expect": "100-continue",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "ReleaseVersion": os.environ.get("FF_RELEASE_VERSION", "OB52"),
    }

    try:
        r = requests.post(URL, data=payload, headers=headers, timeout=12)
    except requests.RequestException:
        return {"status": "error", "message": "Free Fire server se connect nahi ho paya. Thodi der baad try karo."}

    if r.status_code in (401, 403):
        return {"status": "error", "message": "Verification token expire ho gaya hai (admin ko naya token dalna hoga)."}
    if r.status_code != 200 or not r.content:
        return {"status": "not_found", "message": "Ye UID India server par nahi mila."}

    try:
        info = like_count_pb2.Info()
        info.ParseFromString(r.content)
        acc = info.AccountInfo
    except Exception:
        return {"status": "not_found", "message": "Ye UID India server par nahi mila."}

    if acc.UID != int(uid) or not acc.PlayerNickname:
        return {"status": "not_found", "message": "Ye UID India server par nahi mila."}

    result = {"status": "ok", "uid": uid, "nickname": acc.PlayerNickname,
              "likes": acc.Likes, "server": "IND"}
    _cache[uid] = (time.time(), result)
    return result
