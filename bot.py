import telebot
import mysql.connector
import pyotp
import qrcode
import os
from io import BytesIO
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import requests
import secrets
import string
import logging
# ==================== Налаштування Telegram бота ====================

TOKEN = os.getenv("TELEGRAM_TOKEN")

# TOKEN = "0000"
first_moderator_id = "YOUR_ID"
bot = telebot.TeleBot(TOKEN)
logging.basicConfig(level=logging.INFO, filename="bot.log", format="%(asctime)s - %(levelname)s - %(message)s")

# ==================== Підключення до бази даних ====================

connection = mysql.connector.connect(
    host=os.getenv("DB_HOST", "db"),
    user=os.getenv("DB_USER", "test"),
    password=os.getenv("DB_PASSWORD", "3324MMMM"),
    database=os.getenv("DB_NAME", "telegram")
)
# connection = mysql.connector.connect(
#     host="192.168.0.7",
#     user="0000",
#     password="0000",
#     database="0000"
# )
cursor = connection.cursor()




# ==================== Створення таблиць ====================
# Таблиця груп (зберігає назву групи, Hetzner API-токен та підпис)
create_groups_table = """
CREATE TABLE IF NOT EXISTS groups_for_hetzner (
    group_name VARCHAR(255) NOT NULL,
    key_hetzner VARCHAR(255) NOT NULL,
    group_signature VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
    PRIMARY KEY (group_name)
);
"""


# Таблиця користувачів (зв’язок з групою, секрет для 2FA)
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

# Таблиця для зберігання одноразових кодів для реєстрації користувачів
create_time_secret_key = """
CREATE TABLE IF NOT EXISTS time_key (
    group_name VARCHAR(255) NOT NULL,
    time_key VARCHAR(255) NOT NULL,
    FOREIGN KEY (group_name) REFERENCES groups_for_hetzner(group_name) ON DELETE CASCADE
);
"""

# Таблиця адміністраторів (2FA для модераторів)
create_admins_table = """
CREATE TABLE IF NOT EXISTS admins_2fa (
    admin_id VARCHAR(50) NOT NULL PRIMARY KEY,
    username VARCHAR(255) NOT NULL,
    secret_key VARCHAR(255) NOT NULL
);
"""

# Таблиця для очікування модераторів
create_pending_admins_table = """
CREATE TABLE IF NOT EXISTS pending_admins (
    moderator_id VARCHAR(50) NOT NULL PRIMARY KEY
);
"""

# Таблиця для серверів Hetzner (одна група може мати декілька серверів)
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
cursor.execute(create_blocked_users)
cursor.execute(create_groups_table)
cursor.execute(create_users_table)
cursor.execute(create_time_secret_key)
cursor.execute(create_admins_table)
cursor.execute(create_pending_admins_table)
cursor.execute(create_hetzner_servers_table)
cursor.execute(create_blocked_users)
connection.commit()


# pending_admins = """
# INSERT IGNORE INTO pending_admins (moderator_id)
# VALUES ('0000')
# """
# # cursor.execute(pending_admins)
connection.commit()

# ==================== Глобальні змінні та клавіатури ====================
# Головна клавіатура для користувачів
main_markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
main_markup.add(KeyboardButton("/my_id"), KeyboardButton("/server_control"))

# Словники для зберігання даних 2FA та QR-кодів
qr_message_id = {}
admin_qr_msg_id = {}
registration_info = {}   # зберігає дані реєстрації користувача
selected_server = {}     # зберігає вибір сервера користувача (chat_id -> server_id)
group_messages = []
pending_deletion = {}
secret_message_id = {}
admin_secret_message_id = {}
pending_removals = {}
wrong_attempts = {}
pending_unblock = {}
# ==================== Декоратори для перевірки прав ====================
def admin_only(func):
    def wrapper(message, *args, **kwargs):
        if not is_admin(message.from_user.id):
            return
        return func(message, *args, **kwargs)
    return wrapper

def user_only(func):
    def wrapper(message, *args, **kwargs):
        if not is_user(message.from_user.id) and not is_admin(message.from_user.id):
            return  # Якщо користувач не є звичайним користувачем або адміністратором, нічого не робимо
        return func(message, *args, **kwargs)
    return wrapper


