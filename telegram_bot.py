# telegram_bot.py
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
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
MANAGER_CHAT_ID = os.environ.get("MANAGER_CHAT_ID")

# --- Проверка наличия переменных окружения ---
if not all([TELEGRAM_BOT_TOKEN, DATABASE_URL, MANAGER_CHAT_ID]):
    raise ValueError("One or more required environment variables for Telegram bot are not set.")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# --- ЛОГИКА ОТПРАВКИ СООБЩЕНИЙ (для Telegram) ---
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
    # (Здесь остается вся ваша логика пошагового диалога,
    # которая использует send_telegram_message и chat_id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, dialog_step FROM clients WHERE chat_id = %s", (str(chat_id),))
    client = cur.fetchone()
    if not client:
        # Важно: убедитесь, что в вашей таблице clients колонка для ID называется chat_id
        cur.execute("INSERT INTO clients (chat_id, name) VALUES (%s, %s) RETURNING id, dialog_step", (str(chat_id), name))
        client = cur.fetchone()
    client_id, dialog_step = client[0], client[1]
    
    # ... (и так далее, вся остальная логика диалога)
    
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

# Этот блок не будет выполняться на Render, но полезен для локального теста
if __name__ == '__main__':
    app.run(port=5000)
