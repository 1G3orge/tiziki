from flask import Flask, request, jsonify
import requests
import base64
from datetime import datetime
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)
CORS(app)

# 🔐 Live M-Pesa Config
MPESA_BASE_URL = "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
TOKEN_URL = "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
SHORTCODE = "5346268"  # Replace with your BuyGoods shortcode
PASSKEY = "8d9756e6f0e08473b287b4bdc1e8a745d0a28d222a55482fd236fdd6d51c92b2"
CALLBACK_URL = "https://web-production-929d5.up.railway.app/mpesa/callback"
CONSUMER_KEY = "BAqAD0MtDAfXBTbwLqzhmgSszUo6YV10p6Ly91dndfH41mR8"
CONSUMER_SECRET = "6B6N674G2LYva5h4rueE7tisiKmAhePGW3SRBQCtZg8i0YWArS5ihtcpFnAJ8Z08"

# Google Sheets Setup
SHEET_NAME = "Tiziki WiFi Data"
SERVICE_ACCOUNT_FILE = "google-credentials.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

try:
    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1
except Exception as google_auth_error:
    sheet = None
    print("❌ Error connecting to Google Sheets:", google_auth_error)
    print("⚠️ Ensure 'google-credentials.json' is present and valid in this environment.")

def get_access_token():
    try:
        res = requests.get(TOKEN_URL, auth=(CONSUMER_KEY, CONSUMER_SECRET))
        return res.json().get("access_token") if res.status_code == 200 else None
    except Exception as e:
        print("Token error:", e)
        return None

def generate_password():
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    data = SHORTCODE + PASSKEY + timestamp
    password = base64.b64encode(data.encode()).decode()
    return password, timestamp

@app.route('/store_payment', methods=['POST'])
def store_payment():
    try:
        data = request.get_json()
        phone = data.get("phone_number")
        amount = int(data.get("price", 0))
        ip = data.get("ip_address")
        timestamp = data.get("timestamp")
        option_type = data.get("option_type")
        duration = data.get("selected_option")

        if not phone or not amount:
            return jsonify({"status": "error", "message": "Missing phone or amount"}), 400

        token = get_access_token()
        if not token:
            return jsonify({"status": "error", "message": "Access token failed"}), 500

        password, mpesa_time = generate_password()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        payload = {
            "BusinessShortCode": SHORTCODE,
            "Password": password,
            "Timestamp": mpesa_time,
            "TransactionType": "CustomerBuyGoodsOnline",
            "Amount": amount,
            "PartyA": phone,
            "PartyB": SHORTCODE,
            "PhoneNumber": phone,
            "CallBackURL": CALLBACK_URL,
            "AccountReference": "TIZIKI",
            "TransactionDesc": f"{option_type} {duration}"
        }

        response = requests.post(MPESA_BASE_URL, json=payload, headers=headers)
        res_data = response.json()

        # Log to Google Sheet with error handling
        if sheet:
            try:
                sheet.append_row([phone, amount, option_type, duration, ip, timestamp, "Pending"])
            except Exception as sheet_error:
                print("❌ Google Sheets Logging Error:", sheet_error)
                print("⚠️ This may be due to quota limits, auth issues, or locked sheet")

        if response.status_code == 200 and res_data.get("ResponseCode") == "0":
            return jsonify({"status": "success", "message": "STK push sent"})
        else:
            return jsonify({"status": "error", "message": "STK push failed", "details": res_data}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=8080)