def admin_only_callback(func):
    def wrapper(call, *args, **kwargs):
        if not is_admin(call.from_user.id):
            return
        return func(call, *args, **kwargs)
    return wrapper


# ==================== Функції роботи з базою даних ====================
def is_admin(user_id):
    cursor.execute("SELECT admin_id FROM admins_2fa WHERE admin_id = %s", (str(user_id),))
    return cursor.fetchone() is not None
def is_user(user_id):
    cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (str(user_id),))
    return cursor.fetchone() is not None

def get_group_by_user(user_id):
    cursor.execute("SELECT group_name FROM users WHERE user_id = %s", (str(user_id),))
    result = cursor.fetchone()
    return result[0] if result else None

def get_hetzner_key(group_name):
    cursor.execute("SELECT key_hetzner FROM groups_for_hetzner WHERE group_name = %s", (group_name,))
    result = cursor.fetchone()
    return result[0] if result else None

def get_admin_secret_key(user_id):
    cursor.execute("SELECT secret_key FROM admins_2fa WHERE admin_id = %s", (str(user_id),))
    result = cursor.fetchone()
    return result[0] if result else None

def get_user_secret(user_id):
    cursor.execute("SELECT secret_key FROM users WHERE user_id = %s", (str(user_id),))
    result = cursor.fetchone()
    return result[0] if result else None

# ==================== Меню ====================
@bot.message_handler(commands=["start"])
@user_only
def start(message):

    send_commands_menu(message)

def send_commands_menu(message):

    """
    Надсилає користувачу меню з кнопками під клавіатурою.
    Після натискання кнопки її текст просто надсилається в чат.
    """
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)

    # Команди для звичайного користувача
    user_commands = ["мій айді", "керування сервером"]

    admin_commands = [
        "створити групу",
        "змінити групу",
        "добавити модератора",
        "керування модераторами",
        "добавити сервер",
        "створити одноразовий код",
        "список одноразових кодів",
        "список груп",
        "розблокувати користувача"
    ]

    # Додаємо кнопки відповідно до прав користувача
    if is_admin(message.from_user.id):
        buttons = admin_commands + user_commands
    else:
        buttons = user_commands

    for button in buttons:
        markup.add(button)

    bot.send_message(message.chat.id, "Оберіть команду:", reply_markup=markup)
# ==================== Команди для користувачів ====================

@bot.message_handler(func=lambda message: message.text.strip().lower() == "мій айді")
@user_only
def my_id(message):
    bot.reply_to(message, f"Ваш user ID: {message.from_user.id}")

    send_commands_menu(message)

# Реєстрація користувача через одноразовий код і 2FA
# @bot.message_handler(func=lambda message: message.text.strip().lower() == "реєстрація")
@bot.message_handler(commands=["register"])
def register(message):
    cursor.execute("SELECT * FROM users WHERE user_id = %s", (str(message.chat.id),))
    if cursor.fetchone():
        bot.send_message(message.chat.id, "Ви вже зареєстровані.")
        return

    bot.register_next_step_handler(message, verify_one_time_code)


