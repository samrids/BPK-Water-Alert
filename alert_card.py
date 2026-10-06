"""
alert_card.py — วาดภาพการ์ดแจ้งเตือนระดับน้ำ (PNG) ให้มองแวบเดียวรู้ว่า ผ่านได้ / เสี่ยง / ควรเลี่ยง
พร้อมกราฟระดับน้ำย้อนหลังเทียบกับตลิ่ง

ใช้ฟอนต์ Sarabun ในโฟลเดอร์ fonts/ (OFL) และต้องมี Pillow ที่เปิด raqm เพื่อจัดสระ/วรรณยุกต์ไทยถูกตำแหน่ง
"""
import io
from collections import Counter
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).resolve().parent / "fonts"
W, H = 1080, 1360
S = 2                       # วาดใหญ่ 2 เท่าแล้วย่อ ขอบเส้นจะเรียบ

INK, MUTED, PAPER, TILE, LINE = "#202124", "#5F6368", "#FFFFFF", "#F1F3F4", "#DADCE0"
GREEN, AMBER, RED, GREY, WATER = "#188038", "#F9AB00", "#C5221F", "#5F6368", "#1A73E8"
BAND = {"ok": "#E6F4EA", "risk": "#FEEFC3", "avoid": "#FCE8E6"}
THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
               "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]

# ระดับ -> (สีพื้น, สีตัวอักษร, หัวข้อใหญ่, คำอธิบาย)
LEVELS = {
    "ok":    (GREEN, PAPER, "ผ่านได้ปกติ",      "ระดับน้ำยังห่างตลิ่ง ถนนปกติ"),
    "risk":  (AMBER, INK,   "ผ่านได้ แต่เสี่ยง", "น้ำใกล้ตลิ่ง ถนนอาจมีน้ำขัง ขับช้า ๆ"),
    "avoid": (RED,   PAPER, "เลี่ยงเส้นทาง",    "น้ำล้นตลิ่ง ถนนริมน้ำอาจจมน้ำ"),
    "stale": (GREY,  PAPER, "ข้อมูลไม่อัปเดต",  "สถานีไม่ส่งค่าใหม่ ตรวจสอบเองก่อนเดินทาง"),
}
KIND_LABEL = {
    "near": "แจ้งเตือน: น้ำใกล้ล้นตลิ่ง",
    "over": "ด่วน: น้ำล้นตลิ่ง",
    "normal": "ระดับน้ำกลับสู่ปกติ",
    "rising": "น้ำยังสูงขึ้นต่อเนื่อง",
    "receding": "น้ำลดลงต่ำกว่าตลิ่งแล้ว (ยังเฝ้าระวัง)",
    "stale": "สถานีไม่อัปเดตข้อมูล",
    "test": "ทดสอบระบบแจ้งเตือน",
    "status": "สถานะ ณ ตอนนี้ (ตามคำขอ)",
    "daily": "รายงานประจำวัน",
}
# สถานีที่เทียบกับระดับถนน (ROAD_LEVEL_<รหัส>) แทนตลิ่ง
SUB_ROAD = {"ok": "ระดับน้ำยังต่ำกว่าถนน ถนนปกติ",
            "risk": "น้ำใกล้ระดับถนน อาจมีน้ำขัง ขับช้า ๆ",
            "avoid": "น้ำขึ้นถนนแล้ว ควรเลี่ยงเส้นทาง"}
KIND_ROAD = dict(KIND_LABEL, near="แจ้งเตือน: น้ำใกล้ขึ้นถนน", over="ด่วน: น้ำท่วมถนน",
                 receding="น้ำลดลงต่ำกว่าถนนแล้ว (ยังเฝ้าระวัง)")


def is_road(d):
    return d.get("ref_name") == "ถนน"


@lru_cache(maxsize=None)
def font(size, weight="Bold"):
    return ImageFont.truetype(str(FONT_DIR / f"Sarabun-{weight}.ttf"), int(size * S))


def P(*v):
    return [int(x * S) for x in v]


