import os
import telebot
import mysql.connector
import pyotp
import qrcode
import logging
import requests
import secrets
import string
from io import BytesIO
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton


# ==================== Налаштування Telegram бота ====================
TOKEN = os.getenv("TELEGRAM_TOKEN")
first_moderator_id = os.getenv("MODERATOR_ID")
bot = telebot.TeleBot(TOKEN)

logging.basicConfig(level=logging.INFO, filename="bot.log", format="%(asctime)s - %(levelname)s - %(message)s")

# ==================== Допоміжна функція для роботи з базою даних ====================
def execute_db(query, params=None, fetchone=False, commit=False):
    try:
        connection = mysql.connector.connect(
            host=os.getenv("DB_HOST", "db"),
            user=os.getenv("DB_USER", "test"),
            password=os.getenv("DB_PASSWORD", "3324MMMM"),
            database=os.getenv("DB_NAME", "telegram")
        )
        cursor = connection.cursor()
        cursor.execute(query, params)
        result = None
        if commit:
            connection.commit()
        else:
            result = cursor.fetchone() if fetchone else cursor.fetchall()
        cursor.close()
        connection.close()
        return result
    except mysql.connector.Error as err:
        logging.error(f"Error executing query: {err}")
        return None


create_groups_table = """
CREATE TABLE IF NOT EXISTS groups_for_hetzner (
    group_name VARCHAR(255) NOT NULL,
    key_hetzner VARCHAR(255) NOT NULL,
    group_signature VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
    PRIMARY KEY (group_name)
);
"""
create_users_table = """
CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(50) NOT NULL,
    username VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
    group_name VARCHAR(255) NOT NULL,
    secret_key VARCHAR(255) NOT NULL,
    PRIMARY KEY (user_id),
    FOREIGN KEY (group_name) REFERENCES groups_for_hetzner(group_name)
);
"""
create_time_secret_key = """
CREATE TABLE IF NOT EXISTS time_key (
    group_name VARCHAR(255) NOT NULL,
    time_key VARCHAR(255) NOT NULL,
    FOREIGN KEY (group_name) REFERENCES groups_for_hetzner(group_name) ON DELETE CASCADE
);
"""
create_admins_table = """
CREATE TABLE IF NOT EXISTS admins_2fa (
    admin_id VARCHAR(50) NOT NULL PRIMARY KEY,
    username VARCHAR(255) NOT NULL,
    secret_key VARCHAR(255) NOT NULL
);
"""
create_pending_admins_table = """
CREATE TABLE IF NOT EXISTS pending_admins (
    moderator_id VARCHAR(50) NOT NULL PRIMARY KEY
);
"""
create_hetzner_servers_table = """
CREATE TABLE IF NOT EXISTS hetzner_servers (
    group_name VARCHAR(255) NOT NULL,
    server_id VARCHAR(255) NOT NULL,
    server_name VARCHAR(255) DEFAULT NULL,
    PRIMARY KEY (group_name, server_id),
    FOREIGN KEY (group_name) REFERENCES groups_for_hetzner(group_name) ON DELETE CASCADE
);
"""
create_blocked_users = """
CREATE TABLE IF NOT EXISTS blocked_users (
    user_id VARCHAR(50) PRIMARY KEY,
    block_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    nickname VARCHAR(255),
    reason TEXT
);
"""

# Виконуємо створення таблиць
for query in [create_blocked_users, create_groups_table, create_users_table, create_time_secret_key,
              create_admins_table, create_pending_admins_table, create_hetzner_servers_table]:
    execute_db(query, commit=True)

# ==================== Глобальні змінні та клавіатури ====================
main_markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
main_markup.add(KeyboardButton("/my_id"), KeyboardButton("/server_control"))

qr_message_id = {}
admin_qr_msg_id = {}
registration_info = {}   # дані реєстрації користувача
selected_server = {}     # зберігає вибір сервера користувача (chat_id -> server_id)
pending_deletion = {}
secret_message_id = {}
admin_secret_message_id = {}
pending_removals = {}
wrong_attempts = {}
pending_unblock = {}
users_cache = set()
admins_cache = set()

# ==================== Функції перевірки прав доступу ====================
def update_users_cache():
    global users_cache, admins_cache
    # Отримуємо всіх користувачів (прості користувачі)
    users = execute_db("SELECT user_id FROM users", fetchone=False)
    # Отримуємо всіх адміністраторів
    admins = execute_db("SELECT admin_id FROM admins_2fa", fetchone=False)
    users_cache = {str(row[0]) for row in users} if users else set()
    admins_cache = {str(row[0]) for row in admins} if admins else set()

# Спочатку оновлюємо кеш при старті
update_users_cache()

def is_admin(user_id):
    return str(user_id) in admins_cache

def is_user(user_id):
    uid = str(user_id)
    return uid in users_cache or uid in admins_cache

def get_group_by_user(user_id):
    result = execute_db("SELECT group_name FROM users WHERE user_id = %s", (str(user_id),), fetchone=True)
    return result[0] if result else None

def get_hetzner_key(group_name):
    result = execute_db("SELECT key_hetzner FROM groups_for_hetzner WHERE group_name = %s", (group_name,), fetchone=True)
    return result[0] if result else None

def get_admin_secret_key(user_id):
    result = execute_db("SELECT secret_key FROM admins_2fa WHERE admin_id = %s", (str(user_id),), fetchone=True)
    return result[0] if result else None

