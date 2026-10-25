FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates openssl && rm -rf /var/lib/apt/lists/*
COPY root.cer sub.cer /tmp/
RUN (openssl x509 -inform DER -in /tmp/root.cer -out /usr/local/share/ca-certificates/russian-root.crt 2>/dev/null || openssl x509 -in /tmp/root.cer -out /usr/local/share/ca-certificates/russian-root.crt) && \
    (openssl x509 -inform DER -in /tmp/sub.cer -out /usr/local/share/ca-certificates/russian-sub.crt 2>/dev/null || openssl x509 -in /tmp/sub.cer -out /usr/local/share/ca-certificates/russian-sub.crt) && \
    update-ca-certificates
COPY bot.py catalog.py import_events.py events.json /app/
ENV EVENTS_DB=/data/events.db
RUN mkdir -p /data && chown 65534:65534 /data
USER 65534:65534
CMD ["python", "bot.py"]
