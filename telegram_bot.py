import os
import json
import requests
import psycopg2
from functools import wraps

from flask import Flask, request, jsonify
from dotenv import load_dotenv

app = Flask(__name__)
load_dotenv()

# --- КОНФИГУРАЦИЯ ДЛЯ TELEGRAM ---
TELEGRAM_BOT_TOKEN = os.environ.get("8257873112AAH4z7WgpEizp8aeLIC4PU7otNPPCtc47ek")
DATABASE_URL = os.environ.get("https://rsapp.onrender.com")
MANAGER_CHAT_ID = os.environ.get("6427625827")
MANAGER_PASSWORD = os.environ.get("obisar21")

# --- Проверка наличия переменных окружения ---
if not all([TELEGRAM_BOT_TOKEN, DATABASE_URL, MANAGER_CHAT_ID, MANAGER_PASSWORD]):
    raise ValueError("One or more required environment variables for Telegram bot are not set.")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# --- Хранение сессий менеджера ---
manager_sessions = {}

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def get_db_connection():
    """Устанавливает соединение с базой данных."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    """Создает таблицы в базе данных для Telegram-бота."""
    conn = get_db_connection()
    cur = conn.cursor()
    # Обратите внимание, что phone_number заменен на chat_id
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tg_clients (
            id SERIAL PRIMARY KEY,
            chat_id VARCHAR(50) UNIQUE NOT NULL,
            name VARCHAR(100),
            status VARCHAR(50) DEFAULT 'new',
            managed_by_manager BOOLEAN DEFAULT FALSE,
            dialog_step VARCHAR(50) DEFAULT 'start',
            budget VARCHAR(100),
            car_type VARCHAR(100)
        );
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tg_messages (
            id SERIAL PRIMARY KEY,
            client_id INTEGER REFERENCES tg_clients(id),
            message_text TEXT,
            sender_is_bot BOOLEAN,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    conn.commit()
    cur.close()
    conn.close()

# --- ЛОГИКА ОТПРАВКИ СООБЩЕНИЙ ---
def send_telegram_message(text, chat_id):
    """Отправляет текстовое сообщение в Telegram."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(TELEGRAM_API_URL, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        print(f"Сообщение успешно отправлено в чат {chat_id}")
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при отправке сообщения: {e}")
        if e.response is not None:
            print(f"Ответ сервера Telegram: {e.response.text}")

# --- ЛОГИКА ОБРАБОТКИ ДИАЛОГА ---
def process_chat_message(message_body, chat_id, name):
    """Обрабатывает входящие сообщения и ведет диалог."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # --- ЛОГИКА ДЛЯ МЕНЕДЖЕРА ---
    if str(chat_id) == MANAGER_CHAT_ID:
        # ... (здесь будет ваша логика для команд /login, /list, /takeover) ...
        send_telegram_message("Команда от менеджера получена.", chat_id)
        cur.close()
        conn.close()
        return

    # --- ЛОГИКА ДЛЯ КЛИЕНТА ---
    # Находим или создаем клиента
    cur.execute("SELECT id, dialog_step, managed_by_manager FROM tg_clients WHERE chat_id = %s", (str(chat_id),))
    client = cur.fetchone()
    if not client:
        cur.execute("INSERT INTO tg_clients (chat_id, name) VALUES (%s, %s) RETURNING id, dialog_step, managed_by_manager", (str(chat_id), name))
        client = cur.fetchone()
    client_id, dialog_step, managed_by_manager = client

    # Сохраняем сообщение клиента
    cur.execute("INSERT INTO tg_messages (client_id, message_text, sender_is_bot) VALUES (%s, %s, %s)",
                (client_id, message_body, False))
    conn.commit()

    # Если чатом управляет менеджер, пересылаем ему сообщение
    if managed_by_manager:
        manager_message = f"Сообщение от клиента {name} ({chat_id}):\n\n{message_body}"
        send_telegram_message(manager_message, MANAGER_CHAT_ID)
        cur.close()
        conn.close()
        return
    
    # --- Логика пошагового диалога ---
    user_input = message_body.lower().strip()
    reply_text = ""

    if dialog_step == 'start':
        reply_text = f"Здравствуйте, {name}! Я помогу вам подобрать автомобиль из Кореи. Начнем? (Да/Нет)"
        cur.execute("UPDATE tg_clients SET dialog_step = 'ask_budget' WHERE id = %s", (client_id,))
    
    elif dialog_step == 'ask_budget':
        if user_input == 'да':
            reply_text = "Отлично! Какой у вас бюджет в долларах США? (например, 25000)"
            cur.execute("UPDATE tg_clients SET dialog_step = 'get_budget' WHERE id = %s", (client_id,))
        else:
            reply_text = "Хорошо, если передумаете, просто напишите мне."
            cur.execute("UPDATE tg_clients SET dialog_step = 'start' WHERE id = %s", (client_id,))
    
    elif dialog_step == 'get_budget':
        if user_input.isdigit():
            reply_text = "Принято. Какой тип кузова вас интересует? (например, Седан, Кроссовер, Внедорожник)"
            cur.execute("UPDATE tg_clients SET budget = %s, dialog_step = 'get_car_type' WHERE id = %s", (user_input, client_id))
        else:
            reply_text = "Пожалуйста, введите бюджет цифрами."
            
    elif dialog_step == 'get_car_type':
        cur.execute("SELECT budget FROM tg_clients WHERE id = %s", (client_id,))
        budget = cur.fetchone()[0]
        reply_text = f"Спасибо! Ваш запрос записан:\n\n*Тип авто*: {message_body}\n*Бюджет*: до ${budget}\n\nНаш менеджер скоро с вами свяжется."
        cur.execute("UPDATE tg_clients SET car_type = %s, dialog_step = 'done', status = 'completed' WHERE id = %s", (message_body, client_id))

    if reply_text:
        send_telegram_message(reply_text, chat_id)
        cur.execute("INSERT INTO tg_messages (client_id, message_text, sender_is_bot) VALUES (%s, %s, %s)",
                    (client_id, reply_text, True))

    conn.commit()
    cur.close()
    conn.close()


# --- ОСНОВНОЙ ENDPOINT (для Telegram) ---
@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json()
        if 'message' in data and 'text' in data['message']:
            chat_id = data['message']['chat']['id']
            message_text = data['message']['text']
            user_name = data['message']['from'].get('first_name', 'User')
            process_chat_message(message_text, chat_id, user_name)
        return jsonify(status="ok"), 200
    except Exception as e:
        print(f"Ошибка обработки вебхука Telegram: {e}")
        return jsonify(status="error"), 500
