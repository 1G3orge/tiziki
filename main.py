from flask import Flask, request, jsonify
import os

app = Flask(__name__)

@app.route('/')
def index():
    return 'Tiziki WiFi App Live with HTTPS!'

@app.route('/store_payment', methods=['POST'])
def store_payment():
    data = request.get_json()
    print("Received payment:", data)
    return jsonify({"status": "success", "message": "Payment received"}), 200

@app.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    callback = request.get_json()
    print("Callback received:", callback)
    return jsonify({"ResultCode": 0, "ResultDesc": "Success"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))  # Railway provides PORT
    app.run(host="0.0.0.0", port=port)