def verify_one_time_code(message):
    user_id = message.chat.id

    one_time_code = message.text.strip()
    cursor.execute("SELECT group_name FROM time_key WHERE time_key = %s", (one_time_code,))
    result = cursor.fetchone()

    if result:
        # Якщо код правильний – очищуємо лічильник невдалих спроб (якщо потрібно)
        wrong_attempts.pop(user_id, None)
        group_name = result[0]
        cursor.execute("DELETE FROM time_key WHERE time_key = %s AND group_name = %s", (one_time_code, group_name))
        connection.commit()
        username = message.chat.username if message.chat.username else message.from_user.first_name
        secret = pyotp.random_base32()
        registration_info[str(user_id)] = {"username": username, "group_name": group_name, "secret": secret}
        send_qr(message, secret)
    else:
        # Логування невдалої спроби
        logging.warning(f"Користувач {user_id} ввів невірний тимчасовий код.")
        wrong_attempts[user_id] = wrong_attempts.get(user_id, 0) + 1

        if wrong_attempts[user_id] >= 5:
            # Збереження нікнейму користувача разом із блокуванням
            nickname = message.chat.username if message.chat.username else message.from_user.first_name
            cursor.execute("INSERT IGNORE INTO blocked_users (user_id, nickname, reason) VALUES (%s, %s, %s)",
                           (str(user_id), nickname, "Вичерпано кількість спроб введення тимчасового коду"))
            connection.commit()
            logging.error(f"Користувач {user_id} заблокований після 5 невдалих спроб.")
        else:
            if wrong_attempts[user_id] < 5:
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

    # Відправляємо QR-код
    sent_msg = bot.send_photo(
        message.chat.id,
        bio,
        caption="Відскануйте QR-код для Google Authenticator або скопіюйте код який знаходиться нижче."
    )
    qr_message_id[message.chat.id] = sent_msg.message_id

    # Відправляємо секретний код під QR-кодом
    secret_msg = bot.send_message(
        message.chat.id,
        f"{secret}"
    )
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
                cursor.execute(
                    "INSERT INTO users (user_id, username, group_name, secret_key) VALUES (%s, %s, %s, %s)",
                    (str(message.chat.id), info["username"], info["group_name"], info["secret"])
                )
                connection.commit()
            except mysql.connector.Error as err:
                bot.send_message(message.chat.id, f"Помилка збереження даних: {err}")
            registration_info.pop(str(message.chat.id), None)

        send_commands_menu(message)
        # Видаляємо QR-код
        try:
            bot.delete_message(message.chat.id, qr_message_id[message.chat.id])
        except Exception as e:
            print(f"Помилка видалення QR-коду: {e}")
        # Видаляємо повідомлення з секретним кодом
        try:
            bot.delete_message(message.chat.id, secret_message_id[message.chat.id])
        except Exception as e:
            print(f"Помилка видалення секретного коду: {e}")
    else:
        bot.send_message(message.chat.id, "❌ Невірний код. Будь ласка, спробуйте ще раз.")
        bot.register_next_step_handler(message, verify_2fa, secret)

# ==================== Модераторські команди ====================
@bot.message_handler(func=lambda message: message.text.strip().lower() == "розблокувати користувача")
@admin_only
def unblock_user(message):
    # Перевірка прав адміністратора


    # Отримання списку заблокованих користувачів із бази даних
    cursor.execute("SELECT user_id, nickname FROM blocked_users")
    blocked = cursor.fetchall()
    if not blocked:
        bot.send_message(message.chat.id, "Немає заблокованих користувачів.")
        return

    # Формування інлайн-клавіатури з кнопками для кожного заблокованого користувача
    markup = InlineKeyboardMarkup()
    for user in blocked:
        user_id, nickname = user
        display = nickname if nickname and nickname.strip() != "" else f"ID: {user_id}"
        markup.add(InlineKeyboardButton(f"Розблокувати {display}", callback_data=f"confirm_unblock:{user_id}"))
    bot.send_message(message.chat.id, "Оберіть користувача для розблокування:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_unblock:"),)
@admin_only_callback
def confirm_unblock_callback(call):
    # Отримання ID користувача для розблокування із callback_data
    parts = call.data.split(":", 1)
    if len(parts) != 2:
        bot.answer_callback_query(call.id, "Невірний формат даних.")
        return
    unblock_user_id = parts[1]
    admin_id = call.from_user.id
    # Записуємо ID користувача, якого потрібно розблокувати, в глобальний словник
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

    # Отримуємо ID користувача, якого потрібно розблокувати
    unblock_user_id = pending_unblock.pop(admin_id)
    # Отримуємо нікнейм користувача перед видаленням з таблиці заблокованих
    cursor.execute("SELECT nickname FROM blocked_users WHERE user_id = %s", (str(unblock_user_id),))
    result = cursor.fetchone()
    if result:
        nickname = result[0]
        cursor.execute("DELETE FROM blocked_users WHERE user_id = %s", (str(unblock_user_id),))
        connection.commit()
        wrong_attempts.pop(unblock_user_id, None)
        logging.info(f"Користувача {unblock_user_id} ({nickname}) розблоковано адміністратором {admin_id}.")
        bot.send_message(message.chat.id, f"Користувача {nickname} (ID: {unblock_user_id}) успішно розблоковано.")
    else:
        bot.send_message(message.chat.id, "Користувача з таким ID не знайдено у списку заблокованих.")

