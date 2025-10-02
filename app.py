import os
import json
import base64
import hmac
import hashlib
import uuid
import requests
from functools import wraps

from flask import Flask, request, jsonify, Response, abort
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidTag
from dotenv import load_dotenv

app = Flask(__name__)
load_dotenv()

# --- CONFIGURATION ---
VERIFY_TOKEN = "obisar2121!"
APP_SECRET = os.environ.get("APP_SECRET")
PRIVATE_KEY_PEM = os.environ.get("PRIVATE_KEY")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN")

# --- Basic Sanity Checks for Environment Variables ---
if not APP_SECRET:
    raise ValueError("APP_SECRET environment variable not set.")
if not PRIVATE_KEY_PEM:
    raise ValueError("PRIVATE_KEY environment variable not set.")
if not PHONE_NUMBER_ID:
    raise ValueError("PHONE_NUMBER_ID environment variable not set.")
if not ACCESS_TOKEN:
    raise ValueError("ACCESS_TOKEN environment variable not set.")

PRIVATE_KEY = serialization.load_pem_private_key(PRIVATE_KEY_PEM.encode(), password=None)
GRAPH_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

# --- DECORATOR FOR SIGNATURE VALIDATION ---
def validate_signature(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not signature.startswith("sha256="):
            abort(401)
        expected_signature = hmac.new(APP_SECRET.encode(), request.get_data(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature[7:], expected_signature):
            abort(401)
        return f(*args, **kwargs)
    return decorated_function

# --- CRYPTOGRAPHIC FUNCTIONS ---
def decrypt_request(encrypted_aes_key_b64, initial_vector_b64, encrypted_flow_data_b64):
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
    aesgcm = AESGCM(aes_key)
    iv = os.urandom(12)
    encrypted_data = aesgcm.encrypt(iv, json.dumps(response_data).encode('utf-8'), None)
    return base64.b64encode(iv + encrypted_data).decode('utf-8')


# --- MESSAGE SENDING LOGIC (Adapted from main.py) ---

def send_text_message(text, phone_number):
    """Sends a simple text message."""
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": str(phone_number),
        "type": "text",
        "text": {"preview_url": False, "body": text}
    })
    send_request_to_graph_api(payload)

def send_flow_message(flow_id, screen_id, flow_header, flow_body, phone_number):
    """Constructs and sends a message to trigger a Flow."""
    flow_token = str(uuid.uuid4())
    interactive_payload = {
        "type": "flow",
        "header": {"type": "text", "text": flow_header},
        "body": {"text": flow_body},
        "footer": {"text": "Click the button to start"},
        "action": {
            "name": "flow",
            "parameters": {
                "flow_message_version": "3",
                "flow_token": flow_token,
                "flow_id": flow_id,
                "flow_cta": "Start",
                "flow_action": "navigate",
                "flow_action_payload": {"screen": screen_id},
            }
        }
    }
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": str(phone_number),
        "type": "interactive",
        "interactive": interactive_payload
    })
    send_request_to_graph_api(payload)

def send_request_to_graph_api(payload):
    """Generic function to send a POST request to the Graph API."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + ACCESS_TOKEN,
    }
    try:
        response = requests.post(GRAPH_API_URL, headers=headers, data=payload)
        response.raise_for_status()
        print("Message sent successfully!")
    except requests.exceptions.RequestException as e:
        print(f"Error sending message: {e}")


# --- WEBHOOK PROCESSING LOGIC (Adapted from main.py) ---

def process_text_message(message_body, phone_number, name):
    """Processes incoming text messages and decides on a response."""
    user_input = message_body.lower()
    
    # Simple keyword matching
    if "привет" in user_input or "здравствуйте" in user_input:
        reply_text = f"Здравствуйте, {name}! Я ваш помощник по подбору авто из Кореи. Чтобы начать, просто напишите 'подбор'."
        send_text_message(reply_text, phone_number)
        
    elif "подбор" in user_input:
        # Here you would trigger your car selection flow
        # Make sure to replace <YOUR_FLOW_ID> with the actual ID from WhatsApp Manager
        send_flow_message(
            flow_id="1620826182228246",
            screen_id="WELCOME_SCREEN", # The entry screen of your flow
            flow_header="Подбор Авто",
            flow_body="Нажмите 'Start', чтобы начать подбор автомобиля вашей мечты.",
            phone_number=phone_number
        )
    else:
        # Default fallback response
        reply_text = "Я не совсем понял ваш запрос. Напишите 'подбор', чтобы начать процесс выбора автомобиля."
        send_text_message(reply_text, phone_number)

def process_flow_completion(response_json, phone_number, name):
    """Processes the data from a completed static Flow."""
    flow_data = json.loads(response_json)
    flow_key = flow_data.get("flow_key")
    
    if flow_key == "contact":
        firstname = flow_data.get("firstname", "")
        issue = flow_data.get("issue", "")
        reply = f"Спасибо, {firstname}! Мы получили ваше сообщение: '{issue}'. Скоро свяжемся с вами."
        send_text_message(reply, phone_number)
    else:
        print(f"Received unknown flow completion with key: {flow_key}")


# --- MAIN FLASK ENDPOINT ---

@app.route('/api/whatsapp', methods=['GET', 'POST'])
def whatsapp_endpoint():
    
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            print("Webhook verified!")
            return challenge, 200
        else:
            print("Webhook verification failed.")
            return 'Verification token does not match', 403

    elif request.method == 'POST':
        request_body = request.get_json()
        
        # Check if this is an encrypted Flow message (for multi-step flows)
        if 'encrypted_aes_key' in request_body:
            try:
                decrypted_data, aes_key = decrypt_request(
                    request_body['encrypted_aes_key'],
                    request_body['initial_vector'],
                    request_body['encrypted_flow_data']
                )
                print("Decrypted Flow Data:", decrypted_data)

                # --- Your existing multi-screen flow logic router goes here ---
                # This remains the same as before, handling INIT, data_exchange, etc.
                flow_action = decrypted_data.get('action')
                # ... (rest of your multi-screen logic)
                response_payload = {"version": "3.0", "screen": "ERROR_SCREEN", "data": {}} # Default
                if flow_action == 'INIT':
                    response_payload = {"version": "3.0", "screen": "WELCOME_SCREEN", "data": {}}
                
                encrypted_response_body = encrypt_response(aes_key, response_payload)
                return Response(encrypted_response_body, status=200, mimetype='text/plain')

            except (InvalidTag, ValueError, KeyError) as e:
                print(f"Decryption failed: {e}")
                return Response("Decryption failed", status=421, mimetype='text/plain')
        
        # Otherwise, process it as a standard unencrypted webhook
        else:
            try:
                changes = request_body['entry'][0]['changes'][0]['value']
                message_object = changes.get('messages')
                
                if message_object:
                    message_data = message_object[0]
                    phone_number = message_data['from']
                    name = changes['contacts'][0]['profile']['name']
                    
                    # Check for a standard text message
                    if 'text' in message_data:
                        message_body = message_data['text']['body']
                        process_text_message(message_body, phone_number, name)
                    
                    # Check for a completed flow (from a terminal screen)
                    elif 'interactive' in message_data and 'nfm_reply' in message_data['interactive']:
                        response_json = message_data['interactive']['nfm_reply']['response_json']
                        process_flow_completion(response_json, phone_number, name)

                return jsonify(status="ok"), 200

            except (KeyError, IndexError) as e:
                print(f"Error processing standard webhook: {e}")
                return jsonify(status="error", reason="malformed data"), 400
