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
TRANSACTION_STATUS_URL = "https://api.safaricom.co.ke/mpesa/transactionstatus/v1/query"
TOKEN_URL = "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
SHORTCODE = "5346268"           # Head Office shortcode
TILL_NUMBER = "4498236"         # Store Till number
PASSKEY = "8d9756e6f0e08473b287b4bdc1e8a745d0a28d222a55482fd236fdd6d51c92b2"
CALLBACK_URL = "https://web-production-929d5.up.railway.app/mpesa/callback"
CONSUMER_KEY = "BAqAD0MtDAfXBTbwLqzhmgSszUo6YV10p6Ly91dndfH41mR8"
CONSUMER_SECRET = "6B6N674G2LYva5h4rueE7tisiKmAhePGW3SRBQCtZg8i0YWArS5ihtcpFnAJ8Z08"
INITIATOR_NAME = "testapiuser"
SECURITY_CREDENTIAL = "ClONZiMYBpc65lmpJ7nvnrDmUe0WvHvA5QbOsPjEo92B6IGFwDdvdeJIFL0kgwsEKWu6SQKG4ZZUxjC"

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

def query_transaction_status(transaction_id, phone):
    try:
        token = get_access_token()
        if not token:
            print("❌ Failed to get access token for transaction status check.")
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {
            "Initiator": INITIATOR_NAME,
            "SecurityCredential": SECURITY_CREDENTIAL,
            "CommandID": "TransactionStatusQuery",
            "TransactionID": transaction_id,
            "PartyA": SHORTCODE,
            "IdentifierType": "4",
            "ResultURL": CALLBACK_URL,
            "QueueTimeOutURL": CALLBACK_URL,
            "Remarks": "Check Transaction",
            "Occasion": "AutoStatusCheck",
            "OriginatorConversationID": f"AUTO_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        }

        response = requests.post(TRANSACTION_STATUS_URL, json=payload, headers=headers)
        print("🔄 Transaction Status Response:", response.json())

        # Optionally update Google Sheet here if response includes confirmation

    except Exception as e:
        print("❌ Error checking transaction status:", e)

def update_status_after_delay(phone, transaction_id=None, delay=10):
    time.sleep(delay)
    if sheet:
        try:
            cell = sheet.find(phone)
            status_cell = sheet.cell(cell.row, 7).value  # Status cell
            if status_cell == "Pending":
                sheet.update_cell(cell.row, 7, "❌ Not Received")
                if transaction_id:
                    query_transaction_status(transaction_id, phone)
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
        merchant_request_id = res_data.get("MerchantRequestID")

        if sheet:
            try:
                sheet.append_row([phone, duration, amount, ip, timestamp, status_text, "Pending"])
                threading.Thread(target=update_status_after_delay, args=(phone, merchant_request_id), daemon=True).start()
            except Exception as e:
                print("❌ Google Sheets Logging Error:", e)

        if response.status_code == 200 and res_data.get("ResponseCode") == "0":
            return jsonify({"status": "success", "message": "STK push sent to phone", "MerchantRequestID": merchant_request_id})
        else:
            return jsonify({"status": "error", "message": "STK push failed", "details": res_data}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    try:
        callback_data = request.get_json(force=True)
        print("\ud83d\udce5 M-Pesa Callback:", json.dumps(callback_data, indent=2))

        stk = callback_data.get("Body", {}).get("stkCallback", {})
        result_code = stk.get("ResultCode", -1)
        result_desc = stk.get("ResultDesc", "No description provided")

        phone = None
        amount = None
        row_to_update = None

        # 🔍 Debugging info
        print(f"\ud83d\udd0d ResultCode: {result_code}")
        print(f"\ud83d\udd0d ResultDesc: {result_desc}")

        if result_code == 0:
            metadata = stk.get("CallbackMetadata", {}).get("Item", [])
            for item in metadata:
                if item.get("Name") == "PhoneNumber":
                    phone = str(item.get("Value"))
                elif item.get("Name") == "Amount":
                    amount = item.get("Value")
            print(f"\ud83d\udd0d Phone found in callback: {phone}")
        else:
            print("\u26a0\ufe0f Transaction failed \u2014 no CallbackMetadata returned.")

        status_text = "Success" if result_code == 0 else "\u274c Payment Failed"
        payment_status = "Confirmed" if result_code == 0 else "Failed"

        if sheet:
            try:
                if phone:
                    try:
                        cell = sheet.find(phone)
                        row_to_update = cell.row
                        print(f"\u2705 Matched row by phone: {row_to_update}")
                    except:
                        print("\u26a0\ufe0f Phone number not found in sheet. Will try fallback.")

                if row_to_update is None:
                    records = sheet.get_all_values()
                    for i in range(len(records) - 1, 0, -1):
                        if len(records[i]) >= 7 and records[i][6].strip().lower() == "pending":
                            row_to_update = i + 1
                            break

                if row_to_update:
                    sheet.update_cell(row_to_update, 6, status_text)
                    sheet.update_cell(row_to_update, 7, payment_status)
                    sheet.update_cell(row_to_update, 8, result_desc)
                    print(f"\ud83d\udcdd Sheet updated at row {row_to_update}: {payment_status} | {result_desc}")
                else:
                    print("\u26a0\ufe0f No matching row found in sheet to update.")

            except Exception as e:
                print("\u274c Google Sheets update error:", e)
        else:
            print("\u2139\ufe0f Sheet unavailable \u2014 skipping update.")

        return jsonify({"ResultCode": 0, "ResultDesc": "Callback handled successfully"})

    except Exception as e:
        print("\u274c Callback error:", e)
        return jsonify({"ResultCode": 1, "ResultDesc": "Callback failed"})


@app.route('/transaction_status', methods=['POST'])
def transaction_status():
    try:
        data = request.get_json()
        transaction_id = data.get("TransactionID")
        originator_conversation_id = data.get("OriginatorConversationID") or "AG_" + datetime.now().strftime("%Y%m%d_%H%M%S")

        if not transaction_id:
            return jsonify({"status": "error", "message": "Missing Transaction ID"}), 400

        token = get_access_token()
        if not token:
            return jsonify({"status": "error", "message": "Access token failed"}), 500

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {
            "Initiator": INITIATOR_NAME,
            "SecurityCredential": SECURITY_CREDENTIAL,
            "CommandID": "TransactionStatusQuery",
            "TransactionID": transaction_id,
            "PartyA": SHORTCODE,
            "IdentifierType": "4",
            "ResultURL": CALLBACK_URL,
            "QueueTimeOutURL": CALLBACK_URL,
            "Remarks": "OK",
            "Occasion": "TizikiCheck",
            "OriginatorConversationID": originator_conversation_id
        }

        response = requests.post(TRANSACTION_STATUS_URL, json=payload, headers=headers)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    
@app.route('/check_status', methods=['POST'])
def check_status():
    try:
        data = request.get_json()
        phone = data.get("phone_number")
        if not phone or not sheet:
            return jsonify({"status": "error", "message": "Missing phone number or sheet unavailable"}), 400

        cell = sheet.find(phone)
        status = sheet.cell(cell.row, 7).value  # Payment Status column
        result_desc = sheet.cell(cell.row, 8).value  # ResultDescription column

        return jsonify({
          "status": "success",
          "payment_status": status,
          "result_description": result_desc
        })
    except Exception as e:
        print("❌ /check_status error:", e)
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8080)