class Card:
    def __init__(self, h=H):
        self.h = h
        self.img = Image.new("RGBA", P(W, h), PAPER)
        self.d = ImageDraw.Draw(self.img)

    def text(self, x, y, txt, size, weight="Bold", fill=INK, anchor="ls"):
        self.d.text(P(x, y), txt, font=font(size, weight), fill=fill, anchor=anchor)

    def width(self, txt, size, weight="Bold"):
        return self.d.textlength(txt, font=font(size, weight)) / S

    def fit(self, txt, size, weight, max_w, min_size=24):
        while size > min_size and self.width(txt, size, weight) > max_w:
            size -= 2
        return size

    def overlay(self, draw_fn):
        """วาดรูปทรงโปร่งแสงบนเลเยอร์แยกแล้วรวมกลับ"""
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        draw_fn(ImageDraw.Draw(layer))
        self.img.alpha_composite(layer)
        self.d = ImageDraw.Draw(self.img)

    def png(self):
        out = io.BytesIO()
        self.img.convert("RGB").resize((W, self.h), Image.LANCZOS).save(out, "PNG", optimize=True)
        return out.getvalue()


def draw_icon(c, level, cx, cy, r, bg, fg):
    d = c.d
    if level == "risk":         # ป้ายสามเหลี่ยม !
        d.polygon([tuple(P(cx, cy - r)), tuple(P(cx + r * .98, cy + r * .8)), tuple(P(cx - r * .98, cy + r * .8))], fill=fg)
        d.rounded_rectangle(P(cx - r * .09, cy - r * .42, cx + r * .09, cy + r * .28), radius=int(r * .09 * S), fill=bg)
        d.ellipse(P(cx - r * .11, cy + r * .42, cx + r * .11, cy + r * .64), fill=bg)
        return
    d.ellipse(P(cx - r, cy - r, cx + r, cy + r), fill=fg)
    if level == "ok":           # เครื่องหมายถูก
        d.line([tuple(P(cx - r * .45, cy + r * .02)), tuple(P(cx - r * .12, cy + r * .35)),
                tuple(P(cx + r * .5, cy - r * .33))], fill=bg, width=int(r * .2 * S), joint="curve")
    elif level == "avoid":      # ป้ายห้ามเข้า
        d.rounded_rectangle(P(cx - r * .62, cy - r * .16, cx + r * .62, cy + r * .16), radius=int(r * .06 * S), fill=bg)
    else:                       # ?
        c.text(cx, cy + r * .05, "?", r * 1.5, "ExtraBold", bg, "mm")


def dashed(c, x0, x1, y, fill, width=3, dash=16, gap=10):
    x = x0
    while x < x1:
        c.d.line(P(x, y, min(x + dash, x1), y), fill=fill, width=width * S)
        x += dash + gap


