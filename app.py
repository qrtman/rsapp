import os
import json
import base64
import hmac
import hashlib
from functools import wraps

from flask import Flask, request, jsonify, Response, abort

# (Your cryptographic imports remain the same)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidTag

app = Flask(__name__)

# --- CONFIGURATION ---
VERIFY_TOKEN = "obisar2121!"
APP_SECRET = os.environ.get("APP_SECRET")
PRIVATE_KEY_PEM = os.environ.get("PRIVATE_KEY")

if not APP_SECRET:
    raise ValueError("APP_SECRET environment variable not set.")
if not PRIVATE_KEY_PEM:
    raise ValueError("PRIVATE_KEY environment variable not set.")

PRIVATE_KEY = serialization.load_pem_private_key(PRIVATE_KEY_PEM.encode(), password=None)

# --- DECORATOR FOR SIGNATURE VALIDATION ---
def validate_signature(f):
    """Decorator to validate the request signature from Meta."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not signature.startswith("sha256="):
            print("Validation failed: Invalid signature header format.")
            abort(401)

        expected_signature = hmac.new(
            APP_SECRET.encode(),
            request.get_data(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature[7:], expected_signature):
            print("Validation failed: Signatures do not match.")
            abort(401)
        
        return f(*args, **kwargs)
    return decorated_function

# (The decrypt_request and encrypt_response functions remain the same)
def decrypt_request(encrypted_aes_key_b64, initial_vector_b64, encrypted_flow_data_b64):
    # ... same decryption logic ...
    encrypted_aes_key = base64.b64decode(encrypted_aes_key_b64)
    initial_vector = base64.b64decode(initial_vector_b64)
    encrypted_flow_data = base64.b64decode(encrypted_flow_data_b64)
    whatsapp_public_key = x25519.X25519PublicKey.from_public_bytes(encrypted_aes_key[:32])
    shared_key = PRIVATE_KEY.exchange(whatsapp_public_key)
    aes_key = shared_key
    aesgcm = AESGCM(aes_key)
    decrypted_data = aesgcm.decrypt(initial_vector, encrypted_flow_data, None)
    return json.loads(decrypted_data.decode('utf-8')), aes_key

def encrypt_response(aes_key, response_data):
    # ... same encryption logic ...
    aesgcm = AESGCM(aes_key)
    iv = os.urandom(12)
    encrypted_data = aesgcm.encrypt(iv, json.dumps(response_data).encode('utf-8'), None)
    return base64.b64encode(iv + encrypted_data).decode('utf-8')

# --- FLASK ENDPOINT ---
@app.route('/api/whatsapp', methods=['GET', 'POST'])
@validate_signature
def whatsapp_endpoint():
    
    if request.method == 'GET':
        # ... same verification logic ...
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            return challenge, 200
        else:
            return 'Verification token does not match', 403

    elif request.method == 'POST':
        request_body = request.get_json()
        
        # --- NEW: Handle Health Checks and Error Notifications (Unencrypted) ---
        action = request_body.get('action')
        if action == 'ping':
            print("Received health check.")
            return jsonify({"data": {"status": "active"}})

        if 'data' in request_body and 'error' in request_body['data']:
            error_message = request_body['data'].get('error_message')
            print(f"Received an error notification: {error_message}")
            return jsonify({"data": {"acknowledged": True}})

        # --- Proceed with standard encrypted Flow message processing ---
        try:
            decrypted_data, aes_key = decrypt_request(
                request_body['encrypted_aes_key'],
                request_body['initial_vector'],
                request_body['encrypted_flow_data']
            )
            print("Decrypted Data:", decrypted_data)
        
        except (InvalidTag, ValueError, KeyError) as e:
            print(f"Decryption failed: {e}")
            return Response("Decryption failed", status=421, mimetype='text/plain')

        # --- Main Endpoint Logic Router ---
        flow_action = decrypted_data.get('action')
        response_payload = None

        if flow_action == 'INIT':
            response_payload = {"version": "3.0", "screen": "WELCOME_SCREEN", "data": {}}

        elif flow_action == 'data_exchange':
            # ... same business logic as before ...
            submitted_screen = decrypted_data.get('screen')
            screen_data = decrypted_data.get('data', {})
            if submitted_screen == 'WELCOME_SCREEN':
                response_payload = {"version": "3.0", "screen": "BUDGET_SCREEN", "data": {}}
            elif submitted_screen == 'BUDGET_SCREEN':
                budget = screen_data.get('budget', '').strip()
                if budget.isdigit():
                    response_payload = {"version": "3.0", "screen": "CAR_TYPE_SCREEN", "data": {"user_budget": budget}}
                else:
                    response_payload = {"version": "3.0", "screen": "BUDGET_SCREEN", "data": {"error_message": "Пожалуйста, введите бюджет цифрами."}}
            elif submitted_screen == 'CAR_TYPE_SCREEN':
                # ... same SUCCESS response logic as before ...
                 response_payload = {
                    "version": "3.0", "screen": "SUCCESS",
                    "data": {
                        "extension_message_response": {
                            "params": {
                                "flow_token": decrypted_data.get('flow_token'),
                                "body": f"Спасибо! Мы получили ваш запрос..."
                            }}}}
        
        if response_payload:
            encrypted_response_body = encrypt_response(aes_key, response_payload)
            return Response(encrypted_response_body, status=200, mimetype='text/plain')
        else:
            return Response("Unknown action", status=400, mimetype='text/plain')