@bot.message_handler(func=lambda message: message.text.strip().lower() == "змінити групу")
@admin_only
def switch_group(message):
    user_id = message.from_user.id
    cursor.execute("SELECT group_name FROM groups_for_hetzner")
    groups = cursor.fetchall()
    if not groups:
        bot.send_message(message.chat.id, "Немає доступних груп для перемикання.")
        return

    markup = InlineKeyboardMarkup()
    for group in groups:
        markup.add(InlineKeyboardButton(group[0], callback_data=f"switch_group:{group[0]}"))

    bot.send_message(message.chat.id, "Оберіть групу для перемикання:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("switch_group:"))
@admin_only_callback
def confirm_switch_group(call):
    new_group = call.data.split(":")[1]
    user_id = str(call.from_user.id)

    # Запит 2FA коду перед зміною групи
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

    cursor.execute("UPDATE users SET group_name = %s WHERE user_id = %s", (new_group, user_id))
    connection.commit()

    bot.send_message(message.chat.id, f"Ви тепер працюєте в групі '{new_group}'.")

    # Видалення клавіатури після перемикання групи
    try:
        bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=msg_id, reply_markup=None)
    except Exception as e:
        print(f"Помилка при видаленні кнопок: {e}")


@bot.message_handler(commands=["ad_moderator_standart"])
def ad_moderator_standart(message):
    try:
        cursor.execute("INSERT IGNORE INTO pending_admins (moderator_id) VALUES (%s)", (str(first_moderator_id),))
        connection.commit()
        bot.send_message(message.chat.id, "Стандартного модератора додано до списку очікування.")


    except mysql.connector.Error as err:
        bot.send_message(message.chat.id, f"❌ Помилка: {err}")

# /clear_users – видаляє всіх користувачів (лише для модераторів)
@bot.message_handler(commands=["clear_users"])
@admin_only
def clear_users(message):
    secret = get_admin_secret_key(message.from_user.id)
    bot.send_message(message.chat.id, "Введіть код 2FA для підтвердження видалення всіх користувачів:")
    bot.register_next_step_handler(message, lambda m: verify_clear_users(m, secret))

def verify_clear_users(message, secret):
    totp = pyotp.TOTP(secret)
    if totp.verify(message.text.strip()):
        try:
            cursor.execute("DELETE FROM users")
            connection.commit()
            bot.reply_to(message, "Усі користувачі видалені.")

            send_commands_menu(message)
        except mysql.connector.Error as err:
            bot.reply_to(message, f"Помилка: {err}")
    else:
        bot.send_message(message.chat.id, "❌ Невірний код. Операція скасована.")

# /create_time_key – генерує одноразовий код для обраної групи (лише для модераторів)
@bot.message_handler(func=lambda message: message.text.strip().lower() == "створити одноразовий код")
@admin_only
def create_time_key(message):
    secret = get_admin_secret_key(message.from_user.id)
    bot.send_message(message.chat.id, "Введіть код 2FA для генерації одноразового коду:")
    bot.register_next_step_handler(message, verify_create_time_key_2fa, secret)

def verify_create_time_key_2fa(message, secret):
    totp = pyotp.TOTP(secret)
    if totp.verify(message.text.strip()):
        bot.send_message(message.chat.id, "✅ Код підтверджено! Оберіть групу для генерації одноразового коду:")
        cursor.execute("SELECT group_name, group_signature FROM groups_for_hetzner")
        groups = cursor.fetchall()
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

@bot.callback_query_handler(func=lambda call: call.data.startswith("create_time_key:"),)
@admin_only_callback
def callback_create_time_key(call):
    group_name = call.data.split(":", 1)[1]
    bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
    length = 25
    chars = string.ascii_letters + string.digits + string.punctuation
    one_key = ''.join(secrets.choice(chars) for _ in range(length))
    try:
        cursor.execute("INSERT INTO time_key (group_name, time_key) VALUES (%s, %s)", (group_name, one_key))
        connection.commit()
        bot.answer_callback_query(call.id, f"Одноразовий код для групи '{group_name}' згенеровано!")
        bot.send_message(call.message.chat.id, f"Одноразовий код для групи '{group_name}':\n{one_key}")

    except mysql.connector.Error as err:
        bot.send_message(call.message.chat.id, f"Помилка генерації коду: {err}")

