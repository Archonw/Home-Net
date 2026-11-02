"""Proxy DHCP for the network start: tells PXE clients which file to load, the router keeps handing out addresses.

dnsmasq in proxy mode leaves the file out of the first offer for UEFI clients and waits for a second request on port
4011. Some firmware, a Dell Latitude among them, never sends it and gives up. This answers with the file right in the
first offer (like pixiecore), and also on port 4011 for clients that ask there. dnsmasq only serves TFTP.
"""

import socket
import struct
import threading

MAGIC = b"\x63\x82\x53\x63"
SO_BINDTODEVICE = 25
DISCOVER, OFFER, REQUEST, ACK, INFORM = 1, 2, 3, 5, 8

# Client architecture (option 93) -> file on the TFTP server; UEFI x86-64 gets the signed shim
EFI_X64 = (7, 9)
BIOS = 0


def parse(data):
    """(header fields, options) of a DHCP packet, or None if it is not one."""
    if len(data) < 240 or data[0] != 1 or data[236:240] != MAGIC:
        return None
    options, i = {}, 240
    while i < len(data):
        code = data[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        if i + 1 >= len(data):
            break
        length = data[i + 1]
        options[code] = data[i + 2:i + 2 + length]
        i += 2 + length
    head = {"xid": data[4:8], "flags": data[10:12], "ciaddr": data[12:16], "giaddr": data[24:28],
            "chaddr": data[28:44], "mac": ":".join(f"{b:02x}" for b in data[28:34])}
    return head, options


def boot_file(options):
    """Which file this client should load, or None if it is no PXE client we serve."""
    vendor = options.get(60, b"")
    user_class = options.get(77, b"")
    if not vendor.startswith(b"PXEClient"):
        return None
    arch = struct.unpack("!H", options[93][:2])[0] if len(options.get(93, b"")) >= 2 else BIOS
    if arch in EFI_X64:
        return "shimx64.efi"
    if arch == BIOS:
        # iPXE, once loaded, asks again and identifies itself; then it gets the small script
        return "start.0" if b"iPXE" in user_class else "ipxe.0"
    return None


def reply(head, options, kind, server_ip, filename):
    out = bytearray(240)
    out[0], out[1], out[2] = 2, 1, 6
    out[4:8] = head["xid"]
    out[10:12] = head["flags"]
    out[12:16] = head["ciaddr"]
    out[20:24] = socket.inet_aton(server_ip)
    out[24:28] = head["giaddr"]
    out[28:44] = head["chaddr"]
    out[44:44 + len(server_ip)] = server_ip.encode()
    name = filename.encode()
    out[108:108 + len(name)] = name
    out[236:240] = MAGIC

    def opt(code, value):
        out.extend(bytes([code, len(value)]) + value)

    opt(53, bytes([kind]))
    opt(54, socket.inet_aton(server_ip))
    opt(60, b"PXEClient")
    if 97 in options:
        opt(97, options[97])
    # PXE discovery control 8: take the file from this offer, no menu and no boot server discovery
    opt(43, bytes([6, 1, 8, 255]))
    opt(66, server_ip.encode())
    opt(67, name)
    out.append(255)
    return bytes(out)


class ProxyDHCP:
    def __init__(self, iface, server_ip, log=print, seen=None):
        self.iface = iface
        self.server_ip = server_ip
        self.log = log
        self.seen = seen or (lambda mac: None)
        self.socks = []
        self.running = False

    def open(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, self.iface.encode())
        s.bind(("0.0.0.0", port))
        return s

    def start(self):
        """Raises OSError when the ports cannot be opened."""
        self.socks = [self.open(67), self.open(4011)]
        self.running = True
        for s in self.socks:
            threading.Thread(target=self.serve, args=(s,), daemon=True).start()

    def stop(self):
        self.running = False
        for s in self.socks:
            try:
                s.close()
            except OSError:
                pass
        self.socks = []

    def alive(self):
        return self.running

    def serve(self, sock):
        port = sock.getsockname()[1]
        while self.running:
            try:
                data, addr = sock.recvfrom(4096)
            except OSError:
                break
            try:
                self.handle(sock, port, data, addr)
            except Exception as exc:  # noqa: BLE001 - one odd packet must not stop the network start
                self.log(f"proxydhcp: {type(exc).__name__}: {exc}")

    def handle(self, sock, port, data, addr):
        parsed = parse(data)
        if not parsed:
            return
        head, options = parsed
        kind = options.get(53, b"\0")[0]
        if kind not in (DISCOVER, REQUEST, INFORM):
            return
        filename = boot_file(options)
        if not filename:
            return
        # On port 67 a request is meant for the router, unless it names this server
        if port == 67 and kind != DISCOVER and options.get(54) != socket.inet_aton(self.server_ip):
            return
        answer = reply(head, options, OFFER if kind == DISCOVER else ACK, self.server_ip, filename)
        if port == 4011:
            sock.sendto(answer, addr)
        else:
            sock.sendto(answer, ("255.255.255.255", 68))
        arch = options.get(93, b"").hex() or "-"
        self.log(f"proxydhcp: {head['mac']} arch {arch} port {port} -> {filename}")
        self.seen(head["mac"])
