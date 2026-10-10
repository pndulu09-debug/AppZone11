"""
Free Fire UID validator (INDIA server only).

Fetches player info from the game server (client.ind.freefiremobile.com).
Only India server UIDs are found - UIDs from other servers return "not_found".

Required: a valid JWT token (from a guest account), set as an env var:
    export FF_JWT_TOKEN="eyJ..."
The token expires after a few hours; set a new one when it does.
Optional: FF_RELEASE_VERSION (changes with game updates, e.g. OB52)
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
      ok            -> UID is real (India server), returned with nickname + likes
      not_found     -> UID not found on the India server
      invalid       -> wrong format
      unconfigured  -> FF_JWT_TOKEN not set (only the format was checked)
      error         -> network/token problem
    """
    uid = str(uid).strip()
    if not valid_format(uid):
        return {"status": "invalid", "message": "UID must be 7-12 digits."}

    token = os.environ.get("FF_JWT_TOKEN", "").strip()
    if not token:
        return {"status": "unconfigured",
                "message": "UID verification is not configured on the server (only the format was checked)."}

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
        return {"status": "error", "message": "Could not connect to the Free Fire server. Please try again in a moment."}

    if r.status_code in (401, 403):
        return {"status": "error", "message": "The verification token has expired (the admin needs to set a new one)."}
    if r.status_code != 200 or not r.content:
        return {"status": "not_found", "message": "This UID was not found on the India server."}

    try:
        info = like_count_pb2.Info()
        info.ParseFromString(r.content)
        acc = info.AccountInfo
    except Exception:
        return {"status": "not_found", "message": "This UID was not found on the India server."}

    if acc.UID != int(uid) or not acc.PlayerNickname:
        return {"status": "not_found", "message": "This UID was not found on the India server."}

    result = {"status": "ok", "uid": uid, "nickname": acc.PlayerNickname,
              "likes": acc.Likes, "server": "IND"}
    _cache[uid] = (time.time(), result)
    return result
