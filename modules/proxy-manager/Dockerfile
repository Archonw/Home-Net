# Proxy-Manager-Übersicht: liest Nginx Proxy Manager über dessen REST-API, der Zugang erreicht sie über eine Socket-Datei.
FROM python:3.12-alpine
RUN apk add --no-cache tzdata
WORKDIR /app
COPY service/server.py service/npm.py service/index.html service/
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/service/server.py"]
