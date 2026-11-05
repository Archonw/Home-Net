# Nextcloud-Verwaltung: spricht über die OCS-API mit Nextcloud, der Zugang erreicht sie über eine Socket-Datei.
FROM python:3.12-alpine
RUN apk add --no-cache tzdata ca-certificates
WORKDIR /app
COPY service/server.py service/nextcloud.py service/index.html service/
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/service/server.py"]
