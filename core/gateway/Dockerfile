FROM python:3.12-alpine
WORKDIR /app
COPY core/gateway/server.py core/gateway/portal.html ./
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "/app/server.py"]
