"""Read-only TFTP server for the network start (RFC 1350 with the blksize, tsize and timeout options).

Its own instead of dnsmasq for one reason: shim takes the firmware's load options as the name of the next file. Dell
firmware passes the description of its boot entry there, so shim asks for something like "\\x01Onboard" instead of
grubx64.efi, and that name differs between machines. Any request for a missing file without an extension gets GRUB.
"""

import os
import socket
import struct
import threading

RRQ, DATA, ACK, ERROR, OACK = 1, 3, 4, 5, 6
DEFAULT_BLOCK = 512
MAX_BLOCK = 8192
RETRIES = 5


def clean_name(raw):
    """The requested path inside the root, or None if it leaves it. Unprintable characters are dropped."""
    name = "".join(ch for ch in raw.replace("\\", "/") if ch.isprintable())
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def resolve(root, raw, fallback):
    """(path on disk, logged name) for a request, or (None, name) if there is no such file."""
    name = clean_name(raw)
    if name is None:
        return None, raw
    path = os.path.join(root, name)
    if name and os.path.isfile(path):
        return path, name
    if fallback and name and "/" not in name and "." not in name:
        return os.path.join(root, fallback), f"{name} (als {fallback})"
    return None, name


def parse_request(data):
    """(file name, mode, options) of a read request, or None."""
    if len(data) < 4 or struct.unpack("!H", data[:2])[0] != RRQ:
        return None
    fields = data[2:].split(b"\0")
    if len(fields) < 2:
        return None
    name, mode = fields[0].decode("latin-1"), fields[1].decode("latin-1").lower()
    options = {}
    rest = fields[2:]
    for i in range(0, len(rest) - 1, 2):
        if rest[i]:
            options[rest[i].decode("latin-1").lower()] = rest[i + 1].decode("latin-1")
    return name, mode, options


def error(code, text):
    return struct.pack("!HH", ERROR, code) + text.encode() + b"\0"


class TFTPServer:
    def __init__(self, root, address, fallback="grubx64.efi", log=print, port=69):
        self.root = root
        self.address = address
        self.fallback = fallback
        self.log = log
        self.port = port
        self.sock = None
        self.running = False

    def start(self):
        """Raises OSError when the port cannot be opened."""
        # Bound to the address, not the interface: on Unraid unicast for it arrives on shim-br0 as well as br0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.address, self.port))
        self.running = True
        threading.Thread(target=self.serve, daemon=True).start()

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def alive(self):
        return self.running

    def serve(self):
        while self.running:
            try:
                data, client = self.sock.recvfrom(2048)
            except OSError:
                break
            request = parse_request(data)
            if request:
                threading.Thread(target=self.transfer, args=(client, *request), daemon=True).start()

    def transfer(self, client, raw, mode, options):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.address, 0))
            path, name = resolve(self.root, raw, self.fallback)
            if mode not in ("octet", "netascii") or not path:
                sock.sendto(error(1, "File not found"), client)
                self.log(f"tftp: {name} nicht gefunden für {client[0]}")
                return
            with open(path, "rb") as f:
                content = f.read()
            self.send(sock, client, content, options)
            self.log(f"tftp: {name} an {client[0]} gesendet")
        except TimeoutError:
            self.log(f"tftp: {client[0]} antwortet nicht mehr")
        except (OSError, ValueError) as exc:
            self.log(f"tftp: {type(exc).__name__}: {exc}")
        finally:
            sock.close()

    def send(self, sock, client, content, options):
        block_size, timeout, accepted = DEFAULT_BLOCK, 2.0, {}
        if "blksize" in options:
            block_size = max(8, min(int(options["blksize"]), MAX_BLOCK))
            accepted["blksize"] = str(block_size)
        if "timeout" in options:
            timeout = float(max(1, min(int(options["timeout"]), 255)))
            accepted["timeout"] = str(int(timeout))
        if "tsize" in options:
            accepted["tsize"] = str(len(content))
        sock.settimeout(timeout)
        if accepted:
            packet = struct.pack("!H", OACK) + b"".join(k.encode() + b"\0" + v.encode() + b"\0"
                                                       for k, v in accepted.items())
            if not self.exchange(sock, client, packet, 0):
                return
        blocks = len(content) // block_size + 1
        for number in range(1, blocks + 1):
            chunk = content[(number - 1) * block_size:number * block_size]
            if not self.exchange(sock, client, struct.pack("!HH", DATA, number & 0xFFFF) + chunk, number & 0xFFFF):
                return

    def exchange(self, sock, client, packet, number):
        """Sends a packet until the client acknowledges it. False if the client gave up (UEFI asks for tsize, then
        aborts, before the real download)."""
        for _ in range(RETRIES):
            sock.sendto(packet, client)
            while True:
                try:
                    data, sender = sock.recvfrom(2048)
                except TimeoutError:
                    break
                if sender != client or len(data) < 4:
                    continue
                kind, value = struct.unpack("!HH", data[:4])
                if kind == ERROR:
                    return False
                if kind == ACK and value == number:
                    return True
        raise TimeoutError
