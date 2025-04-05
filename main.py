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

### ✅ 1. Update in /store_payment to log MerchantRequestID to Google Sheet

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
                sheet.append_row([phone, duration, amount, ip, timestamp, status_text, "Pending", "", merchant_request_id])
                threading.Thread(target=update_status_after_delay, args=(phone, merchant_request_id), daemon=True).start()
            except Exception as e:
                print("❌ Google Sheets Logging Error:", e)

        if response.status_code == 200 and res_data.get("ResponseCode") == "0":
            return jsonify({"status": "success", "message": "STK push sent to phone", "MerchantRequestID": merchant_request_id})
        else:
            return jsonify({"status": "error", "message": "STK push failed", "details": res_data}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


### ✅ 2. Update /mpesa/callback to find row by MerchantRequestID

@app.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    try:
        callback_data = request.get_json(force=True)
        print("📥 M-Pesa Callback:", json.dumps(callback_data, indent=2))

        stk = callback_data.get("Body", {}).get("stkCallback", {})
        result_code = stk.get("ResultCode", -1)
        result_desc = stk.get("ResultDesc", "No description provided")
        merchant_request_id = stk.get("MerchantRequestID")

        phone = None
        amount = None

        if result_code == 0:
            metadata = stk.get("CallbackMetadata", {}).get("Item", [])
            for item in metadata:
                if item.get("Name") == "PhoneNumber":
                    phone = str(item.get("Value"))
                elif item.get("Name") == "Amount":
                    amount = item.get("Value")
        else:
            print("⚠️ Transaction failed — no CallbackMetadata returned.")

        status_text = "Success" if result_code == 0 else "❌ Payment Failed"
        payment_status = "Confirmed" if result_code == 0 else "Failed"

        if sheet and merchant_request_id:
            try:
                cell = sheet.find(merchant_request_id)
                row_to_update = cell.row

                sheet.update_cell(row_to_update, 6, status_text)
                sheet.update_cell(row_to_update, 7, payment_status)
                sheet.update_cell(row_to_update, 8, result_desc)

                print(f"✅ Updated row {row_to_update} → {payment_status} | {result_desc}")

            except Exception as e:
                print("❌ Google Sheets update error:", e)
        else:
            print("ℹ️ Sheet unavailable or MerchantRequestID missing — skipping update.")

        return jsonify({"ResultCode": 0, "ResultDesc": "Callback handled successfully"})

    except Exception as e:
        print("❌ Callback error:", e)
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
        merchant_request_id = data.get("merchant_request_id")
        if not merchant_request_id or not sheet:
            return jsonify({"status": "error", "message": "Missing MerchantRequestID or sheet unavailable"}), 400

        cell = sheet.find(merchant_request_id)
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

@app.route('/assign_voucher', methods=['POST'])
def assign_voucher():
    try:
        data = request.get_json()
        merchant_id = data.get("merchant_request_id")

        if not merchant_id:
            return jsonify({"status": "error", "message": "Missing Merchant ID"}), 400

        # Connect to sheet2 (assuming already authorized)
        sheet2 = client.open("Tiziki WiFi Data").worksheet("hours" if "hours" in merchant_id.lower() else "days")
        vouchers = sheet2.get_all_records()

        duration_row = None
        for idx, row in enumerate(vouchers, start=2):
            if not str(row.get("Used")).strip().lower() == "true":
                duration_row = idx
                break

        if not duration_row:
            return jsonify({"status": "error", "message": "No voucher available"}), 404

        voucher = sheet2.cell(duration_row, 1).value  # Assuming voucher code is in first column
        sheet2.update_cell(duration_row, 2, "TRUE")   # Mark as used (assuming Used column is 2)
        
        # Update main sheet to mark it linked
        if sheet:
            cell = sheet.find(merchant_id)
            sheet.update_cell(cell.row, 9, "Linked")  # Assuming Column I holds status
            sheet.update_cell(cell.row, 10, voucher)  # Optional: Store the actual voucher code

        return jsonify({"status": "success", "voucher": voucher})

    except Exception as e:
        print("❌ assign_voucher error:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8080)  