def get_user_secret(user_id):
    result = execute_db("SELECT secret_key FROM users WHERE user_id = %s", (str(user_id),), fetchone=True)
    return result[0] if result else None

# ==================== Меню та команди для Telegram бота ====================
def send_commands_menu(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    user_commands = ["мій айді", "керування сервером"]
    admin_commands = ["групи", "розблокувати користувача", "модератори"]
    buttons = admin_commands + user_commands if is_admin(message.from_user.id) else user_commands
    for button in buttons:
        markup.add(button)
    bot.send_message(message.chat.id, "Оберіть команду або вкладку:", reply_markup=markup)

@bot.message_handler(commands=["start"])
def start(message):
    if is_user(message.from_user.id) or is_admin(message.from_user.id):
        send_commands_menu(message)

@bot.message_handler(func=lambda message: message.text.strip().lower() == "мій айді")
def my_id(message):
    bot.reply_to(message, f"Ваш user ID: {message.chat.id}")
    send_commands_menu(message)

# ==================== Реєстрація користувача ====================
@bot.message_handler(commands=["register"])
def register(message):
    if execute_db("SELECT * FROM users WHERE user_id = %s", (str(message.chat.id),), fetchone=True):
        bot.send_message(message.chat.id, "Ви вже зареєстровані.")
        return
    bot.register_next_step_handler(message, verify_one_time_code)

def verify_one_time_code(message):
    user_id = message.chat.id
    one_time_code = message.text.strip()
    result = execute_db("SELECT group_name FROM time_key WHERE time_key = %s", (one_time_code,), fetchone=True)
    if result:
        wrong_attempts.pop(user_id, None)
        group_name = result[0]
        execute_db("DELETE FROM time_key WHERE time_key = %s AND group_name = %s", (one_time_code, group_name), commit=True)
        username = message.chat.username if message.chat.username else message.from_user.first_name
        secret = pyotp.random_base32()
        registration_info[str(user_id)] = {"username": username, "group_name": group_name, "secret": secret}
        send_qr(message, secret)
    else:
        logging.warning(f"Користувач {user_id} ввів невірний тимчасовий код.")
        wrong_attempts[user_id] = wrong_attempts.get(user_id, 0) + 1
        if wrong_attempts[user_id] >= 5:
            nickname = message.chat.username if message.chat.username else message.from_user.first_name
            execute_db("INSERT IGNORE INTO blocked_users (user_id, nickname, reason) VALUES (%s, %s, %s)",
                       (str(user_id), nickname, "Вичерпано кількість спроб введення тимчасового коду"), commit=True)
            logging.error(f"Користувач {user_id} заблокований після 5 невдалих спроб.")
        else:
            bot.register_next_step_handler(message, verify_one_time_code)

def send_qr(message, secret):
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(
        name=message.chat.username if message.chat.username else message.from_user.first_name,
        issuer_name="hetzner_bot_control"
    )
    qr = qrcode.make(uri)
    bio = BytesIO()
    qr.save(bio, format="PNG")
    bio.seek(0)
    sent_msg = bot.send_photo(
        message.chat.id,
        bio,
        caption="Відскануйте QR-код для Google Authenticator або скопіюйте код який знаходиться нижче."
    )
    qr_message_id[message.chat.id] = sent_msg.message_id
    secret_msg = bot.send_message(message.chat.id, f"{secret}")
    secret_message_id[message.chat.id] = secret_msg.message_id
    bot.send_message(message.chat.id, "Введіть код з аутентифікатора:")
    bot.register_next_step_handler(message, verify_2fa, secret)

def verify_2fa(message, secret):
    totp = pyotp.TOTP(secret)
    if totp.verify(message.text.strip()):
        bot.send_message(message.chat.id, "✅ Код правильний! Реєстрація завершена.")
        info = registration_info.get(str(message.chat.id))
        if info:
            try:
                execute_db(
                    "INSERT INTO users (user_id, username, group_name, secret_key) VALUES (%s, %s, %s, %s)",
                    (str(message.chat.id), info["username"], info["group_name"], info["secret"]),
                    commit=True
                )
                # Оновлення кешу після додавання користувача
                update_users_cache()
            except Exception as err:
                bot.send_message(message.chat.id, f"Помилка збереження даних: {err}")
            registration_info.pop(str(message.chat.id), None)
        send_commands_menu(message)
        try:
            bot.delete_message(message.chat.id, qr_message_id[message.chat.id])
        except Exception as e:
            print(f"Помилка видалення QR-коду: {e}")
        try:
            bot.delete_message(message.chat.id, secret_message_id[message.chat.id])
        except Exception as e:
            print(f"Помилка видалення секретного коду: {e}")
    else:
        bot.send_message(message.chat.id, "❌ Невірний код. Будь ласка, спробуйте ще раз.")
        bot.register_next_step_handler(message, verify_2fa, secret)

# ==================== Команди для модераторів ====================
@bot.message_handler(func=lambda message: message.text.strip().lower() == "розблокувати користувача")
def unblock_user(message):
    if not is_admin(message.from_user.id):
        return
    blocked = execute_db("SELECT user_id, nickname FROM blocked_users", fetchone=False)
    if not blocked:
        bot.send_message(message.chat.id, "Немає заблокованих користувачів.")
        return
    markup = InlineKeyboardMarkup()
    for user in blocked:
        user_id, nickname = user
        display = nickname if nickname and nickname.strip() != "" else f"ID: {user_id}"
        markup.add(InlineKeyboardButton(f"Розблокувати {display}", callback_data=f"confirm_unblock:{user_id}"))
    bot.send_message(message.chat.id, "Оберіть користувача для розблокування:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_unblock:"))
