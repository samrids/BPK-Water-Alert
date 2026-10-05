#!/usr/bin/env python3
"""
bpk_bank_alert.py — แจ้งเตือนระดับน้ำใกล้ล้นตลิ่ง สถานีบางปะกง (BPK001) ผ่าน Telegram

ข้อมูล: thaiwater.net (สสน.) — API waterlevel_load
เงื่อนไข:
  🟠 ใกล้ล้นตลิ่ง : ระดับน้ำต่ำกว่าตลิ่งน้อยกว่า NEAR_M (30 ซม.)
  🔴 ล้นตลิ่ง     : ระดับน้ำเท่ากับ/สูงกว่าตลิ่ง
  🟢 กลับสู่ปกติ  : ลดลงจนห่างตลิ่ง >= NEAR_M + HYSTERESIS_M
  ⚪ ข้อมูลไม่อัปเดต : สถานีไม่ส่งค่าใหม่เกิน STALE_H ชั่วโมง
แจ้งเฉพาะตอน "เปลี่ยนสถานะ" + แจ้งซ้ำเมื่อน้ำสูงขึ้นอีก REPEAT_STEP_M ระหว่างอยู่ในสถานะเตือน

ใช้งาน:
  pip install requests pillow
  copy .env.example .env  แล้วใส่ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ในไฟล์ .env
  python bpk_bank_alert.py --test     # ทดสอบส่ง Telegram + แสดงค่าปัจจุบัน
  python bpk_bank_alert.py --preview  # สร้างภาพตัวอย่างทุกสถานะในโฟลเดอร์ preview/ (ไม่ส่ง Telegram)
  python bpk_bank_alert.py --bot      # บอทรอรับ /status และปุ่ม "ดูสถานะล่าสุด" (รันค้างไว้ตลอด)
  python bpk_bank_alert.py            # เช็ก 1 รอบ (ตั้ง Task Scheduler / cron ทุก 10–15 นาที)
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent


def load_env(path=BASE_DIR / ".env"):
    """อ่านไฟล์ .env (KEY=VALUE) ข้างสคริปต์ — ไม่ต้องติดตั้ง python-dotenv
    ค่าใน environment ของระบบ (setx / export) มีลำดับสำคัญกว่าค่าในไฟล์"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        else:
            val = val.split(" #", 1)[0].strip()      # ตัด comment ท้ายบรรทัด
        os.environ.setdefault(key, val)


load_env()


def env_float(key, default):
    try:
        return float(os.getenv(key, default))
    except ValueError:
        print(f"ค่า {key} ใน .env ไม่ใช่ตัวเลข ใช้ค่าเริ่มต้น {default}", file=sys.stderr)
        return float(default)


