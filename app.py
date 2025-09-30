import os
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- Step 1: Define your Verify Token ---
# You must create this token. It's a secret password.
# For security, set this in Render's "Environment" settings.
VERIFY_TOKEN = "PASTE_YOUR_SECRET_VERIFY_TOKEN_HERE"


# This endpoint now accepts GET (for verification) and POST (for messages)
@app.route('/api/whatsapp', methods=['GET', 'POST'])
def whatsapp_endpoint():
    
    # --- Step 2: Handle the GET request for webhook verification ---
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')

        if mode == 'subscribe' and token == VERIFY_TOKEN:
            print('WEBHOOK_VERIFIED')
            return challenge, 200
        else:
            print('VERIFICATION_FAILED')
            return 'Verification token does not match', 403

    # --- Step 3: Handle the POST request (your original code) ---
    elif request.method == 'POST':
        request_data = request.get_data()
        print("Received raw data:", request_data)

        # --- TODO: DECRYPTION LOGIC for Flows/Messages ---
        
        response_payload = {
            "version": "1.0",
            "data": {
                "message": "Response from the endpoint!"
            }
        }
        return jsonify(response_payload)

if __name__ == '__main__':
    app.run()
