import os
import json
import hmac
import hashlib
import requests
from functools import wraps

from flask import Flask, request, jsonify, abort
from dotenv import load_dotenv

app = Flask(__name__)
load_dotenv()

# --- КОНФИГУРАЦИЯ ---
VERIFY_TOKEN = "obisar2121!"
APP_SECRET = os.environ.get("APP_SECRET")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN")

# --- Проверка наличия переменных окружения ---
if not APP_SECRET:
    raise ValueError("APP_SECRET environment variable not set.")
if not PHONE_NUMBER_ID:
    raise ValueError("PHONE_NUMBER_ID environment variable not set.")
if not ACCESS_TOKEN:
    raise ValueError("ACCESS_TOKEN environment variable not set.")

GRAPH_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

# --- Словарь для хранения состояния диалога с пользователем ---
# В реальном приложении это была бы база данных (например, Redis или PostgreSQL)
user_sessions = {}

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

# --- ЛОГИКА ОТПРАВКИ СООБЩЕНИЙ ---
def send_text_message(text, phone_number):
    """Отправляет простое текстовое сообщение."""
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": str(phone_number),
        "type": "text",
        "text": {"preview_url": False, "body": text}
    })
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + ACCESS_TOKEN,
    }
    try:
        response = requests.post(GRAPH_API_URL, headers=headers, data=payload)
        response.raise_for_status()
        print(f"Message sent to {phone_number}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending message: {e}")

# --- ЛОГИКА ОБРАБОТКИ ДИАЛОГА ---
def process_chat_message(message_body, phone_number, name):
    """Обрабатывает входящие сообщения и ведет диалог."""
    user_input = message_body.lower().strip()
    session = user_sessions.get(phone_number, {})

    # Этап 1: Начало диалога
    if not session:
        user_sessions[phone_number] = {"step": "start"}
        reply_text = f"Здравствуйте, {name}! Я помогу вам подобрать автомобиль из Кореи. Начнем? (Да/Нет)"
        send_text_message(reply_text, phone_number)
        return

    # Этап 2: Получение бюджета
    if session.get("step") == "start":
        if user_input == "да":
            user_sessions[phone_number]["step"] = "get_budget"
            reply_text = "Отлично! Какой у вас бюджет в долларах США? (например, 25000)"
            send_text_message(reply_text, phone_number)
        else:
            reply_text = "Хорошо, если передумаете, просто напишите мне."
            send_text_message(reply_text, phone_number)
            user_sessions.pop(phone_number, None) # Завершаем сессию
        return

    # Этап 3: Получение типа кузова
    if session.get("step") == "get_budget":
        if user_input.isdigit():
            user_sessions[phone_number]["budget"] = user_input
            user_sessions[phone_number]["step"] = "get_car_type"
            reply_text = "Принято. Какой тип кузова вас интересует? (например, Седан, Кроссовер, Внедорожник)"
            send_text_message(reply_text, phone_number)
        else:
            reply_text = "Пожалуйста, введите бюджет цифрами."
            send_text_message(reply_text, phone_number)
        return

    # Этап 4: Завершение
    if session.get("step") == "get_car_type":
        user_sessions[phone_number]["car_type"] = user_input
        budget = user_sessions[phone_number].get('budget')
        car_type = user_sessions[phone_number].get('car_type')

        reply_text = f"Спасибо! Ваш запрос записан:\n\n*Тип авто*: {car_type}\n*Бюджет*: до ${budget}\n\nНаш менеджер скоро с вами свяжется."
        send_text_message(reply_text, phone_number)
        user_sessions.pop(phone_number, None) # Завершаем сессию
        return

# --- ОСНОВНОЙ ENDPOINT ---
@app.route('/api/whatsapp', methods=['GET', 'POST'])
@validate_signature
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
        try:
            changes = request_body['entry'][0]['changes'][0]['value']
            message_object = changes.get('messages')
            
            if message_object:
                message_data = message_object[0]
                if 'text' in message_data:
                    phone_number = message_data['from']
                    name = changes['contacts'][0]['profile']['name']
                    message_body = message_data['text']['body']
                    process_chat_message(message_body, phone_number, name)

            return jsonify(status="ok"), 200

        except (KeyError, IndexError) as e:
            print(f"Error processing webhook: {e}")
            return jsonify(status="error", reason="malformed data"), 400