# ---------------- ตั้งค่า (แก้ได้ใน .env) ----------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")         # คั่นด้วย , ได้ถ้าส่งหลายกลุ่ม
STATION_CODE = os.getenv("STATION_CODE", "BPK001")  # สถานีบางปะกง ต.ท่าสะอ้าน อ.บางปะกง
NEAR_M = env_float("NEAR_M", 0.30)                  # เตือนเมื่อห่างตลิ่ง < 30 ซม.
HYSTERESIS_M = env_float("HYSTERESIS_M", 0.05)      # ต้องลดอีก 5 ซม. ถึงนับว่ากลับปกติ
REPEAT_STEP_M = env_float("REPEAT_STEP_M", 0.10)    # อยู่ในสถานะเตือน น้ำขึ้นอีก 10 ซม. -> แจ้งซ้ำ
STALE_H = env_float("STALE_H", 2)                   # ไม่มีค่าใหม่เกินกี่ชั่วโมง -> แจ้งข้อมูลไม่อัปเดต
STATE_FILE = BASE_DIR / os.getenv("STATE_FILE", "bpk_alert_state.json")
ALERT_IMAGE = os.getenv("ALERT_IMAGE", "1").lower() not in ("0", "false", "no", "off")  # ส่งเป็นภาพการ์ด+กราฟ
GRAPH_HOURS = int(env_float("GRAPH_HOURS", 24))     # กราฟย้อนหลังกี่ชั่วโมง
ROAD_NAME = os.getenv("ROAD_NAME", "")              # ชื่อจุด/ถนนที่ต้องระวัง แสดงบนภาพ (ไม่ใส่ก็ได้)
API_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"
GRAPH_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_graph"
HEADERS = {"User-Agent": "Mozilla/5.0 (Alumet MIS water monitor)", "Accept": "application/json"}
WEB_URL = "https://www.thaiwater.net/water/wl"
# ----------------------------------------------------------


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_station():
    last_err = None
    for i in range(3):
        try:
            r = requests.get(API_URL, params={"timestamp": int(time.time() * 1000)},
                             headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
            rows = (data.get("waterlevel_data") or {}).get("data") or data.get("data") or []
            for raw in rows:
                st = raw.get("station") or {}
                if st.get("tele_station_oldcode") == STATION_CODE:
                    return raw
            raise RuntimeError(f"ไม่พบสถานี {STATION_CODE} ในข้อมูล")
        except Exception as e:
            last_err = e
            time.sleep(5 * (i + 1))
    raise last_err


def parse(raw):
    st = raw["station"]
    wl = num(raw.get("waterlevel_msl"))
    prev = num(raw.get("waterlevel_msl_previous"))
    bank = num(st.get("min_bank"))
    to_bank = num(raw.get("diff_wl_bank"))
    if to_bank is not None and "สูงกว่า" in (raw.get("diff_wl_bank_text") or ""):
        to_bank = -to_bank
    if to_bank is None and wl is not None and bank is not None:
        to_bank = round(bank - wl, 2)
    t = raw.get("waterlevel_datetime") or ""
    try:
        ts = datetime.strptime(t[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        ts = None
    return {"name": st["tele_station_name"].get("th", "บางปะกง"), "station_id": st.get("id"), "wl": wl, "prev": prev,
            "bank": bank, "to_bank": to_bank, "time": t, "ts": ts}


def fetch_history(station_id, hours=GRAPH_HOURS):
    """ระดับน้ำย้อนหลัง [(datetime, ม.รทก.)] — API รับเป็นวันที่ จึงตัดช่วงเวลาเองอีกที"""
    now = datetime.now()
    start = now - timedelta(hours=hours)
    r = requests.get(GRAPH_URL, params={"station_type": "tele_waterlevel", "station_id": station_id,
                                        "start_date": f"{start:%Y-%m-%d}", "end_date": f"{now:%Y-%m-%d}"},
                     headers=HEADERS, timeout=30)
    r.raise_for_status()
    out = []
    for p in (r.json().get("data") or {}).get("graph_data") or []:
        try:
            t = datetime.strptime(p["datetime"][:16], "%Y-%m-%d %H:%M")
        except (KeyError, TypeError, ValueError):
            continue
        if t >= start:
            out.append((t, num(p.get("value"))))
    return out


def classify(d, prev_status):
    tb = d["to_bank"]
    if tb is None:
        return prev_status or "normal"
    if tb <= 0:
        return "over"
    if tb < NEAR_M:
        return "near"
    # อยู่ระหว่างเกณฑ์กับเกณฑ์+hysteresis -> คงสถานะเดิม
    if prev_status in ("near", "over") and tb < NEAR_M + HYSTERESIS_M:
        return "near"
    return "normal"


def message(kind, d):
    trend = ""
    if d["wl"] is not None and d["prev"] is not None:
        diff = d["wl"] - d["prev"]
        trend = f" ({'⬆️ ขึ้น' if diff > 0 else '⬇️ ลง' if diff < 0 else '➡️ ทรงตัว'} {abs(diff)*100:.0f} ซม.)"
    head = {
        "near": "🟠 <b>เตือน: น้ำใกล้ล้นตลิ่ง</b>",
        "over": "🔴 <b>ด่วน: น้ำล้นตลิ่ง</b>",
        "normal": "🟢 <b>ระดับน้ำกลับสู่ปกติ</b>",
        "rising": "🟠 <b>น้ำยังสูงขึ้นต่อเนื่อง</b>",
        "receding": "🟠 <b>น้ำลดลงต่ำกว่าตลิ่งแล้ว (ยังเฝ้าระวัง)</b>",
        "stale": "⚪ <b>สถานีไม่อัปเดตข้อมูล</b>",
        "test": "🧪 <b>ทดสอบระบบแจ้งเตือน</b>",
        "status": "📍 <b>สถานะ ณ ตอนนี้</b>",
    }[kind]
    tb = d["to_bank"]
    tb_txt = "-" if tb is None else (f"เกินตลิ่ง {abs(tb)*100:.0f} ซม." if tb <= 0 else f"ต่ำกว่าตลิ่ง {tb*100:.0f} ซม.")
    return (f"{head}\n"
            f"สถานี{d['name']} ({STATION_CODE}) แม่น้ำบางปะกง\n"
            f"ระดับน้ำ: <b>{d['wl']:.2f} ม.รทก.</b>{trend}\n"
            f"ตลิ่ง: {d['bank']:.2f} ม.รทก. → <b>{tb_txt}</b>\n"
            f"เวลาวัด: {d['time']}\n"
            f'<a href="{WEB_URL}">thaiwater.net</a>')


LEVEL_OF = {"normal": "ok", "near": "risk", "over": "avoid"}


def card(kind, d, level, history=None):
    """ภาพการ์ด PNG — ถ้าสร้างไม่ได้ (ไม่มี Pillow/ฟอนต์ ฯลฯ) คืน None แล้วส่งเป็นข้อความแทน"""
    if not ALERT_IMAGE:
        return None
    try:
        from alert_card import render_card
        if history is None:
            try:
                history = fetch_history(d["station_id"])
            except Exception as e:
                print("ดึงกราฟย้อนหลังไม่สำเร็จ:", e, file=sys.stderr)
        return render_card(level, kind, d, station_code=STATION_CODE, near_m=NEAR_M,
                           road=ROAD_NAME, history=history, hours=GRAPH_HOURS)
    except Exception as e:
        print("สร้างภาพไม่สำเร็จ ส่งเป็นข้อความแทน:", e, file=sys.stderr)
        return None


def notify(kind, d, level):
    send(message(kind, d), card(kind, d, level))


STATUS_BUTTON = {"inline_keyboard": [[{"text": "🔄 ดูสถานะล่าสุด", "callback_data": "status"}]]}


def chats():
    return [c.strip() for c in CHAT_ID.split(",") if c.strip()]


def tg(method, timeout=20, **kw):
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", timeout=timeout, **kw)
    if not r.ok:
        print("Telegram error:", r.status_code, r.text, file=sys.stderr)
        r.raise_for_status()
    return r.json().get("result")


def send_to(chat, text, png=None, button=True):
    markup = STATUS_BUTTON if button else None
    if png:
        data = {"chat_id": chat, "caption": text, "parse_mode": "HTML"}
        if markup:
            data["reply_markup"] = json.dumps(markup)
        tg("sendPhoto", timeout=30, data=data, files={"photo": ("bpk_alert.png", png, "image/png")})
    else:
        body = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if markup:
            body["reply_markup"] = markup
        tg("sendMessage", json=body)


def send(text, png=None):
    if not BOT_TOKEN or not CHAT_ID:
        print(f"!! ยังไม่ได้ตั้ง TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (ตรวจไฟล์ {BASE_DIR / '.env'})\n" + text)
        return
    for chat in chats():
        send_to(chat, text, png)


# ---------------- โหมดบอท: ตอบ /status และปุ่ม "ดูสถานะล่าสุด" ----------------
STATUS_CACHE_SEC = 60       # กดถี่ ๆ ภายใน 60 วินาที ใช้ภาพเดิม ไม่ยิง thaiwater ซ้ำ
_status_cache = {"at": 0.0, "text": None, "png": None}


def current_status():
    """สถานะ ณ ตอนนี้ — ใช้สถานะที่ตัวเช็กบันทึกไว้เป็นฐาน ผลจะตรงกับการแจ้งเตือน (มี hysteresis เดียวกัน)"""
    if time.time() - _status_cache["at"] < STATUS_CACHE_SEC:
        return _status_cache["text"], _status_cache["png"]
    d = parse(fetch_station())
    try:
        prev = json.loads(STATE_FILE.read_text(encoding="utf-8") or "{}").get("status")
    except (OSError, ValueError):
        prev = None
    status = classify(d, prev)
    stale = d["ts"] is not None and (datetime.now() - d["ts"]).total_seconds() > STALE_H * 3600
    level = "stale" if stale else LEVEL_OF[status]
    text, png = message("status", d), card("status", d, level)
    _status_cache.update(at=time.time(), text=text, png=png)
    return text, png


def reply_status(chat):
    try:
        text, png = current_status()
    except Exception as e:
        print("ดึงสถานะไม่สำเร็จ:", e, file=sys.stderr)
        send_to(chat, "⚠️ ดึงข้อมูลจาก thaiwater.net ไม่สำเร็จ ลองใหม่อีกครั้งในอีกสักครู่", button=False)
        return
    send_to(chat, text, png)


HELP_TEXT = ("🌊 <b>บอทแจ้งเตือนระดับน้ำ สถานีบางปะกง</b>\n"
             "/status — ดูระดับน้ำและสถานะถนนตอนนี้\n"
             "หรือกดปุ่ม 🔄 ใต้ภาพแจ้งเตือน\n\n"
             "บอทจะแจ้งเองอัตโนมัติเมื่อสถานะเปลี่ยน (เช็กทุก 10 นาที)")


def run_bot():
    if not BOT_TOKEN or not CHAT_ID:
        sys.exit(f"ยังไม่ได้ตั้ง TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (ตรวจไฟล์ {BASE_DIR / '.env'})")
    allowed = set(chats())
    me = tg("getMe")["username"].lower()
    tg("setMyCommands", json={"commands": [
        {"command": "status", "description": "ดูระดับน้ำและสถานะถนนตอนนี้"},
        {"command": "help", "description": "วิธีใช้"}]})
    # ข้ามข้อความที่ค้างอยู่ก่อนบอทเริ่ม จะได้ไม่ตอบคำสั่งเก่า
    pending = tg("getUpdates", json={"timeout": 0})
    offset = pending[-1]["update_id"] + 1 if pending else None
    print(f"บอท @{me} พร้อมรับคำสั่ง (แชตที่อนุญาต: {', '.join(allowed)})", flush=True)

    while True:
        try:
            updates = tg("getUpdates", timeout=60, json={
                "offset": offset, "timeout": 50, "allowed_updates": ["message", "callback_query"]})
        except Exception as e:
            print("getUpdates ผิดพลาด:", e, file=sys.stderr)
            time.sleep(10)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            try:
                if "callback_query" in u:
                    q = u["callback_query"]
                    chat = str((q.get("message") or {}).get("chat", {}).get("id", ""))
                    if chat not in allowed:
                        tg("answerCallbackQuery", json={"callback_query_id": q["id"], "text": "ไม่มีสิทธิ์ใช้งาน"})
                        continue
                    tg("answerCallbackQuery", json={"callback_query_id": q["id"], "text": "กำลังดึงข้อมูล..."})
                    if q.get("data") == "status":
                        reply_status(chat)
                    continue
                m = u.get("message") or {}
                text = (m.get("text") or "").strip()
                if not text.startswith("/"):
                    continue
                cmd, _, target = text.split()[0][1:].partition("@")
                if target and target.lower() != me:
                    continue                    # คำสั่งของบอทตัวอื่นในกลุ่ม
                chat = str(m["chat"]["id"])
                if chat not in allowed:
                    print(f"ปฏิเสธคำสั่งจากแชต {chat} (ไม่อยู่ใน TELEGRAM_CHAT_ID)", file=sys.stderr)
                    continue
                cmd = cmd.lower()
                if cmd == "status":
                    print(f"{datetime.now():%Y-%m-%d %H:%M} /status จากแชต {chat}", flush=True)
                    reply_status(chat)
                elif cmd in ("start", "help"):
                    send_to(chat, HELP_TEXT, button=True)
            except Exception as e:
                print("จัดการข้อความไม่สำเร็จ:", e, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="ส่งข้อความทดสอบพร้อมค่าปัจจุบัน")
    ap.add_argument("--preview", action="store_true", help="สร้างภาพตัวอย่างทุกสถานะใน preview/ ไม่ส่ง Telegram")
    ap.add_argument("--bot", action="store_true", help="รันบอทรอรับคำสั่ง /status และปุ่มบน Telegram (ทำงานตลอด)")
    args = ap.parse_args()

    if args.bot:
        run_bot()
        return

    d = parse(fetch_station())
    now = datetime.now()

    if args.test:
        print(message("test", d))
        notify("test", d, LEVEL_OF[classify(d, None)])
        return

    if args.preview:
        preview(d)
        return

    # เขียนไฟล์สถานะไม่ได้ = จะแจ้งซ้ำทุกรอบ จึงหยุดก่อนส่ง
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.touch(exist_ok=True)
    except OSError as e:
        sys.exit(f"เขียนไฟล์สถานะ {STATE_FILE} ไม่ได้ ({e}) — ตรวจสิทธิ์โฟลเดอร์ data/ (ต้องเป็นของ uid 1000)")
    raw_state = STATE_FILE.read_text(encoding="utf-8").strip()
    state = json.loads(raw_state) if raw_state else {}
    prev_status = state.get("status")
    status = classify(d, prev_status)
    to_send = []

    # ข้อมูลค้าง
    stale = d["ts"] is not None and (now - d["ts"]).total_seconds() > STALE_H * 3600
    if stale and not state.get("stale"):
        to_send.append(("stale", "stale"))
    state["stale"] = stale

    if prev_status is None:
        # รอบแรก: บันทึกสถานะ ไม่แจ้ง (ยกเว้นเข้าเกณฑ์อยู่แล้ว)
        if status in ("near", "over"):
            to_send.append((status, LEVEL_OF[status]))
        state["alert_wl"] = d["wl"]
    elif status != prev_status:
        kind = "receding" if (prev_status == "over" and status == "near") else status
        to_send.append((kind, LEVEL_OF[status]))
        state["alert_wl"] = d["wl"]
    elif status in ("near", "over") and d["wl"] is not None \
            and d["wl"] - (state.get("alert_wl") or d["wl"]) >= REPEAT_STEP_M:
        to_send.append(("rising", LEVEL_OF[status]))
        state["alert_wl"] = d["wl"]

    state.update({"status": status, "last_wl": d["wl"], "last_time": d["time"],
                  "checked": now.isoformat(timespec="minutes")})
    for kind, level in to_send:
        notify(kind, d, level)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    tb = d["to_bank"]
    print(f"{now:%Y-%m-%d %H:%M} {d['wl']:.2f} ม. | ห่างตลิ่ง {tb*100 if tb is not None else float('nan'):.0f} ซม. "
          f"| สถานะ {status} | ส่ง {len(to_send)} ข้อความ")


def preview(d):
    """ภาพตัวอย่าง 4 สถานะ — ใช้กราฟจริง แต่ปรับค่าปัจจุบันให้ตรงกับแต่ละสถานะ"""
    out_dir = BASE_DIR / "preview"
    out_dir.mkdir(exist_ok=True)
    history = fetch_history(d["station_id"])
    samples = {"ok": ("normal", None), "risk": ("near", NEAR_M / 2),
               "avoid": ("over", -0.10), "stale": ("stale", None)}
    for level, (kind, tb) in samples.items():
        dd, hist = dict(d), [p for p in history if p[1] is not None]
        if tb is not None and d["bank"] is not None and hist:
            dd["to_bank"], dd["wl"] = tb, round(d["bank"] - tb, 2)
            dd["prev"] = dd["wl"] - 0.03
            # ต่อหางกราฟให้วิ่งขึ้นไปถึงค่าตัวอย่าง จะได้เห็นเส้นเข้าโซน
            t0, v0 = hist[-1]
            hist += [(t0 + timedelta(minutes=10 * i), v0 + (dd["wl"] - v0) * i / 12) for i in range(1, 13)]
            dd["ts"] = hist[-1][0]
        png = card(kind, dd, level, history=hist)
        if png:
            (out_dir / f"{level}.png").write_bytes(png)
            print("เขียน", out_dir / f"{level}.png")


if __name__ == "__main__":
    main()
