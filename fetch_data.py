#!/usr/bin/env python3
# -----------------------------
# NIFTY Option Chain Updater (KiteConnect)
# -----------------------------
import os
import sys
import pytz
import gspread
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

# Define expiries you want to track
EXPIRIES = [
    ("2025-11-18", "Expiry1"),
    ("2025-11-25", "Expiry2"),
    ("2025-12-02", "Expiry3"),
    ("2025-12-09", "Expiry4"),
]



# -----------------------------
# 1. Market Open Check (IST)
# -----------------------------
ist = pytz.timezone("Asia/Kolkata")
now = datetime.now(ist)
current_time = now.time()
market_open = time(8, 10)
market_close = time(15, 30)

if not (market_open <= current_time <= market_close) or now.weekday() >= 5:
    print("📉 Market is closed, exiting script.")
    sys.exit(0)
print(f"✅ Market is open. Time: {current_time}")

# -----------------------------
# 2. Setup KiteConnect
# -----------------------------
if not API_KEY or not ACCESS_TOKEN:
    raise Exception("❌ Missing API_KEY or ACCESS_TOKEN in environment!")

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

# -----------------------------
# 3. Setup Google Sheets
# -----------------------------
if not SHEET_ID or not os.path.exists(GOOGLE_CREDS_PATH):
    raise Exception("❌ Missing Google Sheet ID or credentials file!")

scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS_PATH, scope)
client = gspread.authorize(creds)

# -----------------------------
# 4. Process each Expiry
# -----------------------------
for expiry, sheet_name in EXPIRIES:
    print(f"\n📌 Processing expiry {expiry} → Sheet {sheet_name}")

    try:
        # Get target sheet
        try:
            sheet = client.open_by_key(SHEET_ID).worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            sheet = client.open_by_key(SHEET_ID).add_worksheet(title=sheet_name, rows=1000, cols=20)

        # Load existing data for OI change
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
                        continue

        # Fetch option chain instruments for this expiry
        instruments = kite.instruments("NFO")
        nifty_options = [i for i in instruments if i["name"] == "NIFTY" and i["expiry"].strftime("%Y-%m-%d") == expiry]
        print(f"✅ Found {len(nifty_options)} NIFTY contracts for {expiry}")

        # Build option chain
        option_chain = {}
        for inst in nifty_options:
            try:
                quote = kite.quote(inst["instrument_token"])
                ltp = quote[str(inst["instrument_token"])]["last_price"]
                oi = quote[str(inst["instrument_token"])].get("oi", 0)
                vol = quote[str(inst["instrument_token"])].get("volume", 0)

                strike = inst["strike"]
                typ = inst["instrument_type"]

                if strike not in option_chain:
                    option_chain[strike] = {"call": {}, "put": {}}

                if typ == "CE":
                    prev_oi = prev_oi_dict.get(strike, {}).get("call", 0)
                    option_chain[strike]["call"] = {
                        "ltp": ltp,
                        "oi": oi,
                        "chg_oi": oi - prev_oi,
                        "vol": vol
                    }
                elif typ == "PE":
                    prev_oi = prev_oi_dict.get(strike, {}).get("put", 0)
                    option_chain[strike]["put"] = {
                        "ltp": ltp,
                        "oi": oi,
                        "chg_oi": oi - prev_oi,
                        "vol": vol
                    }
            except Exception as e:
                print(f"⚠️ Error fetching {inst['tradingsymbol']}: {e}")

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
                ""  # VWAP placeholder
            ])

        # Write to sheet
        headers_row = [
            "Call LTP", "Call OI", "Call Chg OI", "Call Vol",
            "Strike", "Expiry",
            "Put LTP", "Put OI", "Put Chg OI", "Put Vol",
            "VWAP"
        ]

        sheet.clear()
        sheet.insert_row(headers_row, 1)
        if rows:
            sheet.insert_rows(rows, 2)

        print(f"✅ Logged {len(rows)} rows in {sheet_name}")

    except Exception as e:
        print(f"❌ Error processing {expiry}: {e}")
