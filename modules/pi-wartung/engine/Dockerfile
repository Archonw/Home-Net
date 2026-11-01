FROM debian:trixie-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      bash rsync e2fsprogs dosfstools fdisk util-linux mount openssh-client xz-utils gzip pigz \
      curl procps findutils coreutils python3 ca-certificates >/dev/null \
    && rm -rf /var/lib/apt/lists/*
COPY engine/pi-backup.sh engine/engine_socket.py /app/
ENV PIW_ROOT=/data/pi-wartung/images
CMD ["python3", "/app/engine_socket.py"]