# /stop_bot – зупиняє бота (лише для модераторів) і видаляє всі таблиці
@bot.message_handler(commands=["stop_bot"])
@admin_only
def stop_bot(message):
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
        cursor.execute("ALTER TABLE users DROP FOREIGN KEY users_ibfk_1;")
        cursor.execute("ALTER TABLE time_key DROP FOREIGN KEY time_key_ibfk_1;")
    except Exception as e:
        print("Попередження при видаленні зовнішніх ключів:", e)
    tables = ["admins_2fa", "users", "groups_for_hetzner", "time_key", "pending_admins", "hetzner_servers"]
    for table in tables:
        cursor.execute(f"DROP TABLE IF EXISTS {table};")
    connection.commit()
    bot.send_message(message.chat.id, "Бувайте! Бот зупинено.")
    cursor.close()
    connection.close()
    bot.stop_polling()

# /create_group – створює нову групу (запитує назву, ключ Hetzner та підпис)
@bot.message_handler(func=lambda message: message.text.strip().lower() == "створити групу")
@admin_only
def create_group(message):
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
        cursor.execute("INSERT INTO groups_for_hetzner (group_name, key_hetzner, group_signature) VALUES (%s, %s, %s)",
                       (info["group_name"], info["key_hetzner"], group_signature if group_signature != "" else None))
        connection.commit()
        display = group_signature if group_signature and group_signature.strip() != "" else info["group_name"]
        bot.send_message(message.chat.id, f"✅ Групу '{display}' (ідентифікатор: {info['group_name']}) успішно створено!")

        send_commands_menu(message)
    except mysql.connector.Error as err:
        bot.send_message(message.chat.id, f"❌ Помилка створення групи: {err}")

# /add_moderator – додає нового модератора (ID записується у pending_admins)
@bot.message_handler(func=lambda message: message.text.strip().lower() == "добавити модератора")
@admin_only
def add_moderator(message):
    bot.send_message(message.chat.id, "Введіть ID модератора для додавання:")
    bot.register_next_step_handler(message, process_add_moderator)

def process_add_moderator(message):
    moderator_id = message.text.strip()
    try:
        cursor.execute("INSERT IGNORE INTO pending_admins (moderator_id) VALUES (%s)", (moderator_id,))
        connection.commit()
        bot.send_message(message.chat.id, f"Модератор з ID {moderator_id} доданий до списку очікування.")
        send_commands_menu(message)
    except mysql.connector.Error as err:
        bot.send_message(message.chat.id, f"❌ Помилка додавання модератора: {err}")

@bot.message_handler(func=lambda message: message.text.strip().lower() == "список груп")
@admin_only
def list_groups(message):
    cursor.execute("SELECT group_name, group_signature FROM groups_for_hetzner")
    groups = cursor.fetchall()
    if not groups:
        bot.send_message(message.chat.id, "Немає створених груп.")
        return

    for group in groups:
        group_name, group_signature = group
        display_name = group_signature if group_signature and group_signature.strip() != "" else group_name

        # Формування тексту з учасниками
        cursor.execute("SELECT user_id, username FROM users WHERE group_name = %s", (group_name,))
        participants = cursor.fetchall()
        participants_text = ""
        for p in participants:
            user_id, username = p
            role = "Модератор" if is_admin(user_id) else "Користувач"
            participants_text += f"ID: {user_id}, Ім'я: {username}, Роль: {role}\n"
        if not participants_text:
            participants_text = "Немає учасників."

        # Формування тексту з серверами
        cursor.execute("SELECT server_id, server_name FROM hetzner_servers WHERE group_name = %s", (group_name,))
        servers = cursor.fetchall()
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

        # Створення інлайн-клавіатури лише з двома кнопками:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("Видалити користувача", callback_data=f"delete_user_group:{group_name}"))
        markup.add(InlineKeyboardButton("Видалити сервер", callback_data=f"delete_server_group:{group_name}"))
        bot.send_message(message.chat.id, text, reply_markup=markup)

