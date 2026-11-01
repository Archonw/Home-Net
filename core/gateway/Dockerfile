FROM python:3.12-alpine
WORKDIR /app
RUN pip install --no-cache-dir -q cryptography
COPY core/gateway/server.py core/gateway/webauthn.py core/gateway/security.py core/gateway/events.py core/gateway/portal.html core/gateway/login.html ./
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "/app/server.py"]
