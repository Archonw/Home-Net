# Pocket-ID-Verwaltung: spricht über die REST-API mit Pocket ID, der Zugang erreicht sie über eine Socket-Datei.
FROM python:3.12-alpine
RUN apk add --no-cache tzdata
WORKDIR /app
COPY service/server.py service/pocketid.py service/index.html service/
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/service/server.py"]
