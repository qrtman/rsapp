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

# --- КОНФИГУРАЦИЯ ---
VERIFY_TOKEN = "obisar2121!"
APP_SECRET = os.environ.get("APP_SECRET")
PRIVATE_KEY_PEM = os.environ.get("PRIVATE_KEY")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN")

# --- Проверка наличия переменных окружения ---
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

# --- ДЕКОРАТОР ДЛЯ ПРОВЕРКИ ПОДПИСИ ---
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

# --- КРИПТОГРАФИЧЕСКИЕ ФУНКЦИИ ---
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


# --- ЛОГИКА ОТПРАВКИ СООБЩЕНИЙ ---
def send_text_message(text, phone_number):
    """Отправляет простое текстовое сообщение."""
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": str(phone_number),
        "type": "text",
        "text": {"preview_url": False, "body": text}
    })
    send_request_to_graph_api(payload)

def send_flow_message(flow_id, screen_id, flow_header, flow_body, phone_number):
    """Создает и отправляет сообщение для запуска Flow."""
    flow_token = str(uuid.uuid4())
    interactive_payload = {
        "type": "flow",
        "header": {"type": "text", "text": flow_header},
        "body": {"text": flow_body},
        "footer": {"text": "Нажмите кнопку, чтобы начать"},
        "action": {
            "name": "flow",
            "parameters": {
                "flow_message_version": "3",
                "flow_token": flow_token,
                "flow_id": flow_id,
                "flow_cta": "Начать",
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
    """Общая функция для отправки POST-запроса в Graph API."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + ACCESS_TOKEN,
    }
    
    # --- ДОБАВЛЕНО ЛОГИРОВАНИЕ ---
    # Этот код выведет в лог точный JSON, который мы отправляем в Meta
    print("--- Отправка данных в Meta ---")
    print(payload)
    print("-----------------------------")
    
    try:
        response = requests.post(GRAPH_API_URL, headers=headers, data=payload)
        response.raise_for_status()
        print("Сообщение успешно отправлено!")
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при отправке сообщения: {e}")
        # Дополнительно выведем ответ от сервера Meta, если он есть
        if e.response is not None:
            print(f"Ответ сервера Meta: {e.response.text}")

# --- ЛОГИКА ОБРАБОТКИ ВЕБХУКОВ ---
def process_text_message(message_body, phone_number, name):
    """Обрабатывает входящие текстовые сообщения и решает, как ответить."""
    user_input = message_body.lower()
    
    if "привет" in user_input or "здравствуйте" in user_input:
        reply_text = f"Здравствуйте, {name}! Я ваш помощник по подбору авто из Кореи. Чтобы начать, просто напишите 'подбор'."
        send_text_message(reply_text, phone_number)
        
    elif "подбор" in user_input:
        send_flow_message(
            flow_id="16208261822239246",
            screen_id="WELCOME_SCREEN",
            flow_header="Подбор Авто",
            flow_body="Нажмите 'Начать', чтобы запустить подбор автомобиля вашей мечты.",
            phone_number=phone_number
        )
    else:
        reply_text = "Я не совсем понял ваш запрос. Напишите 'подбор', чтобы начать процесс выбора автомобиля."
        send_text_message(reply_text, phone_number)

def process_flow_completion(response_json, phone_number, name):
    """Обрабатывает данные из завершенного статичного Flow."""
    flow_data = json.loads(response_json)
    flow_key = flow_data.get("flow_key")
    
    if flow_key == "contact":
        firstname = flow_data.get("firstname", "")
        issue = flow_data.get("issue", "")
        reply = f"Спасибо, {firstname}! Мы получили ваше сообщение: '{issue}'. Скоро свяжемся с вами."
        send_text_message(reply, phone_number)
    else:
        print(f"Получено завершение неизвестного Flow с ключом: {flow_key}")


# --- ОСНОВНОЙ ENDPOINT ---
@app.route('/api/whatsapp', methods=['GET', 'POST'])
@validate_signature
def whatsapp_endpoint():
    
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            print("Вебхук верифицирован!")
            return challenge, 200
        else:
            print("Ошибка верификации вебхука.")
            return 'Токен верификации не совпадает', 403

    elif request.method == 'POST':
        request_body = request.get_json()
        
        if 'encrypted_aes_key' in request_body:
            try:
                decrypted_data, aes_key = decrypt_request(
                    request_body['encrypted_aes_key'],
                    request_body['initial_vector'],
                    request_body['encrypted_flow_data']
                )
                print("Расшифрованные данные Flow:", decrypted_data)

                flow_action = decrypted_data.get('action')
                response_payload = {"version": "3.0", "screen": "ERROR_SCREEN", "data": {}}
                if flow_action == 'INIT':
                    response_payload = {"version": "3.0", "screen": "WELCOME_SCREEN", "data": {}}
                
                encrypted_response_body = encrypt_response(aes_key, response_payload)
                return Response(encrypted_response_body, status=200, mimetype='text/plain')

            except (InvalidTag, ValueError, KeyError) as e:
                print(f"Ошибка расшифровки: {e}")
                return Response("Ошибка расшифровки", status=421, mimetype='text/plain')
        
        else:
            try:
                changes = request_body['entry'][0]['changes'][0]['value']
                message_object = changes.get('messages')
                
                if message_object:
                    message_data = message_object[0]
                    phone_number = message_data['from']
                    name = changes['contacts'][0]['profile']['name']
                    
                    if 'text' in message_data:
                        message_body = message_data['text']['body']
                        process_text_message(message_body, phone_number, name)
                    
                    elif 'interactive' in message_data and 'nfm_reply' in message_data['interactive']:
                        response_json = message_data['interactive']['nfm_reply']['response_json']
                        process_flow_completion(response_json, phone_number, name)

                return jsonify(status="ok"), 200

            except (KeyError, IndexError) as e:
                print(f"Ошибка обработки стандартного вебхука: {e}")
                return jsonify(status="error", reason="malformed data"), 400
