#!/bin/bash
first_moderator_id="0000"
YOUR_TOKEN="0000"
export DOCKER_BUILDKIT=1
#Отримання директорії, де знаходиться скрипт
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Задаємо ім'я папки, яку потрібно перевірити (наприклад, "docker_folder")
TARGET_FOLDER="docker_folder"
TARGET_DIR="${SCRIPT_DIR}/${TARGET_FOLDER}"

# Умова: якщо папка існує, переходимо в неї та виконуємо команди
if [ -d "$TARGET_DIR" ]; then
    cd "$TARGET_DIR" || { echo "Не вдалося перейти до каталогу $TARGET_DIR"; exit 1; }
    # Увімкнення Docker
    sudo systemctl enable docker
    # Запуск docker-compose
    sudo docker-compose up
    exit
fi

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
    sudo apt-get install docker-ce docker-ce-cli containerd.io -y >/dev/null 2>&1
fi

if ! docker buildx version &>/dev/null; then
    echo "Installing buildx..."
    mkdir -p ~/.docker/cli-plugins/
    curl -sL https://github.com/docker/buildx/releases/latest/download/buildx-$(uname -s)-$(uname -m) -o ~/.docker/cli-plugins/docker-buildx
    chmod +x ~/.docker/cli-plugins/docker-buildx
fi
REPO_URL="https://github.com/Slithon/telegram_bot_hetzner"
CLONE_DIR="telegram_bot_hetzner"

if [ ! -d "$CLONE_DIR" ]; then
    git clone "$REPO_URL" >/dev/null 2>&1
fi

cd "$CLONE_DIR" || exit

# Вказуємо файл docker-compose
COMPOSE_FILE="docker-compose.yml"
# Замінюємо токен у файлі docker-compose.yml, підставляючи значення YOUR_TOKEN
sed -i 's|TELEGRAM_TOKEN: TOKEN|TELEGRAM_TOKEN: \"'"$YOUR_TOKEN"'\"|' "$COMPOSE_FILE"
sed -i 's|MODERATOR_ID: MODERATOR|MODERATOR_ID: \"'"$first_moderator_id"'\"|' "$COMPOSE_FILE"


sudo docker buildx create --name mybuilder
sudo docker buildx use mybuilder
sudo docker buildx inspect --bootstrap

sudo docker buildx build --tag telegram_bot_hetzner_app:latest --load .
sudo docker-compose up