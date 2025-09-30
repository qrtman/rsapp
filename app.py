import os
import json
import base64
import hmac
import hashlib
from functools import wraps

from flask import Flask, request, jsonify, Response, abort

# (All your configuration and crypto functions remain the same)
# ...

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
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # ... same signature validation logic ...
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not signature.startswith("sha256="):
            abort(401)
        expected_signature = hmac.new(APP_SECRET.encode(), request.get_data(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature[7:], expected_signature):
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
        
        # --- NEW: Check if this is an encrypted Flow message or a standard one ---
        if 'encrypted_aes_key' in request_body:
            # This is an encrypted Flow message, proceed with decryption
            try:
                decrypted_data, aes_key = decrypt_request(
                    request_body['encrypted_aes_key'],
                    request_body['initial_vector'],
                    request_body['encrypted_flow_data']
                )
                # ... same Flow logic router as before ...
                # (This part remains unchanged)
                
            except (InvalidTag, ValueError, KeyError) as e:
                return Response("Decryption failed", status=421, mimetype='text/plain')
            
            # ... The entire Flow logic router goes here ...
            flow_action = decrypted_data.get('action')
            # ... etc ...
            # For brevity, this is the same logic as the last full code block.
            # Just ensure it's nested inside this "if" block.
            response_payload = {"version": "3.0", "screen": "ERROR_SCREEN", "data": {}} # Default
            if flow_action == 'INIT':
                response_payload = {"version": "3.0", "screen": "WELCOME_SCREEN", "data": {}}
            # ... rest of your flow logic ...

            encrypted_response_body = encrypt_response(aes_key, response_payload)
            return Response(encrypted_response_body, status=200, mimetype='text/plain')
        
        else:
            # This is a standard, unencrypted message webhook
            print("Received standard webhook:", request_body)
            # You can add logic here to handle regular text messages if you want.
            # For now, just acknowledge it with a 200 OK to stop the errors.
            return jsonify(status="ok"), 200
