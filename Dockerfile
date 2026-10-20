FROM python:3.12-slim

# Создаем непривилегированного пользователя с UID 1000 (стандарт безопасности Hugging Face Spaces)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR $HOME/app

# Копируем проект и устанавливаем зависимости
COPY --chown=user . $HOME/app

RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Открываем стандартный порт Hugging Face
EXPOSE 7860

CMD ["python", "src/bot.py"]