def draw_chart(c, history, bank, near_m, box, now_wl=None, until=None, ref_name="ตลิ่ง", river_bank=None):
    """กราฟเส้นระดับน้ำ พื้นหลังแบ่งโซน ผ่านได้ / เสี่ยง / เลี่ยง
    bank: ระดับอ้างอิง (ตลิ่ง หรือถนน) ; river_bank: ตลิ่งแม่น้ำ วาดเป็นเส้นเทาเพิ่มเมื่ออ้างอิงถนน
    until: ตัดกราฟให้จบที่เวลาวัดเดียวกับตัวเลขบนการ์ด จะได้ไม่ขัดกัน"""
    px0, py0, px1, py1 = box
    pts = [(t, v) for t, v in history if v is not None and (until is None or t <= until)]
    if len(pts) < 2 or bank is None:
        c.d.rounded_rectangle(P(*box), radius=16 * S, fill=TILE)
        c.text((px0 + px1) / 2, (py0 + py1) / 2, "ไม่มีข้อมูลกราฟย้อนหลัง", 34, "Regular", MUTED, "mm")
        return
    t_end = pts[-1][0]
    t_start = pts[0][0]
    vals = [v for _, v in pts]
    lo = min(min(vals), bank - near_m) - 0.25
    hi = max(max(vals), bank, river_bank or bank) + 0.35
    span_t = (t_end - t_start).total_seconds() or 1

    def X(t):
        return px0 + (t - t_start).total_seconds() / span_t * (px1 - px0)

    def Y(v):
        return py1 - (v - lo) / (hi - lo) * (py1 - py0)

    y_bank, y_near = Y(bank), Y(bank - near_m)
    # แถบโซน
    c.d.rectangle(P(px0, py0, px1, y_bank), fill=BAND["avoid"])
    c.d.rectangle(P(px0, y_bank, px1, y_near), fill=BAND["risk"])
    c.d.rectangle(P(px0, y_near, px1, py1), fill=BAND["ok"])

    # เส้นตารางแกน Y ทุก 0.5 ม.
    step = 0.5
    v = (int(lo / step) - 1) * step
    while v <= hi:
        if lo <= v <= hi:
            c.d.line(P(px0, Y(v), px1, Y(v)), fill="#FFFFFF", width=2 * S)
            c.text(px0 - 14, Y(v), f"{v:.1f}", 26, "Regular", MUTED, "rm")
        v += step

    # แกนเวลา: ทุก 6 ชั่วโมง และวันที่ใต้เที่ยงคืน
    t = t_start.replace(minute=0, second=0, microsecond=0)
    while t <= t_end:
        if t >= t_start and t.hour % 6 == 0:
            x = X(t)
            c.d.line(P(x, py0, x, py1), fill="#FFFFFF", width=2 * S)
            c.text(x, py1 + 36, f"{t:%H:%M}", 26, "Regular", MUTED, "ms")
            if t.hour == 0:
                c.text(x, py1 + 68, f"{t.day} {THAI_MONTHS[t.month - 1]}", 24, "Bold", MUTED, "ms")
        t += timedelta(hours=1)

    # เส้นตลิ่ง / เส้นเฝ้าระวัง + ป้ายโซน (นอกกราฟฝั่งขวา)
    dashed(c, px0, px1, y_bank, RED, 3)
    dashed(c, px0, px1, y_near, "#B06000", 2, 10, 10)
    c.text(px0 + 14, y_bank - 12, f"{ref_name} {bank:.2f}", 26, "Bold", RED)
    if river_bank is not None and river_bank > bank:
        y_rb = Y(river_bank)
        dashed(c, px0, px1, y_rb, MUTED, 2, 6, 8)
        c.text(px0 + 14, y_rb - 12, f"ตลิ่ง {river_bank:.2f}", 24, "Regular", MUTED)
    lx = px1 + 16
    c.text(lx, min((py0 + y_bank) / 2, y_bank - 18), "เลี่ยง", 28, "Bold", RED, "lm")
    c.text(lx, (y_bank + y_near) / 2 if y_near - y_bank > 34 else y_near - 4, "เสี่ยง", 28, "Bold", "#B06000", "lm")
    c.text(lx, max((y_near + py1) / 2, y_near + 34), "ผ่านได้", 28, "Bold", GREEN, "lm")

    # เส้นระดับน้ำ + พื้นที่ใต้เส้น
    line = [tuple(P(X(t), Y(v))) for t, v in pts]
    area = line + [tuple(P(X(pts[-1][0]), py1)), tuple(P(X(pts[0][0]), py1))]
    c.overlay(lambda d: d.polygon(area, fill=(26, 115, 232, 46)))
    c.d.line(line, fill=WATER, width=5 * S, joint="curve")

    # จุดล่าสุด
    ex, ey = X(pts[-1][0]), Y(pts[-1][1])
    c.d.ellipse(P(ex - 13, ey - 13, ex + 13, ey + 13), fill=PAPER)
    c.d.ellipse(P(ex - 9, ey - 9, ex + 9, ey + 9), fill=WATER)
    tag = f"ตอนนี้ {now_wl if now_wl is not None else pts[-1][1]:.2f}"
    tw = c.width(tag, 28, "Bold")
    # ป้ายวางฝั่งตรงข้ามกับที่เส้นวิ่งมา: น้ำขึ้น -> ป้ายบน, น้ำลง -> ป้ายล่าง
    rising = pts[-1][1] >= pts[max(0, len(pts) - 7)][1]
    ty = ey - 30 if rising else ey + 64
    ty = min(max(ty, py0 + 42), py1 - 12)
    c.d.rounded_rectangle(P(ex - 26 - tw - 28, ty - 34, ex - 26, ty + 8), radius=10 * S, fill=WATER)
    c.text(ex - 26 - 14, ty - 2, tag, 28, "Bold", PAPER, "rs")
    c.d.rectangle(P(px0, py0, px1, py1), outline=LINE, width=2 * S)


