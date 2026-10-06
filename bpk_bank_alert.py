#!/usr/bin/env python3
"""
bpk_bank_alert.py — แจ้งเตือนระดับน้ำใกล้ล้นตลิ่ง ผ่าน Telegram (หลายสถานี เช่น 4 สถานีใน จ.ฉะเชิงเทรา)

ข้อมูล: thaiwater.net (สสน.) — API waterlevel_load
เงื่อนไข (แยกรายสถานี):
  🟠 ใกล้ล้นตลิ่ง : ระดับน้ำต่ำกว่าตลิ่งน้อยกว่า NEAR_M (30 ซม.)
  🔴 ล้นตลิ่ง     : ระดับน้ำเท่ากับ/สูงกว่าตลิ่ง
  🟢 กลับสู่ปกติ  : ลดลงจนห่างตลิ่ง >= NEAR_M + HYSTERESIS_M
  ⚪ ข้อมูลไม่อัปเดต : สถานีไม่ส่งค่าใหม่เกิน STALE_H ชั่วโมง
แจ้งเฉพาะตอน "เปลี่ยนสถานะ" + แจ้งซ้ำเมื่อน้ำสูงขึ้นอีก REPEAT_STEP_M ระหว่างอยู่ในสถานะเตือน
และส่งภาพสรุปทุกสถานีทุกวันเวลา DAILY_REPORT_TIME

ใช้งาน:
  pip install requests pillow
  copy .env.example .env  แล้วใส่ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / STATION_CODES ในไฟล์ .env
  python bpk_bank_alert.py --test     # ส่งภาพสรุปทุกสถานีเข้ากลุ่ม (ทดสอบ)
  python bpk_bank_alert.py --preview  # สร้างภาพตัวอย่างในโฟลเดอร์ preview/ (ไม่ส่ง Telegram)
  python bpk_bank_alert.py --bot      # บอทรอรับ /status และปุ่มเลือกสถานี (รันค้างไว้ตลอด)
  python bpk_bank_alert.py            # เช็ก 1 รอบ (ตั้ง Task Scheduler / cron ทุก 10 นาที)
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
STATION_CODE = os.getenv("STATION_CODE", "BPK001")  # สถานีหลัก (ใช้เมื่อไม่ได้ตั้ง STATION_CODES)
# สถานีที่เฝ้าระวัง คั่นด้วย , — ค่าเริ่มต้น = 4 สถานีใน จ.ฉะเชิงเทรา
STATION_CODES = [c.strip().upper() for c in os.getenv("STATION_CODES", "BPK001,BPK003,BPK004,BKK016").split(",")
                 if c.strip()] or [STATION_CODE]
AREA_TITLE = os.getenv("AREA_TITLE", "")            # หัวข้อภาพสรุป (ว่าง = "ระดับน้ำ จ.<จังหวัด>")
NEAR_M = env_float("NEAR_M", 0.30)                  # เตือนเมื่อห่างตลิ่ง < 30 ซม.
HYSTERESIS_M = env_float("HYSTERESIS_M", 0.05)      # ต้องลดอีก 5 ซม. ถึงนับว่ากลับปกติ
REPEAT_STEP_M = env_float("REPEAT_STEP_M", 0.10)    # อยู่ในสถานะเตือน น้ำขึ้นอีก 10 ซม. -> แจ้งซ้ำ
STALE_H = env_float("STALE_H", 2)                   # ไม่มีค่าใหม่เกินกี่ชั่วโมง -> แจ้งข้อมูลไม่อัปเดต
STATE_FILE = BASE_DIR / os.getenv("STATE_FILE", "bpk_alert_state.json")
ALERT_IMAGE = os.getenv("ALERT_IMAGE", "1").lower() not in ("0", "false", "no", "off")  # ส่งเป็นภาพการ์ด+กราฟ
GRAPH_HOURS = int(env_float("GRAPH_HOURS", 24))     # กราฟย้อนหลังกี่ชั่วโมง
ROAD_NAME = os.getenv("ROAD_NAME", "")              # ชื่อจุดของสถานีหลัก (STATION_CODE) — แบบเดิม
DAILY_GRACE_H = 3                                   # เครื่องดับช่วงเวลารายงาน ส่งย้อนหลังได้ไม่เกินกี่ชั่วโมง


def road_name(code):
    """ชื่อจุด/ถนนที่ต้องระวังของแต่ละสถานี: ROAD_NAME_<รหัส> เช่น ROAD_NAME_BPK003=..."""
    return os.getenv(f"ROAD_NAME_{code}") or (ROAD_NAME if code == STATION_CODE else "")


def parse_hhmm(v):
    """'06:30' / '6.30' -> (6, 30) ; ว่าง = ปิดรายงานประจำวัน"""
    v = (v or "").strip().replace(".", ":")
    if not v:
        return None
    try:
        h, m = (int(x) for x in v.split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    print(f"DAILY_REPORT_TIME={v!r} ไม่ถูกต้อง (ต้องเป็น HH:MM) ปิดรายงานประจำวัน", file=sys.stderr)
    return None


DAILY_AT = parse_hhmm(os.getenv("DAILY_REPORT_TIME", "06:30"))  # รายงานประจำวัน (เว้นว่าง = ปิด)
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


def fetch_stations(codes=STATION_CODES):
    """{รหัส: ข้อมูลดิบ} ของสถานีที่ขอ — API คืนทุกสถานีทั่วประเทศในครั้งเดียว"""
    last_err = None
    for i in range(3):
        try:
            r = requests.get(API_URL, params={"timestamp": int(time.time() * 1000)},
                             headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
            rows = (data.get("waterlevel_data") or {}).get("data") or data.get("data") or []
            found = {}
            for raw in rows:
                code = (raw.get("station") or {}).get("tele_station_oldcode")
                if code in codes:
                    found[code] = raw
            if not found:
                raise RuntimeError(f"ไม่พบสถานี {', '.join(codes)} ในข้อมูล")
            missing = [c for c in codes if c not in found]
            if missing:
                print(f"ไม่พบสถานี {', '.join(missing)} ในข้อมูลรอบนี้", file=sys.stderr)
            return found
        except Exception as e:
            last_err = e
            time.sleep(5 * (i + 1))
    raise last_err


def parse(raw):
    st = raw["station"]
    geo = raw.get("geocode") or {}
    wl = num(raw.get("waterlevel_msl"))
    prev = num(raw.get("waterlevel_msl_previous"))
    bank = num(st.get("min_bank"))
    # ระยะห่างตลิ่ง (+ = ต่ำกว่าตลิ่ง, - = ล้นตลิ่ง) คำนวณเองจากตัวเลข ไม่พึ่งข้อความ
    # (diff_wl_bank เป็นค่าบวกเสมอ ส่วน diff_wl_bank_text ใช้คำว่า "ล้นตลิ่ง"/"ต่ำกว่าตลิ่ง")
    if wl is not None and bank is not None:
        to_bank = round(bank - wl, 2)
    else:
        to_bank = num(raw.get("diff_wl_bank"))
        if to_bank is not None and "ต่ำกว่า" not in (raw.get("diff_wl_bank_text") or ""):
            to_bank = -to_bank
    t = raw.get("waterlevel_datetime") or ""
    try:
        ts = datetime.strptime(t[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        ts = None
    return {"code": st.get("tele_station_oldcode"), "name": (st.get("tele_station_name") or {}).get("th", ""),
            "river": raw.get("river_name") or "", "amphoe": (geo.get("amphoe_name") or {}).get("th", ""),
            "province": (geo.get("province_name") or {}).get("th", ""), "station_id": st.get("id"),
            "wl": wl, "prev": prev, "bank": bank, "to_bank": to_bank, "time": t, "ts": ts}


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


def is_stale(d, now=None):
    return d["ts"] is not None and ((now or datetime.now()) - d["ts"]).total_seconds() > STALE_H * 3600


LEVEL_OF = {"normal": "ok", "near": "risk", "over": "avoid"}
LEVEL_EMOJI = {"ok": "🟢", "risk": "🟠", "avoid": "🔴", "stale": "⚪"}
HEADS = {
    "near": "🟠 <b>เตือน: น้ำใกล้ล้นตลิ่ง</b>",
    "over": "🔴 <b>ด่วน: น้ำล้นตลิ่ง</b>",
    "normal": "🟢 <b>ระดับน้ำกลับสู่ปกติ</b>",
    "rising": "🟠 <b>น้ำยังสูงขึ้นต่อเนื่อง</b>",
    "receding": "🟠 <b>น้ำลดลงต่ำกว่าตลิ่งแล้ว (ยังเฝ้าระวัง)</b>",
    "stale": "⚪ <b>สถานีไม่อัปเดตข้อมูล</b>",
    "test": "🧪 <b>ทดสอบระบบแจ้งเตือน</b>",
    "status": "📍 <b>สถานะ ณ ตอนนี้</b>",
    "daily": "☀️ <b>รายงานระดับน้ำประจำวัน</b>",
}


def bank_text(tb):
    return "-" if tb is None else (f"เกินตลิ่ง {abs(tb)*100:.0f} ซม." if tb <= 0 else f"ต่ำกว่าตลิ่ง {tb*100:.0f} ซม.")


def trend_text(d):
    if d["wl"] is None or d["prev"] is None:
        return ""
    diff = d["wl"] - d["prev"]
    return f"{'⬆️ ขึ้น' if diff > 0 else '⬇️ ลง' if diff < 0 else '➡️ ทรงตัว'} {abs(diff)*100:.0f} ซม."


def message(kind, d):
    trend = trend_text(d)
    wl = "-" if d["wl"] is None else f"{d['wl']:.2f}"
    bank = "-" if d["bank"] is None else f"{d['bank']:.2f}"
    return (f"{HEADS[kind]}\n"
            f"สถานี{d['name']} ({d['code']}) {d['river']}\n"
            f"ระดับน้ำ: <b>{wl} ม.รทก.</b>{f' ({trend})' if trend else ''}\n"
            f"ตลิ่ง: {bank} ม.รทก. → <b>{bank_text(d['to_bank'])}</b>\n"
            f"เวลาวัด: {d['time']}\n"
            f'<a href="{WEB_URL}">thaiwater.net</a>')


def area_title(items):
    if AREA_TITLE:
        return AREA_TITLE
    provinces = {it["d"]["province"] for it in items if it["d"]["province"]}
    return f"ระดับน้ำ จ.{provinces.pop()}" if len(provinces) == 1 else "ระดับน้ำสถานีเฝ้าระวัง"


def summary_text(kind, items):
    lines = [f"{HEADS[kind]} — {area_title(items)}"]
    for it in items:
        d = it["d"]
        lines.append(f"{LEVEL_EMOJI[it['level']]} {d['name']} — {bank_text(d['to_bank'])}")
    stamps = [it["d"]["ts"] for it in items if it["d"]["ts"]]
    if stamps:
        lines.append(f"ข้อมูล ณ {max(stamps):%Y-%m-%d %H:%M}")
    lines.append(f'<a href="{WEB_URL}">thaiwater.net</a>')
    return "\n".join(lines)


def card(kind, d, level, history=None):
    """ภาพการ์ดรายสถานี PNG — ถ้าสร้างไม่ได้ (ไม่มี Pillow/ฟอนต์ ฯลฯ) คืน None แล้วส่งเป็นข้อความแทน"""
    if not ALERT_IMAGE:
        return None
    try:
        from alert_card import render_card
        if history is None:
            try:
                history = fetch_history(d["station_id"])
            except Exception as e:
                print("ดึงกราฟย้อนหลังไม่สำเร็จ:", e, file=sys.stderr)
        return render_card(level, kind, d, near_m=NEAR_M, road=road_name(d["code"]),
                           history=history, hours=GRAPH_HOURS)
    except Exception as e:
        print("สร้างภาพไม่สำเร็จ ส่งเป็นข้อความแทน:", e, file=sys.stderr)
        return None


def summary_card(kind, items):
    if not ALERT_IMAGE:
        return None
    try:
        from alert_card import render_summary
        return render_summary(kind, [dict(it, road=road_name(it["d"]["code"])) for it in items],
                              title=area_title(items))
    except Exception as e:
        print("สร้างภาพสรุปไม่สำเร็จ ส่งเป็นข้อความแทน:", e, file=sys.stderr)
        return None


# ---------------- ปุ่มใต้ภาพ ----------------
def station_markup(code):
    """ใต้การ์ดรายสถานี: ดูสถานีนี้ล่าสุด / ดูภาพรวม"""
    return {"inline_keyboard": [[{"text": "🔄 สถานีนี้ล่าสุด", "callback_data": f"st:{code}"},
                                 {"text": "📋 ทุกสถานี", "callback_data": "all"}]]}


def summary_markup(items):
    """ใต้ภาพสรุป: ปุ่มชื่อสถานี (แถวละ 2) + รีเฟรช"""
    btns = [{"text": f"{LEVEL_EMOJI[it['level']]} {it['d']['name']}", "callback_data": f"st:{it['d']['code']}"}
            for it in items]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([{"text": "🔄 รีเฟรชภาพรวม", "callback_data": "all"}])
    return {"inline_keyboard": rows}


# ---------------- Telegram ----------------
def chats():
    return [c.strip() for c in CHAT_ID.split(",") if c.strip()]


def tg(method, timeout=20, **kw):
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", timeout=timeout, **kw)
    if not r.ok:
        print("Telegram error:", r.status_code, r.text, file=sys.stderr)
        r.raise_for_status()
    return r.json().get("result")


def send_to(chat, text, png=None, markup=None):
    if png:
        data = {"chat_id": chat, "caption": text, "parse_mode": "HTML"}
        if markup:
            data["reply_markup"] = json.dumps(markup)
        tg("sendPhoto", timeout=30, data=data, files={"photo": ("water_alert.png", png, "image/png")})
    else:
        body = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if markup:
            body["reply_markup"] = markup
        tg("sendMessage", json=body)


def send(text, png=None, markup=None):
    if not BOT_TOKEN or not CHAT_ID:
        print(f"!! ยังไม่ได้ตั้ง TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (ตรวจไฟล์ {BASE_DIR / '.env'})\n" + text)
        return
    for chat in chats():
        send_to(chat, text, png, markup)


def notify(kind, d, level):
    send(message(kind, d), card(kind, d, level), station_markup(d["code"]))


def notify_summary(kind, items):
    send(summary_text(kind, items), summary_card(kind, items), summary_markup(items))


# ---------------- สถานะ ----------------
def read_state():
    try:
        raw = STATE_FILE.read_text(encoding="utf-8").strip()
        state = json.loads(raw) if raw else {}
    except FileNotFoundError:
        state = {}
    if "stations" not in state:
        # ไฟล์สถานะรุ่นเดิม (สถานีเดียว) -> ย้ายไปเป็นของ STATION_CODE จะได้ไม่แจ้งซ้ำหลังอัปเดต
        old = {k: state[k] for k in ("status", "stale", "alert_wl", "last_wl", "last_time") if k in state}
        state = {"stations": {STATION_CODE: old} if old else {}, "daily_sent": state.get("daily_sent")}
    return state


def current_items(raws, state, now=None):
    """[{d, level}] เรียงตาม STATION_CODES — level ใช้สถานะที่บันทึกไว้เป็นฐาน (hysteresis เดียวกับการแจ้งเตือน)"""
    items = []
    for code in STATION_CODES:
        if code not in raws:
            continue
        d = parse(raws[code])
        prev = state["stations"].get(code, {}).get("status")
        items.append({"d": d, "level": "stale" if is_stale(d, now) else LEVEL_OF[classify(d, prev)]})
    return items


# ---------------- โหมดบอท: /status และปุ่มเลือกสถานี ----------------
CACHE_SEC = 60              # กดถี่ ๆ ภายใน 60 วินาที ใช้ข้อมูล/ภาพเดิม ไม่ยิง thaiwater ซ้ำ
_cache = {"at": 0.0, "items": None, "png": {}}


def cached_items():
    if time.time() - _cache["at"] > CACHE_SEC or _cache["items"] is None:
        _cache.update(at=time.time(), items=current_items(fetch_stations(), read_state()), png={})
    return _cache["items"]


def reply_all(chat):
    items = cached_items()
    if "all" not in _cache["png"]:
        _cache["png"]["all"] = summary_card("status", items)
    send_to(chat, summary_text("status", items), _cache["png"]["all"], summary_markup(items))


def reply_station(chat, code):
    it = next((x for x in cached_items() if x["d"]["code"] == code), None)
    if it is None:
        send_to(chat, f"ไม่พบข้อมูลสถานี {code} ในรอบนี้")
        return
    if code not in _cache["png"]:
        _cache["png"][code] = card("status", it["d"], it["level"])
    send_to(chat, message("status", it["d"]), _cache["png"][code], station_markup(code))


def find_station(arg):
    """'/status BPK003' หรือ '/status บางน้ำ' -> รหัสสถานี (None = ไม่ระบุ/ไม่เจอ)"""
    arg = arg.strip()
    if not arg:
        return None
    if arg.upper() in STATION_CODES:
        return arg.upper()
    return next((x["d"]["code"] for x in cached_items() if arg in x["d"]["name"]), None)


def safe_reply(chat, fn, *a):
    try:
        fn(chat, *a)
    except Exception as e:
        print("ดึงสถานะไม่สำเร็จ:", e, file=sys.stderr)
        send_to(chat, "⚠️ ดึงข้อมูลจาก thaiwater.net ไม่สำเร็จ ลองใหม่อีกครั้งในอีกสักครู่")


HELP_TEXT = ("🌊 <b>บอทแจ้งเตือนระดับน้ำ</b>\n"
             "/status — ภาพสรุปทุกสถานี แล้วกดชื่อสถานีเพื่อดูกราฟ\n"
             "/status บางน้ำเปรี้ยว — ดูเฉพาะสถานี (พิมพ์ชื่อบางส่วนหรือรหัสก็ได้)\n\n"
             "บอทจะแจ้งเองอัตโนมัติเมื่อสถานะเปลี่ยน (เช็กทุก 10 นาที) และสรุปทุกเช้า")


def run_bot():
    if not BOT_TOKEN or not CHAT_ID:
        sys.exit(f"ยังไม่ได้ตั้ง TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (ตรวจไฟล์ {BASE_DIR / '.env'})")
    allowed = set(chats())
    me = tg("getMe")["username"].lower()
    tg("setMyCommands", json={"commands": [
        {"command": "status", "description": "ดูระดับน้ำทุกสถานีตอนนี้"},
        {"command": "help", "description": "วิธีใช้"}]})
    # ข้ามข้อความที่ค้างอยู่ก่อนบอทเริ่ม จะได้ไม่ตอบคำสั่งเก่า
    pending = tg("getUpdates", json={"timeout": 0})
    offset = pending[-1]["update_id"] + 1 if pending else None
    print(f"บอท @{me} พร้อมรับคำสั่ง (สถานี: {', '.join(STATION_CODES)} | แชตที่อนุญาต: {', '.join(allowed)})",
          flush=True)

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
                    data = q.get("data") or ""
                    if data in ("all", "status"):            # "status" = ปุ่มจากภาพรุ่นเดิม
                        safe_reply(chat, reply_all)
                    elif data.startswith("st:") and data[3:] in STATION_CODES:
                        safe_reply(chat, reply_station, data[3:])
                    continue
                m = u.get("message") or {}
                text = (m.get("text") or "").strip()
                if not text.startswith("/"):
                    continue
                head, _, arg = text.partition(" ")
                cmd, _, target = head[1:].partition("@")
                if target and target.lower() != me:
                    continue                    # คำสั่งของบอทตัวอื่นในกลุ่ม
                chat = str(m["chat"]["id"])
                if chat not in allowed:
                    print(f"ปฏิเสธคำสั่งจากแชต {chat} (ไม่อยู่ใน TELEGRAM_CHAT_ID)", file=sys.stderr)
                    continue
                cmd = cmd.lower()
                if cmd == "status":
                    print(f"{datetime.now():%Y-%m-%d %H:%M} /status {arg} จากแชต {chat}", flush=True)
                    try:
                        code = find_station(arg)
                    except Exception:
                        code = None
                    if code:
                        safe_reply(chat, reply_station, code)
                    else:
                        safe_reply(chat, reply_all)
                elif cmd in ("start", "help"):
                    send_to(chat, HELP_TEXT, markup={"inline_keyboard": [[
                        {"text": "📋 ดูทุกสถานี", "callback_data": "all"}]]})
            except Exception as e:
                print("จัดการข้อความไม่สำเร็จ:", e, file=sys.stderr)


# ---------------- ตัวเช็กรอบละ 10 นาที ----------------
def check_station(d, s, now):
    """ตัดสินว่าสถานีนี้ต้องแจ้งอะไร พร้อมอัปเดตสถานะ s (dict ของสถานีนั้น) — คืน [(kind, level)]"""
    prev_status = s.get("status")
    status = classify(d, prev_status)
    out = []

    stale = is_stale(d, now)
    if stale and not s.get("stale"):
        out.append(("stale", "stale"))
    s["stale"] = stale

    if prev_status is None:
        # รอบแรกของสถานีนี้: บันทึกสถานะ ไม่แจ้ง (ยกเว้นเข้าเกณฑ์อยู่แล้ว)
        if status in ("near", "over"):
            out.append((status, LEVEL_OF[status]))
        s["alert_wl"] = d["wl"]
    elif status != prev_status:
        kind = "receding" if (prev_status == "over" and status == "near") else status
        out.append((kind, LEVEL_OF[status]))
        s["alert_wl"] = d["wl"]
    elif status in ("near", "over") and d["wl"] is not None \
            and d["wl"] - (s.get("alert_wl") or d["wl"]) >= REPEAT_STEP_M:
        out.append(("rising", LEVEL_OF[status]))
        s["alert_wl"] = d["wl"]

    s.update({"status": status, "last_wl": d["wl"], "last_time": d["time"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="ส่งภาพสรุปทุกสถานีเข้ากลุ่ม (ทดสอบ)")
    ap.add_argument("--preview", action="store_true", help="สร้างภาพตัวอย่างใน preview/ ไม่ส่ง Telegram")
    ap.add_argument("--bot", action="store_true", help="รันบอทรอรับคำสั่ง /status และปุ่มบน Telegram (ทำงานตลอด)")
    args = ap.parse_args()

    if args.bot:
        run_bot()
        return

    raws = fetch_stations()
    now = datetime.now()

    if args.test:
        items = current_items(raws, read_state(), now)
        print(summary_text("test", items))
        notify_summary("test", items)
        return

    if args.preview:
        preview(raws)
        return

    # เขียนไฟล์สถานะไม่ได้ = จะแจ้งซ้ำทุกรอบ จึงหยุดก่อนส่ง
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.touch(exist_ok=True)
    except OSError as e:
        sys.exit(f"เขียนไฟล์สถานะ {STATE_FILE} ไม่ได้ ({e}) — ตรวจสิทธิ์โฟลเดอร์ data/ (ต้องเป็นของ uid 1000)")
    state = read_state()

    alerts, items = [], []
    for code in STATION_CODES:
        if code not in raws:
            continue
        d = parse(raws[code])
        s = state["stations"].setdefault(code, {})
        for kind, level in check_station(d, s, now):
            alerts.append((kind, d, level))
        items.append({"d": d, "level": "stale" if s["stale"] else LEVEL_OF[s["status"]]})

    # รายงานประจำวัน: ภาพสรุปทุกสถานี — ส่งรอบแรกที่ถึงเวลา (เครื่องดับช่วงนั้น ส่งย้อนหลังได้ไม่เกิน DAILY_GRACE_H)
    daily = False
    if DAILY_AT is not None and state.get("daily_sent") != f"{now:%Y-%m-%d}":
        due = now.replace(hour=DAILY_AT[0], minute=DAILY_AT[1], second=0, microsecond=0)
        if due <= now < due + timedelta(hours=DAILY_GRACE_H):
            daily = True
            state["daily_sent"] = f"{now:%Y-%m-%d}"

    state["checked"] = now.isoformat(timespec="minutes")
    for kind, d, level in alerts:
        notify(kind, d, level)
    if daily and items:
        notify_summary("daily", items)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    for it in items:
        d = it["d"]
        wl = "-" if d["wl"] is None else f"{d['wl']:.2f}"
        print(f"{now:%Y-%m-%d %H:%M} {d['code']:<7} {d['name'][:22]:<22} {wl:>6} ม. | "
              f"{bank_text(d['to_bank']):<20} | {it['level']}")
    print(f"{now:%Y-%m-%d %H:%M} ส่งแจ้งเตือน {len(alerts)} ข้อความ{' + รายงานประจำวัน' if daily else ''}", flush=True)


def preview(raws):
    """ภาพตัวอย่าง: การ์ด 4 ระดับของสถานีแรก (ปรับค่าให้ตรงแต่ละระดับ) + ภาพสรุปจากข้อมูลจริง"""
    out_dir = BASE_DIR / "preview"
    out_dir.mkdir(exist_ok=True)
    items = current_items(raws, read_state())
    png = summary_card("status", items)
    if png:
        (out_dir / "summary.png").write_bytes(png)
        print("เขียน", out_dir / "summary.png")

    d = items[0]["d"]
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