# Обробка натискання кнопки "Видалити користувача" для групи
@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_user_group:"))
@admin_only_callback
def delete_user_group_callback(call):
    group_name = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id, "Введіть 2FA-код для підтвердження видалення користувача.")
    bot.send_message(call.message.chat.id, "Введіть 2FA-код для підтвердження видалення користувача:")
    pending_deletion[str(call.from_user.id)] = {
         "action": "list_users",
         "group": group_name,
         "chat_id": call.message.chat.id
    }

# Обробка натискання кнопки "Видалити сервер" для групи
@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_server_group:"))
@admin_only_callback
def delete_server_group_callback(call):
    group_name = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id, "Введіть 2FA-код для підтвердження видалення сервера.")
    bot.send_message(call.message.chat.id, "Введіть 2FA-код для підтвердження видалення сервера:")
    pending_deletion[str(call.from_user.id)] = {
         "action": "list_servers",
         "group": group_name,
         "chat_id": call.message.chat.id
    }

# Після введення 2FA-коду – в залежності від запиту, показуємо перелік учасників або серверів для вибору
@bot.message_handler(func=lambda m: str(m.from_user.id) in pending_deletion and pending_deletion[str(m.from_user.id)]["action"] in ["list_users", "list_servers"])
@admin_only
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
        cursor.execute("SELECT user_id, username FROM users WHERE group_name = %s", (group_name,))
        participants = cursor.fetchall()
        if not participants:
            bot.send_message(chat_id, f"Немає учасників для видалення у групі {group_name}.")
            return
        markup = InlineKeyboardMarkup()
        for p in participants:
            user_id, username = p
            markup.add(InlineKeyboardButton(f"Видалити {username} (ID: {user_id})", callback_data=f"confirm_delete_user:{group_name}:{user_id}"))
        bot.send_message(chat_id, "Оберіть користувача для видалення:", reply_markup=markup)

    elif info["action"] == "list_servers":
        cursor.execute("SELECT server_id, server_name FROM hetzner_servers WHERE group_name = %s", (group_name,))
        servers = cursor.fetchall()
        if not servers:
            bot.send_message(chat_id, f"Немає серверів для видалення у групі {group_name}.")
            return
        markup = InlineKeyboardMarkup()
        for s in servers:
            server_id, server_name = s
            display = server_name if server_name and server_name.strip() != "" else server_id
            markup.add(InlineKeyboardButton(f"Видалити сервер {display}", callback_data=f"confirm_delete_server:{group_name}:{server_id}"))
        bot.send_message(chat_id, "Оберіть сервер для видалення:", reply_markup=markup)

# Підтвердження видалення користувача – після вибору конкретного користувача
@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_delete_user:"))
@admin_only_callback
def confirm_delete_user_callback(call):
    data = call.data.split(":")
    group_name = data[1]
    user_id = data[2]
    try:
        cursor.execute("DELETE FROM users WHERE user_id = %s AND group_name = %s", (user_id, group_name))
        connection.commit()
        bot.answer_callback_query(call.id, f"Користувача з ID {user_id} видалено.")
        bot.send_message(call.message.chat.id, f"Користувача з ID {user_id} видалено з групи {group_name}.")
    except mysql.connector.Error as err:
        bot.send_message(call.message.chat.id, f"❌ Помилка видалення користувача: {err}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_delete_server:"))
@admin_only_callback
def confirm_delete_server_callback(call):
    data = call.data.split(":")
    group_name = data[1]
    server_id = data[2]
    try:
        cursor.execute("DELETE FROM hetzner_servers WHERE server_id = %s AND group_name = %s", (server_id, group_name))
        connection.commit()
        bot.answer_callback_query(call.id, f"Сервер з ID {server_id} видалено.")
        bot.send_message(call.message.chat.id, f"Сервер з ID {server_id} видалено з групи {group_name}.")
    except mysql.connector.Error as err:
        bot.send_message(call.message.chat.id, f"❌ Помилка видалення сервера: {err}")
