#!/usr/bin/env python3
"""Runs in the engine container: answers the service over a unix socket and starts scheduled backups.

The service sends one line, the same words as the forced ssh command on Unraid. pi-backup.sh checks them
in cmd_api, so only that list is possible. Only the service container can reach this socket.
"""
import json
import os
import socketserver
import subprocess
import threading
import time

ENGINE = "/app/pi-backup.sh"
SOCKET = os.environ.get("ENGINE_SOCKET", "/run/pi-wartung/engine.sock")
ROOT = os.environ.get("PIW_ROOT", "/data/pi-wartung/images")
SCHEDULE_SECONDS = 3600


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        command = self.rfile.readline(4096).decode(errors="replace").strip()
        env = dict(os.environ, SSH_ORIGINAL_COMMAND=command)
        try:
            p = subprocess.run(["bash", ENGINE, "api"], env=env, capture_output=True, text=True, timeout=180)
            reply = {"code": p.returncode, "out": p.stdout, "err": p.stderr}
        except subprocess.TimeoutExpired:
            reply = {"code": 124, "out": "", "err": "Zeitüberschreitung"}
        self.wfile.write(json.dumps(reply).encode())


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def scheduler():
    while True:
        time.sleep(SCHEDULE_SECONDS)
        subprocess.run(["bash", ENGINE, "due", ROOT], check=False)


def main():
    os.makedirs(os.path.dirname(SOCKET), exist_ok=True)
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    threading.Thread(target=scheduler, daemon=True).start()
    with Server(SOCKET, Handler) as server:
        os.chmod(SOCKET, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
