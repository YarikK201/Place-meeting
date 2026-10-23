FROM python:3.12-slim
WORKDIR /app
COPY bot.py /app/bot.py
USER 65534:65534
CMD ["python", "bot.py"]