def confirm_unblock_callback(call):
    if not is_admin(call.from_user.id):
        return
    parts = call.data.split(":", 1)
    if len(parts) != 2:
        bot.answer_callback_query(call.id, "Невірний формат даних.")
        return
    unblock_user_id = parts[1]
    admin_id = call.from_user.id
    pending_unblock[admin_id] = unblock_user_id
    bot.answer_callback_query(call.id, "Будь ласка, введіть свій 2FA-код для підтвердження розблокування.")
    bot.send_message(call.message.chat.id, "Введіть свій 2FA-код для підтвердження розблокування:")
    bot.register_next_step_handler(call.message, process_unblock_2fa)

def process_unblock_2fa(message):
    admin_id = message.from_user.id
    admin_secret = get_admin_secret_key(admin_id)
    if not admin_secret:
        bot.send_message(message.chat.id, "Не знайдено ваш секретний ключ для 2FA.")
        pending_unblock.pop(admin_id, None)
        return
    totp = pyotp.TOTP(admin_secret)
    if not totp.verify(message.text.strip()):
        bot.send_message(message.chat.id, "❌ Невірний 2FA-код. Операція скасована.")
        pending_unblock.pop(admin_id, None)
        return
    unblock_user_id = pending_unblock.pop(admin_id)
    result = execute_db("SELECT nickname FROM blocked_users WHERE user_id = %s", (str(unblock_user_id),), fetchone=True)
    if result:
        nickname = result[0]
        execute_db("DELETE FROM blocked_users WHERE user_id = %s", (str(unblock_user_id),), commit=True)
        wrong_attempts.pop(unblock_user_id, None)
        logging.info(f"Користувача {unblock_user_id} ({nickname}) розблоковано адміністратором {admin_id}.")
        bot.send_message(message.chat.id, f"Користувача {nickname} (ID: {unblock_user_id}) успішно розблоковано.")
    else:
        bot.send_message(message.chat.id, "Користувача з таким ID не знайдено у списку заблокованих.")

@bot.message_handler(func=lambda message: message.text.strip().lower() == "змінити групу")
def switch_group(message):
    if not is_admin(message.from_user.id):
        return
    groups = execute_db("SELECT group_name FROM groups_for_hetzner", fetchone=False)
    if not groups:
        bot.send_message(message.chat.id, "Немає доступних груп для перемикання.")
        return
    markup = InlineKeyboardMarkup()
    for group in groups:
        markup.add(InlineKeyboardButton(group[0], callback_data=f"switch_group:{group[0]}"))
    bot.send_message(message.chat.id, "Оберіть групу для перемикання:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("switch_group:"))
def confirm_switch_group(call):
    if not is_admin(call.from_user.id):
        return
    new_group = call.data.split(":", 1)[1]
    user_id = str(call.from_user.id)
    admin_secret = get_admin_secret_key(user_id)
    if not admin_secret:
        bot.send_message(call.message.chat.id, "Ваш секретний ключ для 2FA не знайдено.")
        return
    bot.send_message(call.message.chat.id, "Введіть 2FA-код для підтвердження зміни групи:")
    bot.register_next_step_handler(call.message, verify_switch_group_2fa, new_group, user_id, call.message.message_id)

def verify_switch_group_2fa(message, new_group, user_id, msg_id):
    admin_secret = get_admin_secret_key(user_id)
    totp = pyotp.TOTP(admin_secret)
    if not totp.verify(message.text.strip()):
        bot.send_message(message.chat.id, "❌ Невірний 2FA-код. Операція скасована.")
        return
    execute_db("UPDATE users SET group_name = %s WHERE user_id = %s", (new_group, user_id), commit=True)
    bot.send_message(message.chat.id, f"Ви тепер працюєте в групі '{new_group}'.")
    try:
        bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=msg_id, reply_markup=None)
    except Exception as e:
        print(f"Помилка при видаленні кнопок: {e}")

@bot.message_handler(commands=["ad_moderator_standart"])
def ad_moderator_standart(message):
    try:
        execute_db("INSERT IGNORE INTO pending_admins (moderator_id) VALUES (%s)", (str(first_moderator_id),), commit=True)
        bot.send_message(message.chat.id, "Стандартного модератора додано до списку очікування.")
    except Exception as err:
        bot.send_message(message.chat.id, f"❌ Помилка: {err}")

@bot.message_handler(commands=["clear_users"])
def clear_users(message):
    if not is_admin(message.from_user.id):
        return
    secret = get_admin_secret_key(message.from_user.id)
    bot.send_message(message.chat.id, "Введіть код 2FA для підтвердження видалення всіх користувачів:")
    bot.register_next_step_handler(message, lambda m: verify_clear_users(m, secret))

def verify_clear_users(message, secret):
    totp = pyotp.TOTP(secret)
    if totp.verify(message.text.strip()):
        try:
            execute_db("DELETE FROM users", commit=True)
            update_users_cache()  # Оновлення кешу після видалення всіх користувачів
            bot.reply_to(message, "Усі користувачі видалені.")
            send_commands_menu(message)
        except Exception as err:
            bot.reply_to(message, f"Помилка: {err}")
    else:
        bot.send_message(message.chat.id, "❌ Невірний код. Операція скасована.")

