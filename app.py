import os
import json
import hmac
import hashlib
import requests
import psycopg2
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
DATABASE_URL = os.environ.get("DATABASE_URL")
MANAGER_PHONE_NUMBER = os.environ.get("MANAGER_PHONE_NUMBER")

# --- Проверка наличия переменных окружения ---
if not all([APP_SECRET, PHONE_NUMBER_ID, ACCESS_TOKEN, DATABASE_URL, MANAGER_PHONE_NUMBER]):
    raise ValueError("One or more required environment variables are not set.")

GRAPH_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def get_db_connection():
    """Устанавливает соединение с базой данных."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    """Создает таблицы в базе данных, если их нет."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            phone_number VARCHAR(50) UNIQUE NOT NULL,
            name VARCHAR(100),
            status VARCHAR(50) DEFAULT 'new',
            managed_by_manager BOOLEAN DEFAULT FALSE,
            dialog_step VARCHAR(50) DEFAULT 'start',
            budget VARCHAR(100),
            car_type VARCHAR(100)
        );
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            client_id INTEGER REFERENCES clients(id),
            message_text TEXT,
            sender_is_bot BOOLEAN,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    conn.commit()
    cur.close()
    conn.close()

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
        print(f"Сообщение успешно отправлено на номер {phone_number}")
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при отправке сообщения: {e}")
        if e.response is not None:
            print(f"Ответ сервера Meta: {e.response.text}")

# --- ЛОГИКА ОБРАБОТКИ ДИАЛОГА ---
def process_chat_message(message_body, phone_number, name):
    """Обрабатывает входящие сообщения и ведет диалог."""
    conn = get_db_connection()
    cur = conn.cursor()

    # Находим или создаем клиента
    cur.execute("SELECT id, dialog_step, managed_by_manager FROM clients WHERE phone_number = %s", (phone_number,))
    client = cur.fetchone()
    if not client:
        cur.execute("INSERT INTO clients (phone_number, name) VALUES (%s, %s) RETURNING id, dialog_step, managed_by_manager", (phone_number, name))
        client = cur.fetchone()
    client_id, dialog_step, managed_by_manager = client

    # Сохраняем сообщение клиента
    cur.execute("INSERT INTO messages (client_id, message_text, sender_is_bot) VALUES (%s, %s, %s)",
                (client_id, message_body, False))
    conn.commit()

    # Логика для менеджера (остается без изменений)
    if phone_number == MANAGER_PHONE_NUMBER:
        # ... (код для команд /takeover и /release) ...
        cur.close()
        conn.close()
        return

    # Если чатом управляет менеджер, пересылаем ему сообщение
    if managed_by_manager:
        manager_message = f"Сообщение от клиента {name} ({phone_number}):\n\n{message_body}"
        send_text_message(manager_message, MANAGER_PHONE_NUMBER)
        cur.close()
        conn.close()
        return
    
    # --- Логика пошагового диалога ---
    user_input = message_body.lower().strip()
    reply_text = ""

    if dialog_step == 'start':
        reply_text = f"Здравствуйте, {name}! Я помогу вам подобрать автомобиль из Кореи. Начнем? (Да/Нет)"
        cur.execute("UPDATE clients SET dialog_step = 'ask_budget' WHERE id = %s", (client_id,))
    
    elif dialog_step == 'ask_budget':
        if user_input == 'да':
            reply_text = "Отлично! Какой у вас бюджет в долларах США? (например, 25000)"
            cur.execute("UPDATE clients SET dialog_step = 'get_budget' WHERE id = %s", (client_id,))
        else:
            reply_text = "Хорошо, если передумаете, просто напишите мне."
            cur.execute("UPDATE clients SET dialog_step = 'start' WHERE id = %s", (client_id,)) # Сброс
    
    elif dialog_step == 'get_budget':
        if user_input.isdigit():
            reply_text = "Принято. Какой тип кузова вас интересует? (например, Седан, Кроссовер, Внедорожник)"
            cur.execute("UPDATE clients SET budget = %s, dialog_step = 'get_car_type' WHERE id = %s", (user_input, client_id))
        else:
            reply_text = "Пожалуйста, введите бюджет цифрами."
            
    elif dialog_step == 'get_car_type':
        cur.execute("SELECT budget FROM clients WHERE id = %s", (client_id,))
        budget = cur.fetchone()[0]
        reply_text = f"Спасибо! Ваш запрос записан:\n\n*Тип авто*: {message_body}\n*Бюджет*: до ${budget}\n\nНаш менеджер скоро с вами свяжется."
        cur.execute("UPDATE clients SET car_type = %s, dialog_step = 'done', status = 'completed' WHERE id = %s", (message_body, client_id))

    if reply_text:
        send_text_message(reply_text, phone_number)
        cur.execute("INSERT INTO messages (client_id, message_text, sender_is_bot) VALUES (%s, %s, %s)",
                    (client_id, reply_text, True))

    conn.commit()
    cur.close()
    conn.close()

# --- ОСНОВНОЙ ENDPOINT ---
@app.route('/api/whatsapp', methods=['GET', 'POST'])
@validate_signature
def whatsapp_endpoint():
    if request.method == 'GET':
        # ... (код верификации) ...
        return 'Verification token does not match', 403

    elif request.method == 'POST':
        request_body = request.get_json()
        try:
            changes = request_body['entry'][0]['changes'][0]['value']
            if 'messages' in changes:
                message_data = changes['messages'][0]
                if 'text' in message_data:
                    phone_number = message_data['from']
                    name = changes['contacts'][0]['profile']['name']
                    message_body = message_data['text']['body']
                    process_chat_message(message_body, phone_number, name)
            return jsonify(status="ok"), 200
        except (KeyError, IndexError) as e:
            print(f"Ошибка обработки вебхука: {e}")
            return jsonify(status="error", reason="malformed data"), 400

if __name__ == '__main__':
    init_db()
    app.run()
