# คู่มือ Deploy — BPK Water Alert บน Linux Server (Docker)

ระบบนี้ทำงานในรูปแบบ Docker container ตัวเดียว ทุก 10 นาที container จะเช็กระดับน้ำสถานีบางปะกง (BPK001) จาก thaiwater.net
ถ้าสถานะเปลี่ยน จะส่งภาพการ์ดพร้อมกราฟเข้ากลุ่ม Telegram

```text
┌─────────────── Linux Server ───────────────┐
│  /opt/bpk-alert/                           │
│   ├─ .env        ← token / chat id / เกณฑ์  │──mount (อ่านอย่างเดียว)──┐
│   ├─ data/       ← ไฟล์สถานะ (กันแจ้งซ้ำ)    │──mount──────────────────┤
│   └─ โค้ด + Dockerfile                      │                         ▼
│                                            │   container: bpk-bank-alert
│                                            │   วนรอบทุก 600 วินาที
└────────────────────────────────────────────┘        │            │
                              HTTPS ขาออก ──────────────┘            │
                     api-v3.thaiwater.net (ดึงข้อมูล)    api.telegram.org (ส่งแจ้งเตือน)
```

---

## สารบัญ

1. [สิ่งที่ต้องเตรียม](#1-สิ่งที่ต้องเตรียม)
2. [ติดตั้ง Docker บนเซิร์ฟเวอร์](#2-ติดตั้ง-docker-บนเซิร์ฟเวอร์)
3. [ตรวจว่าเซิร์ฟเวอร์ออกอินเทอร์เน็ตได้](#3-ตรวจว่าเซิร์ฟเวอร์ออกอินเทอร์เน็ตได้)
4. [คัดลอกไฟล์จาก Windows ขึ้นเซิร์ฟเวอร์](#4-คัดลอกไฟล์จาก-windows-ขึ้นเซิร์ฟเวอร์)
5. [ตั้งสิทธิ์ไฟล์ (สำคัญ)](#5-ตั้งสิทธิ์ไฟล์-สำคัญ)
6. [Build และทดสอบ](#6-build-และทดสอบ)
7. [เริ่มระบบจริง](#7-เริ่มระบบจริง)
8. [ตรวจหลัง Deploy (Checklist)](#8-ตรวจหลัง-deploy-checklist)
9. [งานประจำ: ดู log / หยุด / เริ่มใหม่](#9-งานประจำ-ดู-log--หยุด--เริ่มใหม่)
10. [แก้ค่าตั้งค่า](#10-แก้ค่าตั้งค่า)
11. [อัปเดตโค้ดเวอร์ชันใหม่ และย้อนกลับ](#11-อัปเดตโค้ดเวอร์ชันใหม่-และย้อนกลับ)
12. [แก้ปัญหาที่พบบ่อย](#12-แก้ปัญหาที่พบบ่อย)
13. [ความปลอดภัย](#13-ความปลอดภัย)
14. [ถอนการติดตั้ง](#14-ถอนการติดตั้ง)

---

## 1. สิ่งที่ต้องเตรียม

| รายการ | รายละเอียด |
| --- | --- |
| Linux server | Ubuntu 22.04/24.04, Debian 12 หรือ RHEL/Rocky/Alma 8–9 ใช้ทรัพยากรน้อยมาก: RAM ~100 MB, ดิสก์ ~300 MB **ต้องออกเน็ตด้วย IP ในประเทศไทย** เพราะ thaiwater.net ไม่ตอบ IP ต่างประเทศ (ทดสอบแล้ว: DigitalOcean Singapore เชื่อมต่อไม่ได้) |
| สิทธิ์ | บัญชีที่ใช้ `sudo` ได้ และ SSH เข้าเซิร์ฟเวอร์ได้จากเครื่อง Windows |
| อินเทอร์เน็ตขาออก | HTTPS (443) ไปที่ `api-v3.thaiwater.net`, `api.telegram.org` และตอน build ต้องออกไป `registry-1.docker.io`, `deb.debian.org`, `pypi.org` |
| Telegram | Bot token จาก @BotFather และ Chat ID ของกลุ่ม ต้องเพิ่มบอท **@bpk_bank_alert_bot** เข้ากลุ่มแล้ว |
| ไฟล์โปรเจกต์ | โฟลเดอร์ `D:\Awarasoft\Water_Level` บนเครื่อง Windows ที่ทดสอบแล้ว |

> **อย่ารันพร้อมกันสองที่** ถ้าเคยตั้ง Task Scheduler บน Windows ให้รันสคริปต์นี้ ให้ปิดก่อน ไม่งั้นกลุ่มจะได้รับข้อความซ้ำ

---

## 2. ติดตั้ง Docker บนเซิร์ฟเวอร์

SSH เข้าเซิร์ฟเวอร์ก่อน:

```bash
ssh user@server
```

ถ้ามี Docker อยู่แล้ว ให้ข้ามไปขั้นตอน 2.3

### 2.1 Ubuntu / Debian

```bash
curl -fsSL https://get.docker.com | sudo sh
```

### 2.2 RHEL / Rocky / Alma

```bash
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

### 2.3 เปิดบริการ และให้ user ใช้ docker ได้โดยไม่ต้อง sudo

```bash
sudo systemctl enable --now docker      # ให้ Docker เริ่มเองทุกครั้งที่บูตเครื่อง
sudo usermod -aG docker $USER           # เพิ่มตัวเองเข้ากลุ่ม docker
exit                                    # ออกแล้ว SSH เข้าใหม่ สิทธิ์กลุ่มจึงจะมีผล
```

ตรวจผล:

```bash
docker --version
docker compose version                  # ต้องเป็น v2 ขึ้นไป
docker run --rm hello-world             # ต้องเห็นข้อความ "Hello from Docker!"
```

---

## 3. ตรวจว่าเซิร์ฟเวอร์ออกอินเทอร์เน็ตได้

```bash
curl -sS -o /dev/null -w "thaiwater: %{http_code}\n" \
  "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"
curl -sS -o /dev/null -w "telegram:  %{http_code}\n" https://api.telegram.org
```

| ผลที่ได้ | ความหมาย |
| --- | --- |
| `thaiwater: 200` และ `telegram: 302` (หรือ 200) | ใช้ได้ ไปขั้นตอนต่อไป |
| `Could not resolve host` | DNS ของเซิร์ฟเวอร์ใช้ไม่ได้ ติดต่อทีม network |
| ค้างนานแล้ว timeout | firewall บล็อก หรือต้องผ่าน proxy ดู [12.7](#127-ต้องออกเน็ตผ่าน-proxy) |

---

## 4. คัดลอกไฟล์จาก Windows ขึ้นเซิร์ฟเวอร์

### 4.1 ไฟล์ที่ต้องส่งขึ้น

| ไฟล์ / โฟลเดอร์ | ส่ง? | หมายเหตุ |
| --- | :---: | --- |
| `bpk_bank_alert.py` | ✅ | สคริปต์หลัก |
| `alert_card.py` | ✅ | ตัววาดภาพการ์ด + กราฟ |
| `fonts/` | ✅ | ฟอนต์ Sarabun ต้องส่งทั้งโฟลเดอร์ ไม่งั้นภาพไม่มีตัวหนังสือไทย |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore` | ✅ | |
| `.env.example` | ✅ | ไว้เทียบค่าตั้งค่า |
| `.env` | ✅ | มี token ต้องส่งด้วยช่องทางที่ปลอดภัย (scp) เท่านั้น |
| `bpk_alert_state.json`, `data/` | ❌ | สถานะของเครื่องทดสอบ ให้เซิร์ฟเวอร์เริ่มนับใหม่ |
| `preview/`, `__pycache__/` | ❌ | ไฟล์ชั่วคราว |

### 4.2 สร้างโฟลเดอร์ปลายทางบนเซิร์ฟเวอร์

เปิด **PowerShell** บนเครื่อง Windows:

```powershell
ssh -t user@server 'sudo mkdir -p /opt/bpk-alert && sudo chown $(id -u):$(id -g) /opt/bpk-alert'
```

> ใช้ `'...'` (single quote) เพื่อให้ `$(id -u)` ไปทำงานบนเซิร์ฟเวอร์ ไม่ใช่บน PowerShell

### 4.3 คัดลอกไฟล์

```powershell
cd D:\Awarasoft\Water_Level
scp -r bpk_bank_alert.py alert_card.py fonts Dockerfile docker-compose.yml .dockerignore .env.example .env user@server:/opt/bpk-alert/
```

ถ้าไม่สะดวกใช้คำสั่ง ใช้โปรแกรม **WinSCP** ลากไฟล์ตามตารางข้อ 4.1 ไปไว้ที่ `/opt/bpk-alert/` ก็ได้
ถ้ามองไม่เห็นไฟล์ `.env` ใน WinSCP ให้เปิด *Options → Preferences → Panels → Show hidden files*

### 4.4 ตรวจบนเซิร์ฟเวอร์

```bash
cd /opt/bpk-alert
ls -la
ls fonts            # ต้องมี Sarabun-Regular.ttf, Sarabun-Bold.ttf, Sarabun-ExtraBold.ttf
```

---

## 5. ตั้งสิทธิ์ไฟล์ (สำคัญ)

ในตัว container ระบบรันด้วย user **uid 1000** (ไม่ใช่ root) uid นี้จึงต้องเขียนโฟลเดอร์ `data/` ได้ และอ่าน `.env` ได้

```bash
cd /opt/bpk-alert
id -u                       # ดูว่า user ของคุณมี uid อะไร
```

**กรณี A: ได้ `1000`** (พบบ่อยที่สุด user คนแรกของเครื่องมักเป็น 1000)

```bash
mkdir -p data
chmod 600 .env              # ให้อ่านได้เฉพาะเจ้าของ
```

**กรณี B: ได้เลขอื่น**

```bash
mkdir -p data
sudo chown 1000:1000 data .env
sudo chmod 600 .env
# จากนี้ถ้าจะแก้ .env ต้องใช้ sudo เช่น  sudo nano .env
```

> ⚠️ **ห้ามข้ามขั้นนี้ ถ้าตั้งสิทธิ์ผิดจะเกิดปัญหาต่อไปนี้**
> - ถ้าไม่สร้าง `data/` เอง Docker จะสร้างให้เป็นของ root แล้ว container เขียนไฟล์สถานะไม่ได้ ตอนนี้สคริปต์จะหยุดพร้อมแจ้ง `เขียนไฟล์สถานะ ... ไม่ได้` เพื่อกันไม่ให้แจ้งเตือนซ้ำทุกรอบ แต่ระบบก็จะไม่ทำงานจนกว่าจะแก้สิทธิ์
> - ถ้า uid 1000 อ่าน `.env` ไม่ได้ สคริปต์จะไม่เห็น token แล้วขึ้น `!! ยังไม่ได้ตั้ง TELEGRAM_BOT_TOKEN`

**เฉพาะ RHEL / Rocky / Alma ที่เปิด SELinux** (ตรวจด้วย `getenforce` ถ้าได้ `Enforcing` ให้ทำข้อนี้):
แก้ `docker-compose.yml` เติม `:z` ท้าย volume ทั้งสองบรรทัด ไม่งั้น container จะอ่านหรือเขียนไฟล์ไม่ได้

```yaml
    volumes:
      - ./.env:/app/.env:ro,z
      - ./data:/app/data:z
```

---

## 6. Build และทดสอบ

### 6.1 Build image

```bash
cd /opt/bpk-alert
docker compose build
```

ครั้งแรกใช้เวลา 1–3 นาที ต้องเห็นบรรทัดสุดท้ายเป็น `Image bpk-bank-alert:latest Built`

### 6.2 ตรวจว่าภาษาไทยในภาพจะแสดงถูก

```bash
docker compose run --rm bpk-alert python -c "from PIL import features; print('raqm:', features.check('raqm'))"
```

ต้องได้ `raqm: True` ถ้าได้ `False` สระและวรรณยุกต์ในภาพจะซ้อนกันเพี้ยน ดู [12.5](#125-ภาพมีสระวรรณยุกต์ไทยเพี้ยน)

### 6.3 ตรวจเวลา

```bash
docker compose run --rm bpk-alert date
```

ต้องเป็นเวลาไทยและลงท้ายด้วย `+07` (container ตั้ง `TZ=Asia/Bangkok` เอง ไม่ขึ้นกับ timezone ของเซิร์ฟเวอร์)

### 6.4 ส่งข้อความทดสอบเข้ากลุ่ม

```bash
docker compose run --rm bpk-alert python bpk_bank_alert.py --test
```

- บนจอจะแสดงข้อความทดสอบ
- ในกลุ่ม Telegram ต้องได้**ภาพการ์ด "ทดสอบระบบแจ้งเตือน" พร้อมกราฟ**
- ถ้าได้เป็นข้อความอย่างเดียวไม่มีภาพ ดู [12.4](#124-ได้ข้อความแต่ไม่มีภาพ)
- ถ้าขึ้น `Telegram error` ดู [12.1](#121-telegram-error-400-chat-not-found)–[12.2](#122-telegram-error-401-unauthorized)

### 6.5 (ไม่บังคับ) ดูภาพตัวอย่างครบทุกสถานะ

```bash
mkdir -p preview && [ "$(id -u)" = 1000 ] || sudo chown 1000:1000 preview
docker compose run --rm -v ./preview:/app/preview bpk-alert python bpk_bank_alert.py --preview
```

ได้ไฟล์ `ok.png`, `risk.png`, `avoid.png`, `stale.png` ในโฟลเดอร์ `preview/` ดึงกลับมาดูบน Windows ได้ด้วย
`scp -r user@server:/opt/bpk-alert/preview .` (ขั้นนี้ไม่ส่งอะไรเข้ากลุ่ม)

---

## 7. เริ่มระบบจริง

```bash
cd /opt/bpk-alert
docker compose up -d
```

- `-d` = รันเบื้องหลัง ปิดหน้าต่าง SSH ได้
- ตั้ง `restart: unless-stopped` ไว้แล้ว ถ้ารีบูตเซิร์ฟเวอร์หรือ Docker restart ระบบจะกลับมารันเอง (ต้องเปิด `systemctl enable docker` ตามข้อ 2.3)
- **รอบแรก**ระบบจะบันทึกสถานะเริ่มต้นเฉย ๆ และจะไม่ส่งอะไรถ้าน้ำปกติ จะแจ้งทันทีเฉพาะกรณีน้ำใกล้หรือเกินตลิ่งอยู่แล้ว

---

### 7.1 เรียกดูสถานะเองจาก Telegram

`docker compose up -d` จะเปิด container 2 ตัว:

| container | หน้าที่ |
| --- | --- |
| `bpk-bank-alert` | เช็กทุก 10 นาที แล้วแจ้งเตือนเมื่อสถานะเปลี่ยน |
| `bpk-bank-alert-bot` | รอรับคำสั่งจาก Telegram ตลอดเวลา |

ในกลุ่ม Telegram เรียกดูสถานะได้ 2 วิธี:

- กดปุ่ม **🔄 ดูสถานะล่าสุด** ใต้ภาพแจ้งเตือนภาพไหนก็ได้
- แตะปุ่ม **/** ข้างช่องพิมพ์ แล้วเลือก **/status** (หรือพิมพ์ `/status`)

บอทจะส่งภาพการ์ด "สถานะ ณ ตอนนี้" กลับมาภายในไม่กี่วินาที

- ถ้ามีคนกดซ้ำภายใน 60 วินาที บอทจะส่งภาพเดิม ไม่ดึงข้อมูลจาก thaiwater ซ้ำ
- บอทตอบเฉพาะแชตที่อยู่ใน `TELEGRAM_CHAT_ID` คนนอกที่ทักบอทจะไม่ได้รับข้อมูล
- ถ้าเพิ่งเพิ่มบอทเข้ากลุ่มแล้วเมนู `/` ยังไม่ขึ้น ให้ปิดแล้วเปิดแชตใหม่ (Telegram อัปเดตเมนูช้า)

> ⚠️ **รันตัวบอทได้ที่เดียวเท่านั้น** ถ้ามีบอท token เดียวกันรันอยู่ที่อื่นด้วย (เช่น เครื่องทดสอบ) log จะขึ้น `409 Conflict` และกดแล้วบางครั้งไม่ตอบ

## 8. ตรวจหลัง Deploy (Checklist)

```bash
docker compose ps                       # ต้องเห็น 2 ตัว STATUS เป็น Up ทั้งคู่
docker compose logs --tail 20           # ดูผลรอบล่าสุด
cat data/bpk_alert_state.json           # ต้องมีไฟล์นี้หลังรอบแรก
```

log ที่ปกติจะออกมาหนึ่งบรรทัดต่อรอบ (ทุก 10 นาที):

```text
2026-10-05 16:07 1.02 ม. | ห่างตลิ่ง 65 ซม. | สถานะ normal | ส่ง 0 ข้อความ
```

- [ ] `docker compose ps` ขึ้น `Up` ทั้ง `bpk-bank-alert` และ `bpk-bank-alert-bot`
- [ ] พิมพ์ `/status` ในกลุ่ม แล้วได้ภาพสถานะกลับมา
- [ ] ข้อความทดสอบในข้อ 6.4 เข้ากลุ่มเป็นภาพ
- [ ] มีไฟล์ `data/bpk_alert_state.json` และค่า `checked` เป็นเวลาล่าสุด
- [ ] รอ 10 นาทีแล้วดู log อีกครั้ง ต้องมีบรรทัดใหม่เพิ่ม
- [ ] ทดสอบรีบูต (ถ้าทำได้): `sudo reboot` แล้วเข้ามาดู `docker compose ps` ต้องกลับมา `Up` เอง
- [ ] ปิด Task Scheduler บน Windows แล้ว (ถ้าเคยตั้งไว้)

---

## 9. งานประจำ: ดู log / หยุด / เริ่มใหม่

ทุกคำสั่งต้องรันในโฟลเดอร์ `/opt/bpk-alert`

| ต้องการ | คำสั่ง |
| --- | --- |
| ดูสถานะ | `docker compose ps` |
| ดู log ล่าสุด | `docker compose logs --tail 50` |
| ดู log เฉพาะตัวเช็ก / ตัวบอท | `docker compose logs --tail 50 bpk-alert` / `docker compose logs --tail 50 bpk-bot` |
| ดู log แบบสด (Ctrl+C เพื่อออก) | `docker compose logs -f` |
| ดูเฉพาะ error | `docker compose logs \| grep -iE "error\|ไม่สำเร็จ\|Traceback"` |
| ดูสถานะล่าสุดที่บันทึก | `cat data/bpk_alert_state.json` |
| ส่งข้อความทดสอบ | `docker compose run --rm bpk-alert python bpk_bank_alert.py --test` |
| หยุดชั่วคราว | `docker compose stop` |
| เริ่มต่อ | `docker compose start` |
| เริ่มใหม่ (เช่น หลังแก้ `.env`) | `docker compose restart` |
| หยุดและลบ container (ไฟล์ใน `data/` ยังอยู่) | `docker compose down` |

log ถูกจำกัดขนาดไว้ที่ 5 MB × 3 ไฟล์ จึงไม่กินดิสก์จนเต็ม

---

## 10. แก้ค่าตั้งค่า

### 10.1 ค่าใน `.env`

```bash
nano .env               # กรณี B ในข้อ 5 ใช้: sudo nano .env
docker compose restart
```

| ค่า | ค่าเริ่มต้น | ความหมาย |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | – | Token บอท |
| `TELEGRAM_CHAT_ID` | – | Chat ID ของกลุ่ม ถ้ามีหลายกลุ่มให้คั่นด้วย `,` |
| `STATION_CODE` | `BPK001` | รหัสสถานี |
| `NEAR_M` | `0.30` | ห่างตลิ่งน้อยกว่านี้ (เมตร) ถือว่า **เสี่ยง** |
| `HYSTERESIS_M` | `0.05` | ต้องลดลงอีกเท่านี้จึงนับว่ากลับปกติ (กันแจ้งเตือนสลับไปมา) |
| `REPEAT_STEP_M` | `0.10` | ระหว่างอยู่ในสถานะเตือน ถ้าน้ำขึ้นอีกเท่านี้จะแจ้งซ้ำ |
| `STALE_H` | `2` | สถานีไม่ส่งค่าใหม่เกินกี่ชั่วโมงจึงแจ้งว่าข้อมูลไม่อัปเดต |
| `ALERT_IMAGE` | `1` | `1` = ส่งเป็นภาพ, `0` = ส่งข้อความอย่างเดียว |
| `GRAPH_HOURS` | `24` | กราฟย้อนหลังกี่ชั่วโมง |
| `ROAD_NAME` | (ว่าง) | ชื่อจุดหรือถนนที่ต้องระวัง จะแสดงบนแถบสีด้านบนของภาพ |

> แนะนำให้ `restart` ทุกครั้งหลังแก้ `.env` เพราะ editor บางตัว (เช่น vim) บันทึกเป็นไฟล์ใหม่ แล้ว container จะยังเห็นไฟล์เดิมอยู่

### 10.2 ค่าใน `docker-compose.yml`

| ค่า | ค่าเริ่มต้น | ความหมาย |
| --- | --- | --- |
| `INTERVAL_SEC` | `600` | เช็กทุกกี่วินาที (ไม่ควรต่ำกว่า 300 เพราะสถานีส่งค่าทุก 10 นาที) |
| `STATE_FILE` | `data/bpk_alert_state.json` | ไฟล์สถานะ ไม่ต้องแก้ |

หลังแก้ไฟล์นี้ต้องใช้ `docker compose up -d` (`restart` อย่างเดียวจะไม่อ่านค่าใหม่)

---

## 11. อัปเดตโค้ดเวอร์ชันใหม่ และย้อนกลับ

### 11.1 อัปเดต

```bash
# บนเซิร์ฟเวอร์: เก็บ image เดิมไว้เผื่อย้อนกลับ
cd /opt/bpk-alert
docker tag bpk-bank-alert:latest bpk-bank-alert:prev
```

```powershell
# บน Windows: ส่งเฉพาะไฟล์ที่เปลี่ยน (ปกติไม่ต้องส่ง .env ซ้ำ)
cd D:\Awarasoft\Water_Level
scp bpk_bank_alert.py alert_card.py Dockerfile docker-compose.yml user@server:/opt/bpk-alert/
```

```bash
# บนเซิร์ฟเวอร์: build ใหม่และสลับ container
cd /opt/bpk-alert
docker compose up -d --build
docker compose run --rm bpk-alert python bpk_bank_alert.py --test   # ยืนยันว่ายังส่งได้
docker compose logs --tail 5
```

ไฟล์สถานะใน `data/` ยังอยู่ อัปเดตแล้วจึงไม่แจ้งเตือนซ้ำ

### 11.2 ย้อนกลับเวอร์ชันเดิม

```bash
cd /opt/bpk-alert
docker tag bpk-bank-alert:prev bpk-bank-alert:latest
docker compose up -d --no-build --force-recreate
```

### 11.3 ล้าง image เก่าที่ไม่ใช้ (นาน ๆ ครั้ง)

```bash
docker image prune -f
```

---

## 12. แก้ปัญหาที่พบบ่อย

เริ่มจากดู log ทุกครั้ง: `docker compose logs --tail 50`

### 12.1 `Telegram error: 400 ... chat not found`
บอทไม่ได้อยู่ในกลุ่ม หรือ Chat ID ผิด
- เพิ่ม **@bpk_bank_alert_bot** เข้ากลุ่ม แล้วพิมพ์ `/start@bpk_bank_alert_bot` ในกลุ่ม
- เปิด `https://api.telegram.org/bot<TOKEN>/getUpdates` ดูค่า `"chat":{"id": ...}` แล้วนำไปใส่ `TELEGRAM_CHAT_ID`
- ถ้าเห็น `migrate_to_chat_id` แปลว่ากลุ่มถูกอัปเกรดเป็น supergroup ให้ใช้ ID ใหม่ที่ขึ้นต้นด้วย `-100`

### 12.2 `Telegram error: 401 Unauthorized`
Token ผิดหรือถูกยกเลิก ขอ token ใหม่จาก @BotFather แล้วแก้ใน `.env`

### 12.3 `!! ยังไม่ได้ตั้ง TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID`
container มองไม่เห็นหรืออ่าน `.env` ไม่ได้
- ตรวจว่ามีไฟล์จริง: `ls -la /opt/bpk-alert/.env`
- ตรวจสิทธิ์ตามข้อ 5: uid 1000 ต้องอ่านได้
- บน SELinux ให้เติม `:z` ตามข้อ 5
- ทดสอบ: `docker compose run --rm bpk-alert sh -c "test -r .env && echo OK || echo NO"` ต้องได้ `OK` ถ้าได้ `NO` แปลว่าติดเรื่องสิทธิ์

### 12.4 ได้ข้อความแต่ไม่มีภาพ
ใน log จะมีบรรทัด `สร้างภาพไม่สำเร็จ ส่งเป็นข้อความแทน: ...` ระบบออกแบบให้ยังส่งข้อความได้แม้ภาพพัง ดูสาเหตุท้ายบรรทัด:
- `cannot open resource` / ไม่พบไฟล์ฟอนต์: ลืมส่งโฟลเดอร์ `fonts/` ให้ส่งขึ้นไปแล้ว `docker compose up -d --build`
- `ดึงกราฟย้อนหลังไม่สำเร็จ`: API กราฟของ thaiwater ล่มชั่วคราว ภาพยังส่งได้แต่ช่องกราฟจะเขียนว่า "ไม่มีข้อมูลกราฟย้อนหลัง"

### 12.5 ภาพมีสระ/วรรณยุกต์ไทยเพี้ยน
ข้อ 6.2 ได้ `raqm: False` แปลว่าใน image ไม่มี `libfribidi0` ตรวจว่า Dockerfile มีบรรทัด `apt-get install ... libfribidi0` แล้ว build ใหม่ด้วย `docker compose build --no-cache`

### 12.6 `เขียนไฟล์สถานะ data/bpk_alert_state.json ไม่ได้ (Permission denied)`
โฟลเดอร์ `data/` ไม่ใช่ของ uid 1000:
```bash
sudo chown -R 1000:1000 /opt/bpk-alert/data
```
(สคริปต์หยุดก่อนส่งข้อความ เพื่อไม่ให้แจ้งเตือนซ้ำทุกรอบ)

### 12.7 ต้องออกเน็ตผ่าน proxy
ตอนรัน: เพิ่มใน `docker-compose.yml` ใต้ `environment:`
```yaml
      HTTPS_PROXY: http://proxy.company.local:8080
      HTTP_PROXY: http://proxy.company.local:8080
```
ตอน build (ดึง image / apt / pip):
```bash
docker compose build --build-arg HTTPS_PROXY=http://proxy.company.local:8080 \
                     --build-arg HTTP_PROXY=http://proxy.company.local:8080
```
ถ้า `docker pull` ไม่ผ่าน ต้องตั้ง proxy ให้ Docker daemon ด้วย ดูคู่มือ Docker หัวข้อ *Configure the daemon to use a proxy*

### 12.8 ดึงข้อมูล thaiwater ไม่ได้ (`ConnectionError`, `Timeout`, `ไม่พบสถานี BPK001`)
- **ถ้าค้างตั้งแต่ขั้น connect แต่ Telegram และ Google ผ่าน** ให้ดู IP ขาออกด้วย `curl -sS https://ifconfig.me` ถ้าไม่ใช่ IP ไทย (เช่น cloud สิงคโปร์) แปลว่า thaiwater บล็อก ต้องย้ายไปรันบนเครื่องที่ออกเน็ตด้วย IP ไทย
- ทดสอบตามข้อ 3
- ถ้าเป็นชั่วคราว รอบถัดไป (10 นาที) จะลองใหม่เอง ไม่ต้องทำอะไร
- ถ้าขึ้น `ไม่พบสถานี` ต่อเนื่องหลายชั่วโมง อาจเป็นเพราะ สสน. เปลี่ยนรหัสสถานีหรือรูปแบบ API ให้ตรวจที่ https://www.thaiwater.net/water/wl

### 12.9 ได้แจ้งเตือน "ข้อมูลไม่อัปเดต" (การ์ดสีเทา)
สถานีไม่ส่งค่าใหม่เกิน `STALE_H` ชั่วโมง เป็นปัญหาฝั่งสถานี ไม่ใช่ระบบเรา ระหว่างนั้นให้ตรวจสถานการณ์หน้างานเอง เมื่อสถานีกลับมาส่งค่า ระบบจะทำงานต่อเอง

### 12.10 container ขึ้น `Restarting` วนไปเรื่อย ๆ
ตามปกติไม่ควรเกิด เพราะ error ในแต่ละรอบไม่ทำให้ container หยุด ถ้าเกิด ให้ดู `docker compose logs --tail 100` แล้วตรวจว่าไฟล์ในข้อ 4.1 ครบ

### 12.10.1 กด `/status` หรือปุ่ม 🔄 แล้วไม่มีอะไรตอบ

- `docker compose ps` ต้องเห็น `bpk-bank-alert-bot` เป็น `Up`
- ดู log เฉพาะบอท: `docker compose logs --tail 30 bpk-bot`
  - `409 Conflict`: มีบอท token เดียวกันรันอยู่อีกที่ ให้ปิดตัวอื่นให้เหลือที่เดียว
  - `ปฏิเสธคำสั่งจากแชต ...`: แชตนั้นไม่อยู่ใน `TELEGRAM_CHAT_ID` ถ้าตั้งใจให้ใช้ได้ ให้เพิ่มโดยคั่นด้วย `,`
  - `ดึงสถานะไม่สำเร็จ`: เรียก thaiwater ไม่ได้ ดู [12.8](#128-ดึงข้อมูล-thaiwater-ไม่ได้-connectionerror-timeout-ไม่พบสถานี-bpk001)

### 12.11 อยากเริ่มนับสถานะใหม่
```bash
docker compose stop
rm data/bpk_alert_state.json
docker compose start
```

---

## 13. ความปลอดภัย

- `.env` มี Bot token ห้ามนำขึ้น Git, file share หรือส่งทางแชต ระบบตั้ง `.gitignore` และ `.dockerignore` กันไว้แล้ว และ token จะไม่ถูกฝังอยู่ใน image
- ถ้าสงสัยว่า token หลุด ให้พิมพ์ `/revoke` กับ @BotFather เพื่อออก token ใหม่ แล้วแก้ `.env` และ `docker compose restart`
- container รันด้วย user ธรรมดา (uid 1000) ไม่ใช่ root ไม่เปิดพอร์ตใด ๆ และมีแค่การเชื่อมต่อขาออก
- ถ้ามีคนส่ง error ที่มี URL `https://api.telegram.org/bot...` มาในแชต ให้ปิดส่วน token ก่อนทุกครั้ง

---

## 14. ถอนการติดตั้ง

```bash
cd /opt/bpk-alert
docker compose down
docker image rm bpk-bank-alert:latest bpk-bank-alert:prev 2>/dev/null
cd / && sudo rm -rf /opt/bpk-alert      # ⚠️ ลบ .env และไฟล์สถานะด้วย
```
