# Unraid-Verwaltung: spricht nur über Sockets mit dem Unraid-Server, darum ohne Netzwerk.
FROM python:3.12-alpine
RUN apk add --no-cache tzdata
WORKDIR /app
COPY service/server.py service/host.py service/index.html service/
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/service/server.py"]
