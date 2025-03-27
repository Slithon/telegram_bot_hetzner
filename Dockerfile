FROM python:3.9

# Копіюємо проект з репозиторію
COPY . /app

# Встановлюємо робочу директорію
WORKDIR /app

# Встановлюємо залежності Python
RUN pip install --no-cache-dir -r requirements.txt

# Запускаємо Python додаток
CMD ["python", "/app/bot.py"]