#______________________________________________________________________________________________________________________



# /register_admin – реєстрація модератора як адміністратора через 2FA
@bot.message_handler(commands=["register_admin"])
def register_admin(message):
    user_id = str(message.from_user.id)
    cursor.execute("SELECT moderator_id FROM pending_admins WHERE moderator_id = %s", (user_id,))
    if not cursor.fetchone():
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

    # Відправляємо QR-код
    sent_msg = bot.send_photo(
        message.chat.id,
        bio,
        caption="Відскануйте цей QR-код для налаштування 2FA адміністраторів."
    )
    admin_qr_msg_id[message.chat.id] = sent_msg.message_id

    # Відправляємо повідомлення із секретним кодом
    admin_secret_msg = bot.send_message(
        message.chat.id,
        f"{secret}"
    )
    admin_secret_message_id[message.chat.id] = admin_secret_msg.message_id

    bot.send_message(message.chat.id, "Введіть код з Google Authenticator для завершення реєстрації:")
    bot.register_next_step_handler(message, verify_admin_2fa, secret)


def verify_admin_2fa(message, secret):
    totp = pyotp.TOTP(secret)
    if totp.verify(message.text.strip()):
        user_id = str(message.from_user.id)
        username = message.chat.username if message.chat.username else message.from_user.first_name
        try:
            cursor.execute(
                "INSERT INTO admins_2fa (admin_id, username, secret_key) VALUES (%s, %s, %s)",
                (user_id, username, secret)
            )
            connection.commit()
            bot.send_message(message.chat.id, "✅ Ви успішно зареєстровані як адміністратор!")
            cursor.execute("DELETE FROM pending_admins WHERE moderator_id = %s", (user_id,))
            connection.commit()
        except mysql.connector.Error as err:
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


#______________________________________________________________________________________________________________________