def render_card(level, kind, d, *, near_m, road="", history=None, hours=24):
    bg, fg, title, sub = LEVELS[level]
    road_ref = is_road(d)
    if road_ref:
        sub = SUB_ROAD.get(level, sub)
    c = Card()

    # ---- แถบสถานะ ----
    c.d.rectangle(P(0, 0, W, 420), fill=bg)
    head = f"สถานี{d['name']}  ·  {d.get('river') or ''}".rstrip(" ·")
    c.text(60, 66, head, c.fit(head, 38, "ExtraBold", W - 120), "ExtraBold", fg)
    label = "  ·  ".join(x for x in ((KIND_ROAD if road_ref else KIND_LABEL).get(kind, ""), road) if x)
    c.text(60, 114, label, c.fit(label, 30, "Bold", W - 120), "Bold", fg)
    draw_icon(c, level, 180, 252, 108, bg, fg)
    c.text(336, 248, title, c.fit(title, 108, "ExtraBold", W - 336 - 50), "ExtraBold", fg)
    c.text(340, 322, sub, c.fit(sub, 40, "Bold", W - 340 - 50), "Bold", fg)

    # ---- ตัวเลข 3 ช่อง ----
    wl, prev, tb = d["wl"], d["prev"], d["to_ref"]
    tiles = [("ระดับน้ำ", "-" if wl is None else f"{wl:.2f}", "ม.รทก.", INK)]
    if tb is None:
        tiles.append((f"ห่าง{d['ref_name']}", "-", "ซม.", INK))
    elif tb > 0:
        tiles.append((d["under_label"], f"{tb * 100:.0f}", "ซม.", INK))
    else:
        tiles.append((d["over_label"], f"{-tb * 100:.0f}", "ซม.", RED))
    diff = None if wl is None or prev is None else round((wl - prev) * 100)
    if diff is None:
        tiles.append(("แนวโน้ม", "-", "", INK))
    else:
        tiles.append(("น้ำขึ้น" if diff > 0 else "น้ำลง" if diff < 0 else "ทรงตัว",
                      f"{abs(diff)}", "ซม.", RED if diff > 0 else GREEN if diff < 0 else MUTED))

    tw, gap, ty, th = (W - 120 - 48) / 3, 24, 452, 172
    for i, (lab, val, unit, col) in enumerate(tiles):
        x = 60 + i * (tw + gap)
        c.d.rounded_rectangle(P(x, ty, x + tw, ty + th), radius=22 * S, fill=TILE)
        c.text(x + 26, ty + 54, lab, 32, "Regular", MUTED)
        vx = x + 26
        if i == 2 and diff:     # ลูกศรขึ้น/ลง
            if diff > 0:
                pts = [(vx, ty + 136), (vx + 44, ty + 136), (vx + 22, ty + 92)]
            else:
                pts = [(vx, ty + 92), (vx + 44, ty + 92), (vx + 22, ty + 136)]
            c.d.polygon([tuple(P(*p)) for p in pts], fill=col)
            vx += 58
        # ย่อตัวเลขเมื่อยาวเกินช่อง (เช่น -0.78 หรือ 244)
        room = x + tw - 20 - vx - c.width(unit, 32, "Bold") - 10
        vs = c.fit(val, 76, "ExtraBold", room, min_size=48)
        c.text(vx, ty + 140, val, vs, "ExtraBold", col)
        c.text(vx + c.width(val, vs, "ExtraBold") + 10, ty + 140, unit, 32, "Bold", MUTED)

    # ---- กราฟย้อนหลัง ----
    c.text(60, 690, f"ระดับน้ำย้อนหลัง {hours} ชม.", 36, "Bold", INK)
    c.text(W - 60, 690, "หน่วย ม.รทก.", 26, "Regular", MUTED, "rs")
    draw_chart(c, history or [], d["ref"], near_m, (110, 730, W - 140, 1130),
               now_wl=wl, until=d.get("ts"), ref_name=d["ref_name"],
               river_bank=d["bank"] if road_ref else None)

    # ---- ข้อมูลสถานี ----
    c.d.line(P(60, 1210, W - 60, 1210), fill=LINE, width=2 * S)
    place = "  ·  ".join(x for x in (d.get("river"), d.get("amphoe") and f"อ.{d['amphoe']}",
                                     d.get("province") and f"จ.{d['province']}") if x)
    foot = f"สถานี{d['name']} ({d.get('code', '')})  ·  {place}"
    c.text(60, 1262, foot, c.fit(foot, 34, "Bold", W - 120), "Bold", INK)
    c.text(60, 1312, f"เวลาวัด {d['time'] or '-'}  ·  ข้อมูล thaiwater.net (สสน.)", 30, "Regular",
           RED if level == "stale" else MUTED)
    return c.png()


# ---------------- ภาพสรุปหลายสถานี ----------------
LEVEL_SHORT = {"ok": "ผ่านได้ปกติ", "risk": "ผ่านได้ แต่เสี่ยง", "avoid": "เลี่ยงเส้นทาง", "stale": "ข้อมูลไม่อัปเดต"}
LEVEL_COLOR = {"ok": GREEN, "risk": AMBER, "avoid": RED, "stale": GREY}
LEVEL_TEXT_COLOR = {"ok": GREEN, "risk": "#B06000", "avoid": RED, "stale": GREY}   # สีส้มเข้มขึ้นให้อ่านบนพื้นเทา
SEVERITY = {"avoid": 0, "risk": 1, "stale": 2, "ok": 3}


