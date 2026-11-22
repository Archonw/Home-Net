# Preisalarm: liest Preise von Produktseiten und einen Deal-Feed, der Zugang erreicht ihn über eine Socket-Datei.
FROM python:3.12-alpine
RUN apk add --no-cache tzdata
WORKDIR /app
COPY service/server.py service/preise.py service/index.html service/
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/service/server.py"]