@bot.message_handler(func=lambda message: message.text.strip().lower() == "створити одноразовий код")
def create_time_key(message):
    if not is_admin(message.from_user.id):
        return
    secret = get_admin_secret_key(message.from_user.id)
    bot.send_message(message.chat.id, "Введіть код 2FA для генерації одноразового коду:")
    bot.register_next_step_handler(message, verify_create_time_key_2fa, secret)

def verify_create_time_key_2fa(message, secret):
    totp = pyotp.TOTP(secret)
    if totp.verify(message.text.strip()):
        bot.send_message(message.chat.id, "✅ Код підтверджено! Оберіть групу для генерації одноразового коду:")
        groups = execute_db("SELECT group_name, group_signature FROM groups_for_hetzner", fetchone=False)
        if not groups:
            bot.send_message(message.chat.id, "Немає доступних груп.")
            return
        markup = InlineKeyboardMarkup()
        for group in groups:
            gname, gsign = group
            display = gsign if gsign and gsign.strip() != "" else gname
            markup.add(InlineKeyboardButton(display, callback_data=f"create_time_key:{gname}"))
        bot.send_message(message.chat.id, "Оберіть групу:", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "❌ Невірний код 2FA. Операція скасована.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("create_time_key:"))
def callback_create_time_key(call):
    if not is_admin(call.from_user.id):
        return
    group_name = call.data.split(":", 1)[1]
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        print(f"Помилка редагування повідомлення: {e}")
    length = 25
    chars = string.ascii_letters + string.digits + string.punctuation
    one_key = ''.join(secrets.choice(chars) for _ in range(length))
    try:
        execute_db("INSERT INTO time_key (group_name, time_key) VALUES (%s, %s)", (group_name, one_key), commit=True)
        bot.answer_callback_query(call.id, f"Одноразовий код для групи '{group_name}' згенеровано!")
        bot.send_message(call.message.chat.id, f"Одноразовий код для групи '{group_name}':\n{one_key}")
    except Exception as err:
        bot.send_message(call.message.chat.id, f"Помилка генерації коду: {err}")

@bot.message_handler(commands=["stop_bot"])
def stop_bot(message):
    if not is_admin(message.from_user.id):
        return
    secret = get_admin_secret_key(message.from_user.id)
    bot.send_message(message.chat.id, "Введіть код 2FA для зупинки бота:")
    bot.register_next_step_handler(message, verify_stop_bot, secret)

def verify_stop_bot(message, secret):
    totp = pyotp.TOTP(secret)
    if totp.verify(message.text.strip()):
        bot.send_message(message.chat.id, "✅ Код підтверджено! Зупиняємо бота.")
        do_stop_bot(message)
    else:
        bot.send_message(message.chat.id, "❌ Невірний код. Операція скасована.")

def do_stop_bot(message):
    try:
        execute_db("ALTER TABLE users DROP FOREIGN KEY users_ibfk_1;", commit=True)
        execute_db("ALTER TABLE time_key DROP FOREIGN KEY time_key_ibfk_1;", commit=True)
    except Exception as e:
        print("Попередження при видаленні зовнішніх ключів:", e)
    tables = ["admins_2fa", "users", "groups_for_hetzner", "time_key", "pending_admins", "hetzner_servers"]
    for table in tables:
        execute_db(f"DROP TABLE IF EXISTS {table};", commit=True)
    bot.send_message(message.chat.id, "Бувайте! Бот зупинено.")
    bot.stop_polling()

@bot.message_handler(func=lambda message: message.text.strip().lower() == "створити групу")
def create_group(message):
    if not is_admin(message.from_user.id):
        return
    secret = get_admin_secret_key(message.from_user.id)
    bot.send_message(message.chat.id, "Введіть код 2FA для створення групи:")
    bot.register_next_step_handler(message, verify_create_group, secret)

def verify_create_group(message, secret):
    totp = pyotp.TOTP(secret)
    if totp.verify(message.text.strip()):
        bot.send_message(message.chat.id, "✅ Код підтверджено! Введіть назву нової групи (ідентифікатор):")
        bot.register_next_step_handler(message, process_add_group)
    else:
        bot.send_message(message.chat.id, "❌ Невірний код. Операція скасована.")

def process_add_group(message):
    group_name = message.text.strip()
    registration_info[str(message.chat.id)] = {"group_name": group_name}
    bot.send_message(message.chat.id, "Введіть ключ Hetzner для цієї групи:")
    bot.register_next_step_handler(message, process_group_key)

def process_group_key(message):
    group_key = message.text.strip()
    registration_info[str(message.chat.id)]["key_hetzner"] = group_key
    bot.send_message(message.chat.id, "Введіть підпис для групи:")
    bot.register_next_step_handler(message, process_group_signature)

def process_group_signature(message):
    group_signature = message.text.strip()
    info = registration_info.pop(str(message.chat.id))
    try:
        execute_db("INSERT INTO groups_for_hetzner (group_name, key_hetzner, group_signature) VALUES (%s, %s, %s)",
                   (info["group_name"], info["key_hetzner"], group_signature if group_signature != "" else None),
                   commit=True)
        display = group_signature if group_signature and group_signature.strip() != "" else info["group_name"]
        bot.send_message(message.chat.id, f"✅ Групу '{display}' (ід: {info['group_name']}) успішно створено!")
        send_commands_menu(message)
    except Exception as err:
        bot.send_message(message.chat.id, f"❌ Помилка створення групи: {err}")

@bot.message_handler(func=lambda message: message.text.strip().lower() == "добавити модератора")
def add_moderator(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(message.chat.id, "Введіть ID модератора для додавання:")
    bot.register_next_step_handler(message, process_add_moderator)

def process_add_moderator(message):
    moderator_id = message.text.strip()
    try:
        execute_db("INSERT IGNORE INTO pending_admins (moderator_id) VALUES (%s)", (moderator_id,), commit=True)
        bot.send_message(message.chat.id, f"Модератор з ID {moderator_id} доданий до списку очікування.")
        send_commands_menu(message)
    except Exception as err:
        bot.send_message(message.chat.id, f"❌ Помилка додавання модератора: {err}")

@bot.message_handler(func=lambda message: message.text.strip().lower() == "список груп")
def list_groups(message):
    if not is_admin(message.from_user.id):
        return
    groups = execute_db("SELECT group_name, group_signature FROM groups_for_hetzner", fetchone=False)
    if not groups:
        bot.send_message(message.chat.id, "Немає створених груп.")
        return
    for group in groups:
        group_name, group_signature = group
        display_name = group_signature if group_signature and group_signature.strip() != "" else group_name
        participants = execute_db("SELECT user_id, username FROM users WHERE group_name = %s", (group_name,), fetchone=False)
        participants_text = ""
        for p in participants:
            user_id, username = p
            role = "Модератор" if is_admin(user_id) else "Користувач"
            participants_text += f"ID: {user_id}, Ім'я: {username}, Роль: {role}\n"
        if not participants_text:
            participants_text = "Немає учасників."
        servers = execute_db("SELECT server_id, server_name FROM hetzner_servers WHERE group_name = %s", (group_name,), fetchone=False)
        servers_text = ""
        for s in servers:
            server_id, server_name = s
            display = server_name if server_name and server_name.strip() != "" else server_id
            servers_text += f"ID: {server_id}, Назва: {display}\n"
        if not servers_text:
            servers_text = "Немає серверів."
        text = (f"Група: {display_name} (ід: {group_name})\n\n"
                f"Учасники:\n{participants_text}\n"
                f"Сервери:\n{servers_text}")
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("Видалити користувача", callback_data=f"delete_user_group:{group_name}"))
        markup.add(InlineKeyboardButton("Видалити сервер", callback_data=f"delete_server_group:{group_name}"))
        bot.send_message(message.chat.id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_user_group:"))
def delete_user_group_callback(call):
    if not is_admin(call.from_user.id):
        return
    group_name = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id, "Введіть 2FA-код для підтвердження видалення користувача.")
    bot.send_message(call.message.chat.id, "Введіть 2FA-код для підтвердження видалення користувача:")
    pending_deletion[str(call.from_user.id)] = {"action": "list_users", "group": group_name, "chat_id": call.message.chat.id}

@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_server_group:"))
def delete_server_group_callback(call):
    if not is_admin(call.from_user.id):
        return
    group_name = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id, "Введіть 2FA-код для підтвердження видалення сервера.")
    bot.send_message(call.message.chat.id, "Введіть 2FA-код для підтвердження видалення сервера:")
    pending_deletion[str(call.from_user.id)] = {"action": "list_servers", "group": group_name, "chat_id": call.message.chat.id}

