#!/usr/bin/env python3
import os
import sys
import pytz
import gspread
import traceback
from oauth2client.service_account import ServiceAccountCredentials
from kiteconnect import KiteConnect
from datetime import datetime, time

# -----------------------------
# 0. CONFIG
# -----------------------------
SHEET_ID = os.getenv("SHEET_ID")
GOOGLE_CREDS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "service_account.json")
API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

EXPIRIES = [
    ("2026-03-24", "Expiry1"),
    ("2026-03-30", "Expiry2"),
    ("2025-04-07", "Expiry3"),
    ("2026-04-14", "Expiry4"),
]

# -----------------------------
# 1. Market Open Check
# -----------------------------
ist = pytz.timezone("Asia/Kolkata")
now = datetime.now(ist)
current_time = now.time()
market_open = time(9, 10)
market_close = time(15, 35)

if not (market_open <= current_time <= market_close) or now.weekday() >= 5:
    print("📉 Market is closed, exiting script.")
    sys.exit(0)
print(f"✅ Market is open. Time: {current_time}")

# -----------------------------
# 2. Setup KiteConnect
# -----------------------------
if not API_KEY or not ACCESS_TOKEN:
    raise Exception("❌ Missing API_KEY or ACCESS_TOKEN!")

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

# -----------------------------
# 3. Setup Google Sheets
# -----------------------------
if not SHEET_ID or not os.path.exists(GOOGLE_CREDS_PATH):
    raise Exception("❌ Missing Google Sheet ID or credentials file!")

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS_PATH, scope)
client = gspread.authorize(creds)

# -----------------------------
# 4. Process each expiry
# -----------------------------
total_expiries = len(EXPIRIES)
successful = 0
failed = 0

for expiry, sheet_name in EXPIRIES:
    start_time = datetime.now()
    print(f"\n📌 Processing expiry {expiry} → Sheet {sheet_name}")

    try:
        # Get sheet
        try:
            sheet = client.open_by_key(SHEET_ID).worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            sheet = client.open_by_key(SHEET_ID).add_worksheet(
                title=sheet_name, rows=1000, cols=20
            )

        # Load previous OI
        existing_values = sheet.get_all_values()
        prev_oi_dict = {}
        if existing_values:
            headers = existing_values[0]
            if "Strike" in headers and "Call OI" in headers and "Put OI" in headers:
                strike_col = headers.index("Strike")
                call_oi_col = headers.index("Call OI")
                put_oi_col = headers.index("Put OI")

                for row in existing_values[1:]:
                    try:
                        strike = float(row[strike_col])
                        call_oi = int(row[call_oi_col]) if row[call_oi_col] else 0
                        put_oi = int(row[put_oi_col]) if row[put_oi_col] else 0
                        prev_oi_dict[strike] = {"call": call_oi, "put": put_oi}
                    except:
                        pass

        # Fetch NIFTY instruments
        instruments = kite.instruments("NFO")
        nifty_options = [
            i for i in instruments
            if i.get("name") == "NIFTY" and i.get("expiry").strftime("%Y-%m-%d") == expiry
        ]
        print(f"✅ Found {len(nifty_options)} contracts for {expiry}")

        # Build option chain
        option_chain = {}
        fetch_count = 0
        fetch_errors = 0

        for inst in nifty_options:
            try:
                fetch_count += 1

                quote = kite.quote(inst["instrument_token"])
                q = quote[str(inst["instrument_token"])]

                ltp = q["last_price"]
                oi = q.get("oi", 0)
                vol = q.get("volume", 0)

                strike = inst["strike"]
                typ = inst["instrument_type"]

                if strike not in option_chain:
                    option_chain[strike] = {"call": {}, "put": {}}

                if typ == "CE":
                    prev_oi = prev_oi_dict.get(strike, {}).get("call", 0)
                    option_chain[strike]["call"] = {
                        "ltp": ltp, "oi": oi,
                        "chg_oi": oi - prev_oi, "vol": vol
                    }
                elif typ == "PE":
                    prev_oi = prev_oi_dict.get(strike, {}).get("put", 0)
                    option_chain[strike]["put"] = {
                        "ltp": ltp, "oi": oi,
                        "chg_oi": oi - prev_oi, "vol": vol
                    }

            except Exception as e:
                fetch_errors += 1
                print(f"⚠️ Error fetching {inst.get('tradingsymbol')} (token {inst.get('instrument_token')}): {e}")
                traceback.print_exc()

        # Prepare rows
        rows = []
        for strike, data in sorted(option_chain.items()):
            call = data.get("call", {})
            put = data.get("put", {})
            rows.append([
                call.get("ltp", 0),
                call.get("oi", 0),
                call.get("chg_oi", 0),
                call.get("vol", 0),
                strike,
                expiry,
                put.get("ltp", 0),
                put.get("oi", 0),
                put.get("chg_oi", 0),
                put.get("vol", 0),
                ""
            ])

        # ----------------------------------------
        # ✅ WRITE — NO NEW ROWS WILL BE CREATED
        # ----------------------------------------
        headers_row = [
            "Call LTP", "Call OI", "Call Chg OI", "Call Vol",
            "Strike", "Expiry",
            "Put LTP", "Put OI", "Put Chg OI", "Put Vol",
            "VWAP"
        ]

        # Clear only content (keep row structure)
        sheet.batch_clear(["A2:Z1000"])

        # Update header
        sheet.update("A1:K1", [headers_row])

        # Update data rows in place
        if rows:
            sheet.update(f"A2:K{len(rows)+1}", rows)

        # -----------------------------------------

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"✅ Logged {len(rows)} rows in {sheet_name} (fetched {fetch_count}, errors {fetch_errors}) in {elapsed:.1f}s")

        successful += 1

    except Exception as e:
        failed += 1
        print(f"❌ Error processing {expiry}: {e}")
        traceback.print_exc()

# Final summary
print("\n--- Summary ---")
print(f"Expiries processed: {total_expiries}, successful: {successful}, failed: {failed}")
if failed == 0:
    print("🎉 All expiries updated successfully.")
else:
    print("⚠️ Some expiries failed. Check logs above.")
