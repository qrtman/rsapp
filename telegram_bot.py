import os
import json
import requests
import psycopg2
from io import BytesIO

from flask import Flask, request, jsonify
from dotenv import load_dotenv

# --- ИНИЦИАЛИЗАЦИЯ И КОНФИГУРАЦИЯ ---
app = Flask(__name__)
load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
MANAGER_CHAT_ID = os.environ.get("MANAGER_CHAT_ID")
MANAGER_PASSWORD = os.environ.get("MANAGER_PASSWORD")

if not all([TELEGRAM_BOT_TOKEN, DATABASE_URL, MANAGER_CHAT_ID, MANAGER_PASSWORD]):
    raise ValueError("Одна или несколько переменных окружения не установлены. Проверьте все 4 переменные на Render.")

# --- API URL-адреса Telegram ---
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
# URL для скачивания файлов (голосовых сообщений)
TELEGRAM_FILE_URL = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"

manager_sessions = {}

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def get_db_connection():
    """Создает соединение с базой данных."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    """Создает таблицы в базе данных, если они не существуют."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS tg_clients (
                id SERIAL PRIMARY KEY, chat_id VARCHAR(50) UNIQUE NOT NULL, name VARCHAR(100),
                status VARCHAR(50) DEFAULT 'new', managed_by_manager BOOLEAN DEFAULT FALSE,
                dialog_step VARCHAR(50) DEFAULT 'start', budget VARCHAR(100), car_type VARCHAR(100)
            );
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS tg_messages (
                id SERIAL PRIMARY KEY, client_id INTEGER REFERENCES tg_clients(id),
                message_text TEXT, is_voice BOOLEAN DEFAULT FALSE,
                sender_is_bot BOOLEAN, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        conn.commit()
        cur.close()
        conn.close()
        print("База данных успешно инициализирована.")
    except Exception as e:
        print(f"Ошибка при инициализации базы данных: {e}")

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С TELEGRAM API ---

def send_telegram_message(text, chat_id, keyboard=None):
    """Отправляет текстовое сообщение. Может прикреплять клавиатуру."""
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при отправке текстового сообщения: {e}")

def send_voice_message(voice_content, chat_id, caption=""):
    """Отправляет голосовое сообщение."""
    url = f"{TELEGRAM_API_URL}/sendVoice"
    files = {'voice': ('voice_message.ogg', voice_content, 'audio/ogg')}
    data = {'chat_id': chat_id, 'caption': caption}
    try:
        response = requests.post(url, files=files, data=data)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при отправке голосового сообщения: {e}")

def get_file_content(file_id):
    """Скачивает файл (голосовое сообщение) с серверов Telegram."""
    try:
        url = f"{TELEGRAM_API_URL}/getFile?file_id={file_id}"
        response = requests.get(url)
        response.raise_for_status()
        file_path = response.json()['result']['file_path']
        
        download_url = f"{TELEGRAM_FILE_URL}/{file_path}"
        file_response = requests.get(download_url)
        file_response.raise_for_status()
        return file_response.content
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при скачивании файла: {e}")
        return None

# --- ОСНОВНАЯ ЛОГИКА БОТА ---

def process_manager_command(message_body, chat_id_str):
    """Обрабатывает все команды и сообщения от менеджера."""
    conn = get_db_connection()
    cur = conn.cursor()

    if message_body.lower().startswith('/login '):
        pwd = message_body.split(' ', 1)[1]
        if pwd == MANAGER_PASSWORD:
            manager_sessions[chat_id_str] = {"logged_in": True}
            send_telegram_message("✅ Вход выполнен.\nКоманды:\n`/list` - список клиентов\n`/takeover <id>` - взять чат\n`/history <id>` - история чата", chat_id_str)
        else:
            send_telegram_message("❌ Неверный пароль.", chat_id_str)
        return

    if not manager_sessions.get(chat_id_str, {}).get("logged_in"):
        send_telegram_message("Пожалуйста, войдите: `/login <пароль>`", chat_id_str)
        return

    if message_body.lower() == '/list':
        cur.execute("SELECT name, chat_id, status FROM tg_clients ORDER BY id DESC LIMIT 10;")
        clients = cur.fetchall()
        reply = "Последние 10 клиентов:\n\n" if clients else "Клиентов нет."
        for client in clients:
            reply += f"👤 *{client[0]}* | Статус: {client[2]}\n`{client[1]}`\n\n"
        send_telegram_message(reply, chat_id_str)

    elif message_body.lower().startswith('/takeover '):
        try:
            client_to_manage = message_body.split(' ', 1)[1]
            cur.execute("UPDATE tg_clients SET managed_by_manager = FALSE;") # Сброс всех
            cur.execute("UPDATE tg_clients SET managed_by_manager = TRUE WHERE chat_id = %s RETURNING name;", (client_to_manage,))
            client_name = cur.fetchone()
            if client_name:
                send_telegram_message(f"✅ Вы управляете чатом с {client_name[0]} (`{client_to_manage}`).", chat_id_str)
                send_telegram_message("К вам подключился менеджер.", client_to_manage)
            else:
                send_telegram_message("Клиент не найден.", chat_id_str)
        except IndexError:
            send_telegram_message("Используйте: `/takeover <chat_id>`", chat_id_str)
            
    elif message_body.lower().startswith('/history '):
        try:
            client_chat_id = message_body.split(' ', 1)[1]
            cur.execute("SELECT m.message_text, m.sender_is_bot, m.is_voice FROM tg_messages m JOIN tg_clients c ON m.client_id = c.id WHERE c.chat_id = %s ORDER BY m.timestamp DESC LIMIT 20", (client_chat_id,))
            messages = cur.fetchall()
            if not messages:
                send_telegram_message("История сообщений для этого клиента пуста.", chat_id_str)
                return
            
            history = f"История чата с `{client_chat_id}`:\n" + "-"*20 + "\n"
            for msg in reversed(messages): # reversed чтобы читать сверху вниз
                sender = "Бот" if msg[1] else "Клиент"
                text = "[Голосовое сообщение]" if msg[2] else msg[0]
                history += f"*{sender}*: {text}\n"
            send_telegram_message(history, chat_id_str)
        except IndexError:
            send_telegram_message("Используйте: `/history <chat_id>`", chat_id_str)

    else: # Если не команда, то это сообщение для клиента
        cur.execute("SELECT chat_id FROM tg_clients WHERE managed_by_manager = TRUE;")
        active_client = cur.fetchone()
        if active_client:
            send_telegram_message(message_body, active_client[0])
        else:
            send_telegram_message("Нет активного чата. Используйте `/takeover <chat_id>`.", chat_id_str)
    
    conn.commit()
    cur.close()
    conn.close()

def process_client_message(message_body, chat_id_str, name):
    """Обрабатывает сообщения от клиента."""
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, dialog_step, managed_by_manager FROM tg_clients WHERE chat_id = %s;", (chat_id_str,))
    client = cur.fetchone()
    if not client:
        cur.execute("INSERT INTO tg_clients (chat_id, name) VALUES (%s, %s) RETURNING id, dialog_step, managed_by_manager;", (chat_id_str, name))
        client = cur.fetchone()
    client_id, dialog_step, managed_by_manager = client

    cur.execute("INSERT INTO tg_messages (client_id, message_text, sender_is_bot) VALUES (%s, %s, FALSE);", (client_id, message_body))

    if managed_by_manager:
        manager_message = f"Сообщение от {name} (`{chat_id_str}`):\n\n{message_body}"
        send_telegram_message(manager_message, MANAGER_CHAT_ID)
    else:
        # Логика диалога с ботом
        user_input = message_body.lower().strip()
        reply_text = ""
        keyboard = None
        
        if dialog_step == 'start':
            reply_text = f"Здравствуйте, {name}! Я помогу вам подобрать автомобиль из Кореи. Начнем?"
            keyboard = {"keyboard": [[{"text": "Да"}], [{"text": "Нет"}]], "one_time_keyboard": True, "resize_keyboard": True}
            cur.execute("UPDATE tg_clients SET dialog_step = 'ask_budget' WHERE id = %s;", (client_id,))
        
        elif dialog_step == 'ask_budget':
            if user_input == 'да':
                reply_text = "Какой у вас бюджет в долларах США? (например, 25000)"
                cur.execute("UPDATE tg_clients SET dialog_step = 'get_budget' WHERE id = %s;", (client_id,))
            else:
                reply_text = "Хорошо, если передумаете, просто напишите."
                cur.execute("UPDATE tg_clients SET dialog_step = 'start' WHERE id = %s;", (client_id,))
        
        elif dialog_step == 'get_budget':
            if user_input.isdigit():
                reply_text = "Принято. Какой тип кузова вас интересует? (Седан, Кроссовер, Внедорожник и т.д.)"
                cur.execute("UPDATE tg_clients SET budget = %s, dialog_step = 'get_car_type' WHERE id = %s;", (user_input, client_id))
            else:
                reply_text = "Пожалуйста, введите бюджет только цифрами."
        
        elif dialog_step == 'get_car_type':
            cur.execute("SELECT budget FROM tg_clients WHERE id = %s;", (client_id,))
            budget = cur.fetchone()[0]
            reply_text = f"Спасибо! Ваш запрос записан:\n\n*Тип авто*: {message_body}\n*Бюджет*: до ${budget}\n\nНаш менеджер скоро с вами свяжется."
            cur.execute("UPDATE tg_clients SET car_type = %s, dialog_step = 'done', status = 'completed' WHERE id = %s;", (message_body, client_id))
            # Уведомление менеджеру о новом запросе
            manager_notification = f"Новый запрос от {name} (`{chat_id_str}`)\nБюджет: до ${budget}\nТип: {message_body}"
            send_telegram_message(manager_notification, MANAGER_CHAT_ID)

        if reply_text:
            send_telegram_message(reply_text, chat_id_str, keyboard)
            cur.execute("INSERT INTO tg_messages (client_id, message_text, sender_is_bot) VALUES (%s, %s, TRUE);", (client_id, reply_text))

    conn.commit()
    cur.close()
    conn.close()

def process_voice_message(file_id, chat_id_str, name):
    """Обрабатывает входящие голосовые сообщения."""
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, managed_by_manager FROM tg_clients WHERE chat_id = %s;", (chat_id_str,))
    client = cur.fetchone()
    if not client:
        cur.execute("INSERT INTO tg_clients (chat_id, name) VALUES (%s, %s) RETURNING id, managed_by_manager;", (chat_id_str, name))
        client = cur.fetchone()
    client_id, managed_by_manager = client
    
    # Сохраняем в БД информацию о том, что это было голосовое
    cur.execute("INSERT INTO tg_messages (client_id, message_text, sender_is_bot, is_voice) VALUES (%s, %s, FALSE, TRUE);", (client_id, "Голосовое сообщение"))
    
    voice_content = get_file_content(file_id)
    if not voice_content:
        return

    if chat_id_str == MANAGER_CHAT_ID: # Если менеджер отправил голосовое
        cur.execute("SELECT chat_id FROM tg_clients WHERE managed_by_manager = TRUE;")
        active_client = cur.fetchone()
        if active_client:
            send_voice_message(voice_content, active_client[0])
    else: # Если клиент отправил голосовое
        caption = f"Голосовое от {name} (`{chat_id_str}`)"
        send_voice_message(voice_content, MANAGER_CHAT_ID, caption)

    conn.commit()
    cur.close()
    conn.close()


# --- WEBHOOK ENDPOINT ---
@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json()
        
        # Проверяем, есть ли в сообщении текст
        if 'message' in data and 'text' in data['message']:
            chat_id = data['message']['chat']['id']
            chat_id_str = str(chat_id)
            message_text = data['message']['text']
            user_name = data['message']['from'].get('first_name', 'User')

            if chat_id_str == MANAGER_CHAT_ID:
                process_manager_command(message_text, chat_id_str)
            else:
                process_client_message(message_text, chat_id_str, user_name)

        # Проверяем, есть ли в сообщении голос
        elif 'message' in data and 'voice' in data['message']:
            chat_id = data['message']['chat']['id']
            chat_id_str = str(chat_id)
            user_name = data['message']['from'].get('first_name', 'User')
            file_id = data['message']['voice']['file_id']
            
            process_voice_message(file_id, chat_id_str, user_name)

        return jsonify(status="ok"), 200
    except Exception as e:
        print(f"Критическая ошибка в вебхуке: {e}")
        return jsonify(status="error"), 500

# --- ЗАПУСК ПРИЛОЖЕНИЯ ---
# Глобальный вызов для инициализации БД при старте на Render
init_db()

if __name__ == "__main__":
    # Эта часть выполняется только при локальном запуске (python telegram_bot.py)
    # На Render она не выполняется, поэтому init_db() вынесен выше
    app.run(debug=True, port=5001)
