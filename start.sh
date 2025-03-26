#!/bin/bash
first_moderator_id="00000"
YOUR_TOKEN="0000"

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

if ! command_exists git; then
    sudo apt-get update -y >/dev/null 2>&1
    sudo apt-get install -y git >/dev/null 2>&1
fi

if ! command_exists docker; then
    sudo apt-get update -y >/dev/null 2>&1
    sudo apt-get install -y docker.io >/dev/null 2>&1
    sudo systemctl start docker >/dev/null 2>&1
    sudo systemctl enable docker >/dev/null 2>&1
fi

if ! command_exists docker-compose; then
    sudo curl -sL "https://github.com/docker/compose/releases/download/1.29.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose >/dev/null 2>&1
    sudo chmod +x /usr/local/bin/docker-compose >/dev/null 2>&1
fi

REPO_URL="https://github.com/Slithon/telegram_bot_hetzner"
CLONE_DIR="telegram_bot_hetzner"

git clone "$REPO_URL" >/dev/null 2>&1


cd "$CLONE_DIR" || exit

# Вказуємо файл docker-compose
COMPOSE_FILE="docker-compose.yml"
# Замінюємо токен у файлі docker-compose.yml, підставляючи значення YOUR_TOKEN
sed -i 's/TELEGRAM_TOKEN: TOKEN/TELEGRAM_TOKEN: '"$YOUR_TOKEN"'/' "$COMPOSE_FILE"

# Вказуємо файл з ботом
BOT_FILE="bot.py"
# Замінюємо в bot.py рядок з id першого модератора
sed -i 's/first_moderator_id = "YOUR_ID"/first_moderator_id = '"$first_moderator_id"'/' "$BOT_FILE"
sudo systemctl enable docker
sudo docker-compose up --build
