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
DATABASE_URL = os.environ.get("DATABASE_URL") # URL вашей базы данных из Render
MANAGER_PHONE_NUMBER = os.environ.get("MANAGER_PHONE_NUMBER") # WhatsApp номер менеджера

# --- Проверка наличия переменных окружения ---
# ... (код проверки остается таким же) ...

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
    # Таблица клиентов
    cur.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            phone_number VARCHAR(50) UNIQUE NOT NULL,
            name VARCHAR(100),
            status VARCHAR(50) DEFAULT 'new',
            managed_by_manager BOOLEAN DEFAULT FALSE
        );
    ''')
    # Таблица сообщений
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

# --- ДЕКОРАТОР И ФУНКЦИИ ОТПРАВКИ СООБЩЕНИЙ ---
# ... (validate_signature и send_text_message остаются такими же) ...

# --- ОСНОВНАЯ ЛОГИКА БОТА ---

def process_chat_message(message_body, phone_number, name):
    """Обрабатывает входящие сообщения."""
    conn = get_db_connection()
    cur = conn.cursor()

    # Находим или создаем клиента
    cur.execute("SELECT id, status, managed_by_manager FROM clients WHERE phone_number = %s", (phone_number,))
    client = cur.fetchone()
    if not client:
        cur.execute("INSERT INTO clients (phone_number, name) VALUES (%s, %s) RETURNING id, status, managed_by_manager", (phone_number, name))
        client = cur.fetchone()
    client_id, client_status, managed_by_manager = client

    # Сохраняем сообщение клиента
    cur.execute("INSERT INTO messages (client_id, message_text, sender_is_bot) VALUES (%s, %s, %s)",
                (client_id, message_body, False))
    conn.commit()

    # --- ЛОГИКА ДЛЯ МЕНЕДЖЕРА ---
    if phone_number == MANAGER_PHONE_NUMBER:
        # Команда для взятия управления: /takeover 821095560770
        if message_body.lower().startswith('/takeover '):
            client_to_manage = message_body.split(' ')[1]
            cur.execute("UPDATE clients SET managed_by_manager = TRUE WHERE phone_number = %s RETURNING id", (client_to_manage,))
            if cur.fetchone():
                send_text_message(f"Вы взяли управление чатом с клиентом {client_to_manage}", MANAGER_PHONE_NUMBER)
            else:
                send_text_message("Клиент не найден.", MANAGER_PHONE_NUMBER)
        # Команда для возврата управления боту: /release 821095560770
        elif message_body.lower().startswith('/release '):
            client_to_release = message_body.split(' ')[1]
            cur.execute("UPDATE clients SET managed_by_manager = FALSE WHERE phone_number = %s", (client_to_release,))
            send_text_message(f"Вы вернули управление боту для клиента {client_to_release}", MANAGER_PHONE_NUMBER)
        # Если это не команда, пересылаем сообщение клиенту
        else:
             # Находим активный чат с менеджером
            cur.execute("SELECT phone_number FROM clients WHERE managed_by_manager = TRUE")
            active_client_phone = cur.fetchone()
            if active_client_phone:
                send_text_message(message_body, active_client_phone[0])
                # Логируем сообщение менеджера
                cur.execute("SELECT id FROM clients WHERE phone_number = %s", (active_client_phone[0],))
                active_client_id = cur.fetchone()[0]
                cur.execute("INSERT INTO messages (client_id, message_text, sender_is_bot) VALUES (%s, %s, %s)", (active_client_id, message_body, False)) # sender_is_bot = False, т.к. это человек
                conn.commit()
        
        cur.close()
        conn.close()
        return

    # Если чатом управляет менеджер, пересылаем ему сообщение клиента
    if managed_by_manager:
        manager_message = f"Сообщение от клиента {name} ({phone_number}):\n\n{message_body}"
        send_text_message(manager_message, MANAGER_PHONE_NUMBER)
        cur.close()
        conn.close()
        return

    # --- ЛОГИКА АВТООТВЕТЧИКА (старый код) ---
    # ... (Ваша логика с вопросами про бюджет и тип авто) ...
    # Не забудьте обновлять статус клиента в базе данных
    # Например: cur.execute("UPDATE clients SET status = 'interested' WHERE id = %s", (client_id,))

    cur.close()
    conn.close()

# --- ОСНОВНОЙ ENDPOINT ---
@app.route('/api/whatsapp', methods=['GET', 'POST'])
@validate_signature
def whatsapp_endpoint():
    # ... (код для GET и POST запросов остается таким же, он вызывает process_chat_message) ...

if __name__ == '__main__':
    # Инициализируем базу данных при старте приложения
    init_db()
    app.run()