def render_summary(kind, items, *, title):
    """items: [{"d": ข้อมูลสถานี, "level": ok/risk/avoid/stale, "road": ชื่อจุด}] — เรียงสถานีที่อันตรายที่สุดไว้บนสุด"""
    items = sorted(items, key=lambda it: (SEVERITY[it["level"]],
                                          99 if it["d"]["to_ref"] is None else it["d"]["to_ref"]))
    rh, gap, top = 196, 20, 330
    c = Card(top + len(items) * (rh + gap) + 90)
    worst = items[0]["level"] if items else "ok"
    bg, fg = LEVELS[worst][0], LEVELS[worst][1]

    # ---- แถบหัว: สีตามสถานีที่แย่ที่สุด ----
    c.d.rectangle(P(0, 0, W, 290), fill=bg)
    c.text(60, 72, KIND_LABEL.get(kind, ""), 32, "Bold", fg)
    stamps = [it["d"]["ts"] for it in items if it["d"].get("ts")]
    if stamps:
        t = max(stamps)
        c.text(W - 60, 72, f"ข้อมูล {t:%H:%M} น. {t.day} {THAI_MONTHS[t.month - 1]}", 30, "Bold", fg, "rs")
    c.text(60, 168, title, c.fit(title, 76, "ExtraBold", W - 120), "ExtraBold", fg)
    counts = Counter(it["level"] for it in items)
    tally = "   ·   ".join(f"{LEVEL_SHORT[lv]} {counts[lv]}" for lv in ("avoid", "risk", "stale", "ok") if counts[lv])
    c.text(60, 240, tally, c.fit(tally, 36, "Bold", W - 120), "Bold", fg)

    # ---- แถวละสถานี ----
    for i, it in enumerate(items):
        d, lv, road = it["d"], it["level"], it.get("road") or ""
        y = top + i * (rh + gap)
        cy = y + rh / 2
        col = LEVEL_COLOR[lv]
        c.d.rounded_rectangle(P(60, y, W - 60, y + rh), radius=24 * S, fill=col)          # แถบสีซ้าย
        c.d.rounded_rectangle(P(76, y, W - 60, y + rh), radius=24 * S, fill=TILE,
                              corners=(False, True, True, False))
        draw_icon(c, lv, 146, cy, 42, INK if lv == "risk" else PAPER, col)

        tx, tmax = 214, 560
        name = f"สถานี{d['name']}"
        c.text(tx, y + 66, name, c.fit(name, 40, "Bold", tmax), "Bold", INK)
        place = "  ·  ".join(x for x in (d.get("river"), d.get("amphoe") and f"อ.{d['amphoe']}") if x)
        c.text(tx, y + 110, place, c.fit(place, 28, "Regular", tmax), "Regular", MUTED)
        status = LEVEL_SHORT[lv]
        if lv == "stale" and d.get("ts"):
            status += f" (ล่าสุด {d['ts']:%H:%M})"
        if road:
            status += f"  ·  {road}"
        c.text(tx, y + 160, status, c.fit(status, 30, "Bold", tmax), "Bold", LEVEL_TEXT_COLOR[lv])

        xr, tb = W - 92, d["to_ref"]
        if tb is not None:
            c.text(xr, y + 58, d["over_label"] if tb <= 0 else d["under_label"], 28, "Regular", MUTED, "rs")
            uw = c.width("ซม.", 30, "Bold")
            c.text(xr, y + 132, "ซม.", 30, "Bold", MUTED, "rs")
            c.text(xr - uw - 10, y + 132, f"{abs(tb) * 100:.0f}", 72, "ExtraBold", RED if tb <= 0 else INK, "rs")
        if d["wl"] is not None and d["prev"] is not None:
            diff = round((d["wl"] - d["prev"]) * 100)
            trend = f"น้ำขึ้น {diff} ซม." if diff > 0 else f"น้ำลง {-diff} ซม." if diff < 0 else "ทรงตัว"
            c.text(xr, y + 174, trend, 26, "Bold", RED if diff > 0 else GREEN if diff < 0 else MUTED, "rs")

    c.text(60, c.h - 40, "ข้อมูล thaiwater.net (สสน.)  ·  กดปุ่มชื่อสถานีด้านล่างเพื่อดูกราฟ", 28, "Regular", MUTED)
    return c.png()
