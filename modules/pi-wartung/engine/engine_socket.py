#!/usr/bin/env python3
"""Runs in the engine container: answers the service over a unix socket and starts scheduled backups.

The service sends one line, the same words as the forced ssh command on Unraid. pi-backup.sh checks them
in cmd_api, so only that list is possible. Only the service container can reach this socket.
"""
import json
import os
import re
import shutil
import socketserver
import subprocess
import threading
import time

ENGINE = "/app/pi-backup.sh"
SOCKET = os.environ.get("ENGINE_SOCKET", "/run/pi-wartung/engine.sock")
ROOT = os.environ.get("PIW_ROOT", "/data/pi-wartung/images")
SCHEDULE_SECONDS = 3600


def inside_root(path):
    """Full path of something below the backup folder, or an error."""
    real = os.path.realpath(path)
    root = os.path.realpath(ROOT)
    if real != root and not real.startswith(root + os.sep):
        raise ValueError("Pfad außerhalb des Sicherungsordners")
    return real


def allowed_file(path):
    real = inside_root(path)
    name = os.path.basename(real)
    if name not in ("pi.conf", "inventory.json", "ntfy.conf"):
        raise ValueError("Diese Datei darf nicht geändert werden")
    return real


def pi_folder(path):
    real = inside_root(path)
    if real == os.path.realpath(ROOT) or os.path.basename(real).startswith("."):
        raise ValueError("Kein Pi-Ordner")
    return real


def write_private(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def admin(req):
    """Changes asked for by the web interface. Everything stays inside the backup folder."""
    verb = req.get("verb")
    if verb == "read-file":
        path = allowed_file(req["path"])
        return {"out": open(path).read() if os.path.exists(path) else ""}
    if verb == "put-file":
        write_private(allowed_file(req["path"]), req["text"])
        return {}
    if verb == "move":
        src, dst = pi_folder(req["src"]), pi_folder(req["dst"])
        if os.path.exists(src):
            if os.path.exists(dst):
                raise ValueError("Ziel existiert schon")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(src, dst)
        return {}
    if verb == "rename-images":
        folder = pi_folder(req["dir"])
        old, new = req["old"], req["new"]
        for name in os.listdir(folder):
            if name.startswith(old + "_"):
                os.rename(os.path.join(folder, name), os.path.join(folder, new + name[len(old):]))
        return {}
    if verb == "delete":
        shutil.rmtree(pi_folder(req["dir"]), ignore_errors=True)
        return {}
    if verb == "delete-image":
        folder = pi_folder(req["dir"])
        name = req["file"]
        if "/" in name or not re.search(r"\.img(\.xz|\.gz)?$", name):
            raise ValueError("Ungültiger Dateiname")
        for path in (os.path.join(folder, name), os.path.join(folder, name + ".info")):
            if os.path.exists(path):
                os.unlink(path)
        return {}
    if verb == "install-key":
        p = subprocess.run(["bash", ENGINE, "install-key", pi_folder(req["dir"])], input=req["password"] + "\n",
                           capture_output=True, text=True, timeout=90)
        return {"code": p.returncode, "out": p.stdout, "err": p.stderr}
    if verb == "ntfy-test":
        p = subprocess.run(["bash", ENGINE, "ntfy-test"], capture_output=True, text=True, timeout=40)
        return {"code": p.returncode, "out": p.stdout, "err": p.stderr}
    raise ValueError("Unbekannter Befehl")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        command = self.rfile.readline(4 * 1024 * 1024).decode(errors="replace").strip()
        if command.startswith("{"):
            try:
                reply = {"code": 0, "out": "", "err": ""}
                reply.update(admin(json.loads(command)))
            except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
                reply = {"code": 1, "out": "", "err": str(exc)}
            self.wfile.write(json.dumps(reply).encode())
            return
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