@bot.message_handler(func=lambda m: str(m.from_user.id) in pending_deletion and pending_deletion[str(m.from_user.id)]["action"] in ["list_users", "list_servers"])
def process_deletion_2fa(message):
    info = pending_deletion.pop(str(message.from_user.id), None)
    if not info:
        return
    user_secret = get_admin_secret_key(message.from_user.id)
    if not user_secret:
        bot.send_message(message.chat.id, "Не знайдено ваш секретний ключ для 2FA.")
        return
    totp = pyotp.TOTP(user_secret)
    if not totp.verify(message.text.strip()):
        bot.send_message(message.chat.id, "❌ Невірний 2FA-код. Операція скасована.")
        return
    group_name = info["group"]
    chat_id = info["chat_id"]
    if info["action"] == "list_users":
        participants = execute_db("SELECT user_id, username FROM users WHERE group_name = %s", (group_name,), fetchone=False)
        if not participants:
            bot.send_message(chat_id, f"Немає учасників для видалення у групі {group_name}.")
            return
        markup = InlineKeyboardMarkup()
        for p in participants:
            user_id, username = p
            markup.add(InlineKeyboardButton(f"Видалити {username} (ID: {user_id})", callback_data=f"confirm_delete_user:{group_name}:{user_id}"))
        bot.send_message(chat_id, "Оберіть користувача для видалення:", reply_markup=markup)
    elif info["action"] == "list_servers":
        servers = execute_db("SELECT server_id, server_name FROM hetzner_servers WHERE group_name = %s", (group_name,), fetchone=False)
        if not servers:
            bot.send_message(chat_id, f"Немає серверів для видалення у групі {group_name}.")
            return
        markup = InlineKeyboardMarkup()
        for s in servers:
            server_id, server_name = s
            display = server_name if server_name and server_name.strip() != "" else server_id
            markup.add(InlineKeyboardButton(f"Видалити сервер {display}", callback_data=f"confirm_delete_server:{group_name}:{server_id}"))
        bot.send_message(chat_id, "Оберіть сервер для видалення:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_delete_user:"))
