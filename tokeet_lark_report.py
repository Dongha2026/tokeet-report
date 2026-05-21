#!/usr/bin/env python3
"""
Tokeet Data Feed -> Lark Webhook | Bao cao tuan Villa Class6
Chay tu dong moi thu 2 - setup: python3 tokeet_lark_report.py --setup-cron
"""

import csv, io, json, sys, requests, subprocess
from datetime import datetime, timedelta
from pathlib import Path

# ── Cau hinh ────────────────────────────────────────────────────────────────
INQUIRY_FEED = (
    "https://datafeed.tokeet.com/v1/inquiry/1665413246.9677"
    "/93eb68b6-4444-4ed1-9435-4089e0ce6c53-tk2"
    "/4828a160-50c2-4dbc-a9c4-647a942a5178-tk2"
    "/1779334855"
)

LARK_WEBHOOK = "https://open.larksuite.com/open-apis/bot/v2/hook/ef81745e-ce49-4f64-9fc3-b4ba5fc5da99"

# ── Thoi gian: tuan truoc ────────────────────────────────────────────────────
def last_week_range():
    today    = datetime.now()
    this_mon = today - timedelta(days=today.weekday())
    last_mon = (this_mon - timedelta(days=7)).replace(hour=0,  minute=0, second=0, microsecond=0)
    last_sun = (this_mon - timedelta(seconds=1)).replace(hour=23, minute=59, second=59)
    return last_mon, last_sun

# ── Tokeet Data Feed ─────────────────────────────────────────────────────────
def fetch_bookings(d_from):
    start_of_year = d_from.strftime("%Y-01-01")
    url = f"{INQUIRY_FEED}?start={start_of_year}"
    log(f"Downloading feed (start={start_of_year})...")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    content = r.text
    if content.strip().startswith("{"):
        log(f"Feed JSON: {content[:100]}")
        return []
    rows = list(csv.DictReader(io.StringIO(content)))
    log(f"Feed: {len(rows)} records")
    return rows

def parse_date(v):
    if not v: return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try: return datetime.strptime(str(v)[:19], fmt)
        except: pass
    return None

def normalize_channel(src):
    s = (src or "").lower()
    if "airbnb"      in s: return "Airbnb"
    if "booking"     in s: return "Booking.com"
    if "tripadvisor" in s or s == "ta": return "TripAdvisor"
    if "agoda"       in s: return "Agoda"
    if "direct"      in s or s in ("", "tokeet"): return "Trực tiếp"
    return src or "Khác"

def normalize_villa(rental):
    if not rental: return "Khác"
    # "HA1 - The Country House - 7BR" → "The Country House"
    # Strip leading "HAx - " prefix and trailing " - xBR" suffix
    parts = rental.split(" - ")
    if len(parts) >= 3:
        return " - ".join(parts[1:-1]).strip()
    if len(parts) == 2:
        return parts[1].strip()
    return rental.strip()

def aggregate(bookings, d_from, d_to):
    channels = {}   # channel -> {bookings, revenue, cancels, cancel_rev}
    villas   = {}   # villa -> channel -> count
    totals   = {"revenue": 0, "cancel_rev": 0, "bookings": 0, "cancels": 0}
    matched  = 0

    for b in bookings:
        raw_date = b.get("Received") or b.get("Booked") or ""
        d = parse_date(raw_date)
        if not d or d < d_from or d > d_to:
            continue
        matched += 1

        status  = (b.get("Booking Status") or "").lower()
        channel = normalize_channel(b.get("Source") or "")
        villa   = normalize_villa(b.get("Rental") or "")
        cost_s  = str(b.get("Total Cost") or "0").replace(" VND","").replace(",","").strip()
        try:    cost = float(cost_s)
        except: cost = 0.0

        ch = channels.setdefault(channel, {"bookings":0,"revenue":0,"cancels":0,"cancel_rev":0})

        if "cancel" in status:
            ch["cancels"]      += 1; ch["cancel_rev"]    += cost
            totals["cancels"]  += 1; totals["cancel_rev"] += cost
        elif status not in ("not booked","unread","inquiry","new",""):
            ch["bookings"]     += 1; ch["revenue"]      += cost
            totals["bookings"] += 1; totals["revenue"]   += cost
            # villa breakdown (confirmed only)
            villas.setdefault(villa, {})
            villas[villa][channel] = villas[villa].get(channel, 0) + 1

    log(f"Khop {matched} bookings | Confirmed: {totals['bookings']} | Huy: {totals['cancels']}")
    return totals, channels, villas

# ── Lark Webhook ─────────────────────────────────────────────────────────────
def send_lark(week_label, totals, channels, villas):
    # ── Phan tong hop ──
    lines = [
        f"📊 Báo cáo {week_label}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"✅ Tổng booking : {totals['bookings']} | {totals['revenue']:>15,.0f} VND",
        f"❌ Huỷ          : {totals['cancels']} | {totals['cancel_rev']:>15,.0f} VND",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📡 Theo kênh:",
    ]

    for ch, d in sorted(channels.items(), key=lambda x: -x[1]["revenue"]):
        if d["bookings"] > 0:
            lines.append(f"   {ch:<15} {d['bookings']:>2} bk | {d['revenue']:>15,.0f}đ")

    if villas:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🏠 Theo villa:")
        # Sort villa by total bookings desc
        villa_sorted = sorted(villas.items(), key=lambda x: -sum(x[1].values()))
        for villa, chs in villa_sorted:
            ch_parts = "  |  ".join(
                f"{c}: {n}" for c, n in sorted(chs.items(), key=lambda x: -x[1])
            )
            lines.append(f"   {villa}: {ch_parts}")

    text = "\n".join(lines)
    log("Sending to Lark webhook...")

    r = requests.post(LARK_WEBHOOK, json={"msg_type": "text", "content": {"text": text}}, timeout=15)
    d = r.json()
    if d.get("code") == 0 or d.get("StatusCode") == 0 or d.get("status") == 0:
        log("Lark OK!")
    else:
        log(f"Lark response: {d}")
    return d

# ── Setup cron tren macOS ─────────────────────────────────────────────────────
def setup_cron():
    script_path = Path(__file__).resolve()
    plist_path  = Path.home() / "Library/LaunchAgents/io.class6.tokeet_report.plist"
    log_path    = Path.home() / "Library/Logs/tokeet_report.log"
    python_bin  = sys.executable

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.class6.tokeet_report</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_bin}</string>
        <string>{script_path}</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key><integer>2</integer>
        <key>Hour</key><integer>8</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>"""

    plist_path.write_text(plist_content)
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    subprocess.run(["launchctl", "load",   str(plist_path)], check=True)
    print(f"Cron da setup: moi thu 2 luc 8:00 AM")
    print(f"Plist: {plist_path}")
    print(f"Log:   {log_path}")

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    d_from, d_to = last_week_range()
    wk_num       = d_from.isocalendar()[1]
    week_label   = f"Tuần {wk_num}/{d_from.year} ({d_from.strftime('%d/%m')} - {d_to.strftime('%d/%m')})"
    log(f"=== {week_label} ===")

    bks = fetch_bookings(d_from)
    totals, channels, villas = aggregate(bks, d_from, d_to)

    log(f"Doanh thu: {totals['revenue']:,.0f} VND | Huy: {totals['cancel_rev']:,.0f} VND")

    send_lark(week_label, totals, channels, villas)
    log("DONE!")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--setup-cron":
        setup_cron()
    else:
        main()
