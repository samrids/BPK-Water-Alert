FROM python:3.12-slim

# tzdata: ให้ datetime.now() เป็นเวลาไทย (ใช้เทียบกับเวลาวัดของสถานีตอนเช็กข้อมูลค้าง)
# libfribidi0: Pillow ต้องใช้คู่กับ raqm เพื่อจัดสระ/วรรณยุกต์ไทยในภาพให้ถูกตำแหน่ง
RUN apt-get update && apt-get install -y --no-install-recommends tzdata libfribidi0 \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Bangkok \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app
RUN pip install --no-cache-dir requests pillow
COPY fonts/ fonts/
COPY bpk_bank_alert.py alert_card.py ./

# ไม่ใช้ root
RUN useradd -r -u 1000 app && mkdir -p /app/data && chown app /app/data
USER app

# เช็กทุก INTERVAL_SEC วินาที — รอบไหน error ก็แค่ log แล้ววนต่อ
CMD ["sh", "-c", "while true; do python bpk_bank_alert.py; sleep ${INTERVAL_SEC:-600}; done"]
