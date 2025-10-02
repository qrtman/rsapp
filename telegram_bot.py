import os
import json
import requests
import psycopg2
from functools import wraps

from flask import Flask, request, jsonify
from dotenv import load_dotenv

# --- ИНИЦИАЛИЗАЦИЯ И КОНФИГУРАЦИЯ ---
app = Flask(__name__)
load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
MANAGER_CHAT_ID = os.environ.get("MANAGER_CHAT_ID")
MANAGER_PASSWORD = os.environ.get("MANAGER_PASSWORD")

# Проверка, что все переменные окружения установлены
if not all([TELEGRAM_BOT_TOKEN, DATABASE_URL, MANAGER_CHAT_ID, MANAGER_PASSWORD]):
    raise ValueError("Одна или несколько переменных окружения не установлены. Проверьте TELEGRAM_BOT_TOKEN, DATABASE_URL, MANAGER_CHAT_ID, MANAGER_PASSWORD на Render.")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
manager_sessions = {} # Временное хранилище для статуса входа менеджера

# --- ОПТИМИЗИРОВАННАЯ РАБОТА С БАЗОЙ ДАННЫХ ---
def get_db_connection():
    """Создает соединение с базой данных."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    """Создает таблицы в базе данных, если они не существуют."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Создание таблицы клиентов
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
        # Создание таблицы сообщений
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
        print("База данных успешно инициализирована.")
    except Exception as e:
        print(f"Ошибка при инициализации базы данных: {e}")

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def send_telegram_message(text, chat_id):
    """Отправляет сообщение через Telegram Bot API."""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(TELEGRAM_API_URL, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        print(f"Сообщение успешно отправлено в чат {chat_id}")
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при отправке сообщения: {e}")
        if e.response: print(f"Ответ сервера Telegram: {e.response.text}")

# --- ОСНОВНАЯ ЛОГИКА ОБРАБОТКИ СООБЩЕНИЙ ---
def process_chat_message(message_body, chat_id, name):
    conn = get_db_connection()
    cur = conn.cursor()
    chat_id_str = str(chat_id)

    # --- ЛОГИКА ДЛЯ МЕНЕДЖЕРА ---
    if chat_id_str == MANAGER_CHAT_ID:
        if message_body.lower().startswith('/login '):
            pwd = message_body.split(' ', 1)[1]
            if pwd == MANAGER_PASSWORD:
                manager_sessions[chat_id_str] = {"logged_in": True}
                send_telegram_message("✅ Вход выполнен. Доступны команды:\n`/list`\n`/takeover <chat_id>`", chat_id_str)
            else:
                send_telegram_message("❌ Неверный пароль.", chat_id_str)
        
        elif not manager_sessions.get(chat_id_str, {}).get("logged_in"):
            send_telegram_message("Пожалуйста, войдите: `/login <пароль>`", chat_id_str)
        
        elif message_body.lower() == '/list':
            cur.execute("SELECT name, chat_id, status FROM tg_clients ORDER BY id DESC LIMIT 10;")
            clients = cur.fetchall()
            reply = "Последние 10 клиентов:\n\n" if clients else "Список клиентов пуст."
            for client in clients:
                reply += f"👤 *{client[0]}*\n📞 `{client[1]}`\n💡 Статус: {client[2]}\n\n"
            send_telegram_message(reply, chat_id_str)

        elif message_body.lower().startswith('/takeover '):
            try:
                client_to_manage = message_body.split(' ', 1)[1]
                # Сначала "отпускаем" все предыдущие чаты, чтобы избежать путаницы
                cur.execute("UPDATE tg_clients SET managed_by_manager = FALSE RETURNING chat_id, name;")
                released_clients = cur.fetchall()
                
                # Теперь берем новый чат под управление
                cur.execute("UPDATE tg_clients SET managed_by_manager = TRUE WHERE chat_id = %s RETURNING name;", (client_to_manage,))
                client_name = cur.fetchone()
                
                if client_name:
                    # Уведомляем менеджера
                    send_telegram_message(f"Вы взяли управление чатом с {client_name[0]} (`{client_to_manage}`). Теперь все ваши сообщения будут пересылаться ему.", chat_id_str)
                    # Уведомляем нового клиента
                    send_telegram_message("К вам подключился менеджер. Он ответит на все ваши вопросы.", client_to_manage)
                    # Уведомляем старых клиентов, если они были
                    for r_client in released_clients:
                        if r_client[0] != client_to_manage:
                           send_telegram_message("Менеджер отключился. Вам снова отвечает бот.", r_client[0])
                else: 
                    send_telegram_message("Клиент с таким `chat_id` не найден.", chat_id_str)
            except IndexError:
                send_telegram_message("Неверный формат. Используйте: `/takeover <chat_id>`", chat_id_str)

        else: # Пересылка сообщения от менеджера активному клиенту
            cur.execute("SELECT chat_id FROM tg_clients WHERE managed_by_manager = TRUE;")
            active_client = cur.fetchone()
            if active_client:
                client_chat_id = active_client[0]
                send_telegram_message(message_body, client_chat_id)
            else: 
                send_telegram_message("Нет активного чата с клиентом. Используйте `/takeover <chat_id>`.", chat_id_str)
        
        conn.commit()
        cur.close()
        conn.close()
        return

    # --- ЛОГИКА ДЛЯ КЛИЕНТА ---
    cur.execute("SELECT id, dialog_step, managed_by_manager FROM tg_clients WHERE chat_id = %s;", (chat_id_str,))
    client = cur.fetchone()
    if not client:
        cur.execute("INSERT INTO tg_clients (chat_id, name) VALUES (%s, %s) RETURNING id, dialog_step, managed_by_manager;", (chat_id_str, name))
        client = cur.fetchone()
    client_id, dialog_step, managed_by_manager = client

    # Сохраняем сообщение клиента в БД
    cur.execute("INSERT INTO tg_messages (client_id, message_text, sender_is_bot) VALUES (%s, %s, %s);", (client_id, message_body, False))

    if managed_by_manager:
        # Если чат ведет менеджер, пересылаем ему сообщение
        manager_message = f"Сообщение от {name} (`{chat_id_str}`):\n\n{message_body}"
        send_telegram_message(manager_message, MANAGER_CHAT_ID)
    else:
        # Если чат ведет бот, обрабатываем по шагам диалога
        user_input = message_body.lower().strip()
        reply_text = ""
        
        if dialog_step == 'start':
            reply_text = f"Здравствуйте, {name}! Я помогу вам подобрать автомобиль из Кореи. Начнем? (Да/Нет)"
            cur.execute("UPDATE tg_clients SET dialog_step = 'ask_budget' WHERE id = %s;", (client_id,))
        
        elif dialog_step == 'ask_budget':
            if user_input == 'да':
                reply_text = "Отлично! Какой у вас бюджет в долларах США? (например, 25000)"
                cur.execute("UPDATE tg_clients SET dialog_step = 'get_budget' WHERE id = %s;", (client_id,))
            else:
                reply_text = "Хорошо, если передумаете, просто напишите мне."
                cur.execute("UPDATE tg_clients SET dialog_step = 'start' WHERE id = %s;", (client_id,))
        
        elif dialog_step == 'get_budget':
            if user_input.isdigit():
                reply_text = "Принято. Какой тип кузова вас интересует? (например, Седан, Кроссовер, Внедорожник)"
                cur.execute("UPDATE tg_clients SET budget = %s, dialog_step = 'get_car_type' WHERE id = %s;", (user_input, client_id))
            else:
                reply_text = "Пожалуйста, введите бюджет цифрами."
        
        elif dialog_step == 'get_car_type':
            cur.execute("SELECT budget FROM tg_clients WHERE id = %s;", (client_id,))
            budget = cur.fetchone()[0]
            reply_text = f"Спасибо! Ваш запрос записан:\n\n*Тип авто*: {message_body}\n*Бюджет*: до ${budget}\n\nНаш менеджер скоро с вами свяжется."
            cur.execute("UPDATE tg_clients SET car_type = %s, dialog_step = 'done', status = 'completed' WHERE id = %s;", (message_body, client_id))

        if reply_text:
            send_telegram_message(reply_text, chat_id_str)
            # Сохраняем ответ бота в БД
            cur.execute("INSERT INTO tg_messages (client_id, message_text, sender_is_bot) VALUES (%s, %s, %s);", (client_id, reply_text, True))

    conn.commit()
    cur.close()
    conn.close()

# --- WEBHOOK ENDPOINT ---
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
        print(f"Критическая ошибка в вебхуке: {e}")
        return jsonify(status="error"), 500

# --- ЗАПУСК ПРИЛОЖЕНИЯ ---
if __name__ == "__main__":
    # Инициализируем базу данных при локальном запуске
    init_db()
    # При запуске на Render эта часть не выполняется, поэтому init_db() нужно вызывать глобально
    app.run(debug=True)

# Глобальный вызов для инициализации БД при старте на Render
init_db()
