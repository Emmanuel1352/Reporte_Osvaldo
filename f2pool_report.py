#!/usr/bin/env python3
"""Reporte diario de F2Pool a un grupo de WhatsApp (TextMeBot).

Secretos necesarios en GitHub:
  F2POOL_API_SECRET, F2POOL_USER,
  TEXTMEBOT_APIKEY, TEXTMEBOT_RECIPIENT
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Mexico_City")
F2POOL = "https://api.f2pool.com/v2/hash_rate/worker/list"
TEXTMEBOT = "https://api.textmebot.com/send.php"
UA = {"User-Agent": "f2pool-daily-report/1.0"}


def fetch_workers(secret, user, currency):
    body = json.dumps(
        {"mining_user_name": user, "currency": currency}
    ).encode()
    headers = dict(UA)
    headers["Content-Type"] = "application/json"
    headers["F2P-API-SECRET"] = secret
    req = urllib.request.Request(
        F2POOL, data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")[:300]
        raise RuntimeError("F2Pool HTTP %s: %s" % (e.code, txt))
    if data.get("code") not in (None, 0, "0", ""):
        raise RuntimeError(
            "F2Pool error: %s %s" % (data.get("code"), data.get("msg"))
        )
    return data.get("workers", []) or []


def fmt_hash(h):
    h = float(h or 0)
    units = ["H/s", "KH/s", "MH/s", "GH/s", "TH/s", "PH/s", "EH/s"]
    for unit in units:
        if abs(h) < 1000 or unit == units[-1]:
            return "%.2f %s" % (h, unit)
        h /= 1000


def fmt_ago(ts, now):
    if not ts:
        return "sin datos"
    secs = max(0, int(now.timestamp()) - int(ts))
    if secs < 3600:
        return "hace %d min" % (secs // 60)
    if secs < 86400:
        return "hace %d h" % (secs // 3600)
    return "hace %d d" % (secs // 86400)


def info(w):
    return w["hash_rate_info"]


def build_report(results, now):
    lines = []
    bad = any(
        w.get("status") != 0 for ws in results.values() for w in ws
    )
    icon = "⚠️" if bad else "✅"
    lines.append("%s *Reporte F2Pool* - %s" % (icon, now.strftime(
        "%d/%m/%Y %H:%M")))
    for currency, workers in results.items():
        on = [w for w in workers if w.get("status") == 0]
        off = [w for w in workers if w.get("status") == 1]
        exp = [w for w in workers if w.get("status") == 2]
        cur = sum(info(w).get("hash_rate", 0) for w in workers)
        h24 = sum(info(w).get("h24_hash_rate", 0) for w in workers)
        st = sum(info(w).get("h24_stale_hash_rate", 0) for w in workers)
        pct = (st / h24 * 100) if h24 else 0
        lines.append("")
        lines.append("*%s*" % currency.upper())
        lines.append(
            "Máquinas: %d | 🟢 %d | 🔴 %d | ⚪ %d"
            % (len(workers), len(on), len(off), len(exp))
        )
        lines.append("Hashrate actual: " + fmt_hash(cur))
        lines.append("Promedio 24h: " + fmt_hash(h24))
        lines.append("Rechazo 24h: %.2f%%" % pct)
        down = off + exp
        if down:
            lines.append("")
            lines.append("🔴 *Fuera de línea:*")
            down.sort(key=lambda w: w.get("last_share_at") or 0)
            for w in down[:15]:
                ago = fmt_ago(w.get("last_share_at"), now)
                lines.append(
                    "- %s (último share %s)" % (info(w).get("name"), ago)
                )
            if len(down) > 15:
                lines.append("... y %d más" % (len(down) - 15))
        if on and len(workers) <= 30:
            lines.append("")
            lines.append("🟢 *En línea (1h prom.):*")
            on.sort(key=lambda w: info(w).get("name", ""))
            for w in on[:30]:
                hr = fmt_hash(info(w).get("h1_hash_rate"))
                lines.append("- %s: %s" % (info(w).get("name"), hr))
    return lines


def chunk(lines, limit=1000):
    parts, cur = [], ""
    for ln in lines:
        if cur and len(cur) + len(ln) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + ln
    if cur:
        parts.append(cur)
    return parts


def send_textmebot(apikey, recipient, text):
    qs = urllib.parse.urlencode(
        {"recipient": recipient, "apikey": apikey, "text": text}
    )
    req = urllib.request.Request(TEXTMEBOT + "?" + qs, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")[:300]
        raise RuntimeError("TextMeBot HTTP %s: %s" % (e.code, txt))
    plain = " ".join(re.sub(r"<[^>]+>", " ", body).split())
    print("TextMeBot respondió: " + plain[:200])
    if re.search(r"error|invalid|not found|blocked|denied", plain, re.I):
        raise RuntimeError("TextMeBot rechazó el envío: " + plain[:200])


def send_all(apikey, recipient, lines):
    parts = chunk(lines)
    for i, part in enumerate(parts, 1):
        if len(parts) > 1:
            part = "(%d/%d)\n%s" % (i, len(parts), part)
        send_textmebot(apikey, recipient, part)
        if i < len(parts):
            time.sleep(4)


def main():
    env = {k: v.strip() for k, v in os.environ.items()}
    need = ["F2POOL_API_SECRET", "F2POOL_USER"]
    need += ["TEXTMEBOT_APIKEY", "TEXTMEBOT_RECIPIENT"]
    missing = [k for k in need if not env.get(k)]
    if missing:
        print("Faltan secretos: " + ", ".join(missing))
        return 2
    key = env["TEXTMEBOT_APIKEY"]
    dest = env["TEXTMEBOT_RECIPIENT"]
    now = datetime.now(TZ)
    coins = env.get("F2POOL_CURRENCIES", "bitcoin").split(",")
    coins = [c.strip() for c in coins if c.strip()]
    try:
        results = {
            c: fetch_workers(
                env["F2POOL_API_SECRET"], env["F2POOL_USER"], c
            )
            for c in coins
        }
        lines = build_report(results, now)
    except Exception as e:
        lines = ["❌ *Reporte F2Pool* - falló la consulta:", str(e)]
        print("\n".join(lines))
        try:
            send_all(key, dest, lines)
        except Exception as e2:
            print("No se pudo avisar: %s" % e2)
        return 1
    print("\n".join(lines))
    try:
        send_all(key, dest, lines)
    except Exception as e:
        print("Error al enviar: %s" % e)
        return 1
    print("Enviado por WhatsApp (TextMeBot).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