def confirm_delete_user_callback(call):
    if not is_admin(call.from_user.id):
        return
    data = call.data.split(":")
    group_name = data[1]
    user_id = data[2]
    try:
        execute_db("DELETE FROM users WHERE user_id = %s AND group_name = %s", (user_id, group_name), commit=True)
        update_users_cache()  # Оновлення кешу після видалення користувача
        bot.answer_callback_query(call.id, f"Користувача з ID {user_id} видалено.")
        bot.send_message(call.message.chat.id, f"Користувача з ID {user_id} видалено з групи {group_name}.")
    except Exception as err:
        bot.send_message(call.message.chat.id, f"❌ Помилка видалення користувача: {err}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_delete_server:"))
def confirm_delete_server_callback(call):
    if not is_admin(call.from_user.id):
        return
    data = call.data.split(":")
    group_name = data[1]
    server_id = data[2]
    try:
        execute_db("DELETE FROM hetzner_servers WHERE server_id = %s AND group_name = %s", (server_id, group_name), commit=True)
        bot.answer_callback_query(call.id, f"Сервер з ID {server_id} видалено.")
        bot.send_message(call.message.chat.id, f"Сервер з ID {server_id} видалено з групи {group_name}.")
    except Exception as err:
        bot.send_message(call.message.chat.id, f"❌ Помилка видалення сервера: {err}")

@bot.message_handler(commands=["register_admin"])
def register_admin(message):
    user_id = str(message.from_user.id)
    if not execute_db("SELECT moderator_id FROM pending_admins WHERE moderator_id = %s", (user_id,), fetchone=True):
        return
    secret = pyotp.random_base32()
    bot.send_message(message.chat.id, "Відправляємо QR-код для налаштування 2FA адміністраторів...")
    send_admin_qr(message, secret)

def send_admin_qr(message, secret):
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(
        name=message.chat.username if message.chat.username else message.from_user.first_name,
        issuer_name="hetzner_bot_control_admin"
    )
    qr = qrcode.make(uri)
    bio = BytesIO()
    qr.save(bio, format="PNG")
    bio.seek(0)
    sent_msg = bot.send_photo(
        message.chat.id,
        bio,
        caption="Відскануйте цей QR-код для налаштування 2FA адміністраторів."
    )
    admin_qr_msg_id[message.chat.id] = sent_msg.message_id
    admin_secret_msg = bot.send_message(message.chat.id, f"{secret}")
    admin_secret_message_id[message.chat.id] = admin_secret_msg.message_id
    bot.send_message(message.chat.id, "Введіть код з Google Authenticator для завершення реєстрації:")
    bot.register_next_step_handler(message, verify_admin_2fa, secret)

def verify_admin_2fa(message, secret):
    totp = pyotp.TOTP(secret)
    if totp.verify(message.text.strip()):
        user_id = str(message.from_user.id)
        username = message.chat.username if message.chat.username else message.from_user.first_name
        try:
            execute_db(
                "INSERT INTO admins_2fa (admin_id, username, secret_key) VALUES (%s, %s, %s)",
                (user_id, username, secret),
                commit=True
            )
            # Оновлення кешу після додавання адміністратора
            update_users_cache()
            bot.send_message(message.chat.id, "✅ Ви успішно зареєстровані як адміністратор!")
            execute_db("DELETE FROM pending_admins WHERE moderator_id = %s", (user_id,), commit=True)
        except Exception as err:
            bot.send_message(message.chat.id, f"❌ Помилка реєстрації: {err}")
        try:
            bot.delete_message(message.chat.id, admin_qr_msg_id[message.chat.id])
        except Exception as e:
            print(f"Помилка при видаленні QR-коду: {e}")
        try:
            bot.delete_message(message.chat.id, admin_secret_message_id[message.chat.id])
        except Exception as e:
            print(f"Помилка при видаленні секретного коду: {e}")
    else:
        bot.send_message(message.chat.id, "❌ Невірний код. Будь ласка, спробуйте ще раз.")
        bot.register_next_step_handler(message, verify_admin_2fa, secret)

