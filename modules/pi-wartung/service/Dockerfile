FROM python:3.12-alpine
RUN apk add --no-cache openssh-client
WORKDIR /app
COPY piwartung/__init__.py piwartung/core.py piwartung/
COPY service/server.py service/
ENV PYTHONUNBUFFERED=1
USER root
EXPOSE 8080
CMD ["python", "/app/service/server.py"]