@bot.message_handler(func=lambda message: message.text.strip().lower() == "керування модераторами")
@admin_only
def manage_moderators(message):
    cursor.execute("SELECT admin_id, username FROM admins_2fa")
    moderators = cursor.fetchall()
    if not moderators:
        bot.send_message(message.chat.id, "Немає зареєстрованих модераторів.")
        return
    markup = InlineKeyboardMarkup()
    for mod in moderators:
        mod_id, mod_username = mod
        markup.add(InlineKeyboardButton(f"Видалити {mod_username} (ID: {mod_id})", callback_data=f"remove_moderator:{mod_id}"))
    bot.send_message(message.chat.id, "Список модераторів:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("remove_moderator:"),)
@admin_only_callback
def remove_moderator_callback(call):
    mod_id = call.data.split(":", 1)[1]
    chat_id = call.message.chat.id

    # Видаляємо inline-кнопки із повідомлення
    try:
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
    except Exception as e:
        print(f"Помилка редагування повідомлення: {e}")

    # Зберігаємо id модератора для видалення
    pending_removals[str(chat_id)] = mod_id

    # Запитуємо 2FA-код від адміністратора для підтвердження операції
    bot.answer_callback_query(call.id)
    bot.send_message(chat_id, f"Введіть код з аутентифікатора для підтвердження видалення модератора з ID {mod_id}:")
    bot.register_next_step_handler(call.message, verify_remove_moderator, mod_id)

def verify_remove_moderator(message, mod_id):
    chat_id = message.chat.id
    # Отримуємо секретний ключ адміністратора з бази даних
    cursor.execute("SELECT secret_key FROM admins_2fa WHERE admin_id = %s", (str(chat_id),))
    res = cursor.fetchone()
    if res is None:
        bot.send_message(chat_id, "Не знайдено секретного ключа для 2FA.")
        pending_removals.pop(str(chat_id), None)
        return

    secret = res[0]
    totp = pyotp.TOTP(secret)
    if totp.verify(message.text.strip()):
        try:
            cursor.execute("DELETE FROM admins_2fa WHERE admin_id = %s", (mod_id,))
            connection.commit()
            bot.send_message(chat_id, f"Модератор з ID {mod_id} успішно видалено.")
        except mysql.connector.Error as err:
            bot.send_message(chat_id, f"❌ Помилка видалення модератора: {err}")
    else:
        bot.send_message(chat_id, "❌ Невірний 2FA-код. Операцію скасовано.")
    pending_removals.pop(str(chat_id), None)

# ==================== Команди для керування Hetzner-серверами ====================
# @bot.message_handler(commands=["server_control"])
@bot.message_handler(func=lambda message: message.text.strip().lower() == "керування сервером")
@user_only
def server_control(message):
    user_id = message.from_user.id
    group_name = get_group_by_user(user_id)
    if not group_name:
        bot.send_message(message.chat.id, "Ви не зареєстровані або не прив'язані до групи.")
        return

    cursor.execute("SELECT server_id, server_name FROM hetzner_servers WHERE group_name = %s", (group_name,))
    servers = cursor.fetchall()
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
    cursor.execute("SELECT server_id, server_name FROM hetzner_servers WHERE group_name = %s", (group_name,))
    servers = cursor.fetchall()
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

    # Якщо дія — перевірка статусу, не потребуємо 2FA
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
        # Для дій "Увімкнути", "Вимкнути", "Перезавантажити" спочатку запитуємо 2FA-код
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
#______________________________________________________________________________________________________________________


@bot.message_handler(func=lambda message: message.text.strip().lower() == "Добавити сервер")
@admin_only
def add_server(message):
    # Модератор може вибрати групу для додавання сервера
    cursor.execute("SELECT group_name, group_signature FROM groups_for_hetzner")
    groups = cursor.fetchall()
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
@admin_only_callback
def select_group_add_server_callback(call):
    group_name = call.data.split(":", 1)[1]
    try:
        # Видаляємо клавіатуру після вибору групи
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
        cursor.execute(
            "INSERT INTO hetzner_servers (group_name, server_id, server_name) VALUES (%s, %s, %s)",
            (group_name, server_id, server_name if server_name != "" else None)
        )
        connection.commit()
        bot.send_message(message.chat.id, f"✅ Сервер з ID {server_id} успішно додано до групи {group_name}!")
        send_commands_menu(message)
    except mysql.connector.Error as err:
        bot.send_message(message.chat.id, f"❌ Помилка при додаванні сервера: {err}")
@bot.message_handler(func=lambda message: message.text.strip().lower() == "список одноразових кодів")
@admin_only
def list_time_keys(message):
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
    # 2FA пройдено, виконуємо запит до бази
    cursor.execute("SELECT group_name, time_key FROM time_key")
    codes = cursor.fetchall()
    if not codes:
        bot.send_message(message.chat.id, "Немає тимчасових кодів.")
        send_commands_menu(message)
        return

    # Формуємо текст з переліком кодів
    text = "Тимчасові коди:\n\n"
    markup = InlineKeyboardMarkup()
    for group_name, time_key in codes:
        text += f"Група: {group_name} - Код: {time_key}\n"
        # Додаємо кнопку для видалення конкретного коду
        markup.add(InlineKeyboardButton(f"Видалити {group_name} - {time_key}", callback_data=f"delete_time_key:{group_name}:{time_key}"))
    bot.send_message(message.chat.id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_time_key:"))
@admin_only_callback
def delete_time_key_callback(call):
    # Розбираємо callback_data
    parts = call.data.split(":")
    if len(parts) != 3:
        bot.answer_callback_query(call.id, "Невірний формат даних.")
        return
    _, group_name, time_key = parts
    try:
        cursor.execute("DELETE FROM time_key WHERE group_name = %s AND time_key = %s", (group_name, time_key))
        connection.commit()
        bot.answer_callback_query(call.id, f"Тимчасовий код для групи {group_name} видалено.")
        bot.send_message(call.message.chat.id, f"Тимчасовий код для групи {group_name} - {time_key} видалено.")
    except mysql.connector.Error as err:
        bot.send_message(call.message.chat.id, f"❌ Помилка видалення коду: {err}")
        send_commands_menu(call)
@bot.message_handler(content_types=['text'])
@user_only
def all_text(message):
    send_commands_menu(message)



# ==================== Запуск бота ====================
bot.polling()