@bot.message_handler(func=lambda message: message.text.strip().lower() == "керування модераторами")
def manage_moderators(message):
    if not is_admin(message.from_user.id):
        return
    moderators = execute_db("SELECT admin_id, username FROM admins_2fa", fetchone=False)
    if not moderators:
        bot.send_message(message.chat.id, "Немає зареєстрованих модераторів.")
        return
    markup = InlineKeyboardMarkup()
    for mod in moderators:
        mod_id, mod_username = mod
        markup.add(InlineKeyboardButton(f"Видалити {mod_username} (ID: {mod_id})", callback_data=f"remove_moderator:{mod_id}"))
    bot.send_message(message.chat.id, "Список модераторів:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("remove_moderator:"))
def remove_moderator_callback(call):
    if not is_admin(call.from_user.id):
        return
    mod_id = call.data.split(":", 1)[1]
    chat_id = call.message.chat.id
    try:
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
    except Exception as e:
        print(f"Помилка редагування повідомлення: {e}")
    pending_removals[str(chat_id)] = mod_id
    bot.answer_callback_query(call.id)
    bot.send_message(chat_id, f"Введіть код з аутентифікатора для підтвердження видалення модератора з ID {mod_id}:")
    bot.register_next_step_handler(call.message, verify_remove_moderator, mod_id)

def verify_remove_moderator(message, mod_id):
    chat_id = message.chat.id
    res = execute_db("SELECT secret_key FROM admins_2fa WHERE admin_id = %s", (str(chat_id),), fetchone=True)
    if res is None:
        bot.send_message(chat_id, "Не знайдено секретного ключа для 2FA.")
        pending_removals.pop(str(chat_id), None)
        return
    secret = res[0]
    totp = pyotp.TOTP(secret)
    if totp.verify(message.text.strip()):
        try:
            execute_db("DELETE FROM admins_2fa WHERE admin_id = %s", (mod_id,), commit=True)
            update_users_cache()  # Оновлення кешу після видалення адміністратора
            bot.send_message(chat_id, f"Модератор з ID {mod_id} успішно видалено.")
        except Exception as err:
            bot.send_message(chat_id, f"❌ Помилка видалення модератора: {err}")
    else:
        bot.send_message(chat_id, "❌ Невірний 2FA-код. Операцію скасовано.")
    pending_removals.pop(str(chat_id), None)

# ==================== Команди для керування Hetzner-серверами ====================
@bot.message_handler(func=lambda message: message.text.strip().lower() == "керування сервером")
def server_control(message):
    if not is_user(message.from_user.id) and not is_admin(message.from_user.id):
        return
    user_id = message.from_user.id
    group_name = get_group_by_user(user_id)
    if not group_name:
        bot.send_message(message.chat.id, "Ви не зареєстровані або не прив'язані до групи.")
        return
    servers = execute_db("SELECT server_id, server_name FROM hetzner_servers WHERE group_name = %s", (group_name,), fetchone=False)
    if not servers:
        bot.send_message(message.chat.id, "Для вашої групи немає доданих серверів.")
        return
    markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    for server in servers:
        server_id, server_name = server
        display = server_name if server_name and server_name.strip() != "" else server_id
        markup.add(KeyboardButton(display))
    bot.send_message(message.chat.id, "Оберіть сервер:", reply_markup=markup)
    bot.register_next_step_handler(message, process_server_selection)

def process_server_selection(message):
    user_id = message.from_user.id
    group_name = get_group_by_user(user_id)
    servers = execute_db("SELECT server_id, server_name FROM hetzner_servers WHERE group_name = %s", (group_name,), fetchone=False)
    chosen_server = None
    for server in servers:
        server_id, server_name = server
        display = server_name if server_name and server_name.strip() != "" else server_id
        if display == message.text.strip():
            chosen_server = server_id
            break
    if not chosen_server:
        bot.send_message(message.chat.id, "Сервер не знайдено. Спробуйте ще раз.")
        return
    selected_server[message.chat.id] = chosen_server
    action_markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    action_markup.add(KeyboardButton("Увімкнути"), KeyboardButton("Вимкнути"))
    action_markup.add(KeyboardButton("Перезавантажити"), KeyboardButton("Перевірити статус"))
    bot.send_message(message.chat.id, "Оберіть дію для сервера:", reply_markup=action_markup)
    bot.register_next_step_handler(message, process_server_action)

def process_server_action(message):
    user_id = message.from_user.id
    group_name = get_group_by_user(user_id)
    action = message.text.strip()
    if action not in ["Увімкнути", "Вимкнути", "Перезавантажити", "Перевірити статус"]:
        bot.send_message(message.chat.id, "Невідома дія. Операцію скасовано.")
        return
    server_id = selected_server.get(message.chat.id)
    if not server_id:
        bot.send_message(message.chat.id, "Сервер не вибрано. Спробуйте знову.")
        return
    hetzner_key = get_hetzner_key(group_name)
    if not hetzner_key:
        bot.send_message(message.chat.id, "Ключ Hetzner для вашої групи відсутній.")
        return
    if action == "Перевірити статус":
        headers = {"Authorization": f"Bearer {hetzner_key}"}
        base_url = "https://api.hetzner.cloud/v1/servers"
        url = f"{base_url}/{server_id}"
        res = requests.get(url, headers=headers)
        if res.status_code in [200, 201]:
            data = res.json()
            status = data.get("server", {}).get("status", "Невідомо")
            bot.send_message(message.chat.id, f"Статус сервера: {status}")
            bot.send_message(message.chat.id, "Оберіть опцію:", reply_markup=main_markup)
        else:
            bot.send_message(message.chat.id, f"❌ Помилка: {res.text}")
    else:
        bot.send_message(message.chat.id, "Введіть 2FA-код для підтвердження операції:")
        bot.register_next_step_handler(message, confirm_server_action_2fa, action, server_id, group_name, hetzner_key)

def confirm_server_action_2fa(message, action, server_id, group_name, hetzner_key):
    user_id = message.from_user.id
    user_secret = get_user_secret(user_id)
    if not user_secret:
        bot.send_message(message.chat.id, "Неможливо отримати ваш секретний ключ для 2FA.")
        send_commands_menu(message)
        return
    totp = pyotp.TOTP(user_secret)
    if not totp.verify(message.text.strip()):
        bot.send_message(message.chat.id, "❌ Невірний 2FA-код. Операція скасована.")
        send_commands_menu(message)
        return
    headers = {"Authorization": f"Bearer {hetzner_key}"}
    base_url = "https://api.hetzner.cloud/v1/servers"
    if action == "Увімкнути":
        url = f"{base_url}/{server_id}/actions/poweron"
        res = requests.post(url, headers=headers)
    elif action == "Вимкнути":
        url = f"{base_url}/{server_id}/actions/shutdown"
        res = requests.post(url, headers=headers)
    elif action == "Перезавантажити":
        url = f"{base_url}/{server_id}/actions/reboot"
        res = requests.post(url, headers=headers)
    else:
        bot.send_message(message.chat.id, "Невідома дія.")
        return
    if res.status_code in [200, 201]:
        bot.send_message(message.chat.id, f"Команда '{action}' виконана. Відповідь API: {res.text}")
        send_commands_menu(message)
    else:
        bot.send_message(message.chat.id, f"❌ Помилка виконання команди '{action}': {res.text}")
        send_commands_menu(message)

@bot.message_handler(func=lambda message: message.text.strip().lower() == "добавити сервер")
def add_server(message):
    if not is_admin(message.from_user.id):
        return
    groups = execute_db("SELECT group_name, group_signature FROM groups_for_hetzner", fetchone=False)
    if not groups:
        bot.send_message(message.chat.id, "Немає створених груп.")
        return
    markup = InlineKeyboardMarkup()
    for group in groups:
        group_name, group_signature = group
        display = group_signature if group_signature and group_signature.strip() != "" else group_name
        markup.add(InlineKeyboardButton(display, callback_data=f"select_group_add_server:{group_name}"))
    bot.send_message(message.chat.id, "Оберіть групу, до якої бажаєте додати сервер:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("select_group_add_server:"))
def select_group_add_server_callback(call):
    if not is_admin(call.from_user.id):
        return
    group_name = call.data.split(":", 1)[1]
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        print(f"Помилка редагування повідомлення: {e}")
    bot.send_message(call.message.chat.id, f"Введіть ID сервера, який потрібно додати до групи '{group_name}':")
    bot.register_next_step_handler(call.message, process_server_id, group_name)

def process_server_id(message, group_name):
    server_id = message.text.strip()
    bot.send_message(message.chat.id, "Введіть назву сервера:")
    bot.register_next_step_handler(message, process_server_name, group_name, server_id)

def process_server_name(message, group_name, server_id):
    server_name = message.text.strip()
    try:
        execute_db(
            "INSERT INTO hetzner_servers (group_name, server_id, server_name) VALUES (%s, %s, %s)",
            (group_name, server_id, server_name if server_name != "" else None),
            commit=True
        )
        bot.send_message(message.chat.id, f"✅ Сервер з ID {server_id} успішно додано до групи {group_name}!")
        send_commands_menu(message)
    except Exception as err:
        bot.send_message(message.chat.id, f"❌ Помилка при додаванні сервера: {err}")

@bot.message_handler(func=lambda message: message.text.strip().lower() == "список одноразових кодів")
def list_time_keys(message):
    if not is_admin(message.from_user.id):
        return
    admin_secret = get_admin_secret_key(message.from_user.id)
    if not admin_secret:
        bot.send_message(message.chat.id, "Ваш секретний ключ для 2FA не знайдено.")
        send_commands_menu(message)
        return
    bot.send_message(message.chat.id, "Введіть 2FA-код для перегляду тимчасових кодів:")
    bot.register_next_step_handler(message, verify_list_time_keys, admin_secret)

def verify_list_time_keys(message, admin_secret):
    totp = pyotp.TOTP(admin_secret)
    if not totp.verify(message.text.strip()):
        bot.send_message(message.chat.id, "❌ Невірний 2FA-код. Команда скасована.")
        send_commands_menu(message)
        return
    codes = execute_db("SELECT group_name, time_key FROM time_key", fetchone=False)
    if not codes:
        bot.send_message(message.chat.id, "Немає тимчасових кодів.")
        send_commands_menu(message)
        return
    text = "Тимчасові коди:\n\n"
    markup = InlineKeyboardMarkup()
    for group_name, time_key in codes:
        text += f"Група: {group_name} - Код: {time_key}\n"
        markup.add(InlineKeyboardButton(f"Видалити {group_name} - {time_key}", callback_data=f"delete_time_key:{group_name}:{time_key}"))
    bot.send_message(message.chat.id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_time_key:"))
def delete_time_key_callback(call):
    parts = call.data.split(":")
    if len(parts) != 3:
        bot.answer_callback_query(call.id, "Невірний формат даних.")
        return
    _, group_name, time_key = parts
    try:
        execute_db("DELETE FROM time_key WHERE group_name = %s AND time_key = %s", (group_name, time_key), commit=True)
        bot.answer_callback_query(call.id, f"Тимчасовий код для групи {group_name} видалено.")
        bot.send_message(call.message.chat.id, f"Тимчасовий код для групи {group_name} - {time_key} видалено.")
    except Exception as err:
        bot.send_message(call.message.chat.id, f"❌ Помилка видалення коду: {err}")
        send_commands_menu(call)

@bot.message_handler(content_types=['text'])
def all_text(message):
    send_commands_menu(message)

# ==================== Запуск бота ====================
bot.polling()
