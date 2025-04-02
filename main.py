from flask import Flask, request, jsonify
import requests
import base64
from datetime import datetime
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials
import os
import json
import threading
import time

app = Flask(__name__)
CORS(app)  # Enable CORS

# ✅ M-Pesa Configuration (Live)
MPESA_BASE_URL = "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
TOKEN_URL = "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
SHORTCODE = "5346268"           # Head Office shortcode
TILL_NUMBER = "4498236"         # Store Till number
PASSKEY = "8d9756e6f0e08473b287b4bdc1e8a745d0a28d222a55482fd236fdd6d51c92b2"
CALLBACK_URL = "https://web-production-929d5.up.railway.app/mpesa/callback"
CONSUMER_KEY = "BAqAD0MtDAfXBTbwLqzhmgSszUo6YV10p6Ly91dndfH41mR8"
CONSUMER_SECRET = "6B6N674G2LYva5h4rueE7tisiKmAhePGW3SRBQCtZg8i0YWArS5ihtcpFnAJ8Z08"

# ✅ Google Sheets Logging
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
SHEET_NAME = "Tiziki WiFi Data"

try:
    SERVICE_ACCOUNT_INFO = json.loads(os.getenv("GOOGLE_CREDS_JSON"))
    creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1
except Exception as e:
    print("❌ Error connecting to Google Sheets:", e)
    sheet = None

def get_access_token():
    try:
        response = requests.get(TOKEN_URL, auth=(CONSUMER_KEY, CONSUMER_SECRET))
        return response.json().get("access_token") if response.status_code == 200 else None
    except Exception as e:
        print("❌ Token error:", e)
        return None

def generate_password():
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    data = SHORTCODE + PASSKEY + timestamp
    password = base64.b64encode(data.encode()).decode()
    return password, timestamp

def update_status_after_delay(phone, delay=10):
    time.sleep(delay)
    if sheet:
        try:
            cell = sheet.find(phone)
            status_cell = sheet.cell(cell.row, 7).value  # Status cell
            if status_cell == "Pending":
                sheet.update_cell(cell.row, 7, "❌ Not Received")
        except Exception as e:
            print("❌ Status update error:", e)

@app.route('/store_payment', methods=['POST'])
def store_payment():
    try:
        data = request.get_json()
        phone = data.get("phone_number")
        amount = int(data.get("price", 0))
        duration = f"{data.get('selected_option')} {data.get('option_type')}"
        ip = data.get("ip_address", "Unknown")
        timestamp = data.get("timestamp", datetime.now().isoformat())

        if not phone or not amount:
            return jsonify({"status": "error", "message": "Missing phone or amount"}), 400

        token = get_access_token()
        if not token:
            return jsonify({"status": "error", "message": "Access token failed"}), 500

        password, stk_timestamp = generate_password()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        payload = {
            "BusinessShortCode": SHORTCODE,
            "Password": password,
            "Timestamp": stk_timestamp,
            "TransactionType": "CustomerBuyGoodsOnline",
            "Amount": amount,
            "PartyA": phone,
            "PartyB": TILL_NUMBER,
            "PhoneNumber": phone,
            "CallBackURL": CALLBACK_URL,
            "AccountReference": "TIZIKI WIFI ACCESS",
            "TransactionDesc": f"Tiziki WiFi - {amount} KES"
        }

        response = requests.post(MPESA_BASE_URL, json=payload, headers=headers)
        res_data = response.json()

        status_text = "✅ Payment Request Sent" if response.status_code == 200 and res_data.get("ResponseCode") == "0" else "❌ STK Push Failed"

        if sheet:
            try:
                sheet.append_row([phone, duration, amount, ip, timestamp, status_text, "Pending"])
                threading.Thread(target=update_status_after_delay, args=(phone,), daemon=True).start()
            except Exception as e:
                print("❌ Google Sheets Logging Error:", e)

        if response.status_code == 200 and res_data.get("ResponseCode") == "0":
            return jsonify({"status": "success", "message": "STK push sent to phone"})
        else:
            return jsonify({"status": "error", "message": "STK push failed", "details": res_data}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    try:
        callback_data = request.get_json()
        print("📥 M-Pesa Callback:", json.dumps(callback_data, indent=2))

        stk = callback_data.get("Body", {}).get("stkCallback", {})
        result_code = stk.get("ResultCode")
        metadata = stk.get("CallbackMetadata", {}).get("Item", [])
        phone = ""
        amount = ""

        for item in metadata:
            if item.get("Name") == "PhoneNumber":
                phone = str(item.get("Value"))
            elif item.get("Name") == "Amount":
                amount = item.get("Value")

        status = "✅ Payment Confirmed" if result_code == 0 else "❌ Payment Failed"

        if sheet and phone:
            try:
                cell = sheet.find(phone)
                sheet.update_cell(cell.row, 6, status)  # Update Status column
                sheet.update_cell(cell.row, 7, "Confirmed" if result_code == 0 else "Failed")
            except Exception as e:
                print("❌ Callback logging error:", e)

        return jsonify({"ResultCode": 0, "ResultDesc": "Success"})
    except Exception as e:
        print("❌ Callback error:", e)
        return jsonify({"ResultCode": 1, "ResultDesc": "Failed"})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8080)
