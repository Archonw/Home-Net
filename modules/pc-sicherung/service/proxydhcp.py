"""DHCP for the network start: tells PXE clients which file to load, the router keeps handing out addresses.

Unknown PXE clients get a proxy offer (address from the router, file from us), with the file right in the first offer
and again on port 4011. Some firmware, a Dell Latitude among them, ignores every proxy offer. So PCs added to the module
get a full offer instead: the address, netmask, gateway and lease the router has just offered them, plus our boot file.
They then ask us, not the router, to confirm it; the router keeps the address for them, and the backup system asks the
router again. The files come from tftp.py.
"""

import socket
import struct
import threading
import time

MAGIC = b"\x63\x82\x53\x63"
SO_BINDTODEVICE = 25
IP_MTU_DISCOVER, IP_PMTUDISC_DONT = 10, 0
# Replies are padded to the classic DHCP size; some PXE firmware drops shorter ones
MIN_SIZE = 548
DISCOVER, OFFER, REQUEST, ACK, INFORM = 1, 2, 3, 5, 8
ETH_P_IP = 0x0800
# Copied from the router's offer into a full offer: netmask, gateway, DNS, domain, broadcast, lease, T1, T2
LEASE_OPTIONS = (1, 3, 6, 15, 28, 51, 58, 59)
ROUTER_WAIT = 3

# Client architecture (option 93) -> file on the TFTP server; UEFI x86-64 gets the signed shim
EFI_X64 = (7, 9)
BIOS = 0


def parse(data, op=1):
    """(header fields, options) of a DHCP request (op 1) or reply (op 2), or None if it is not one."""
    if len(data) < 240 or data[0] != op or data[236:240] != MAGIC:
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
            "chaddr": data[28:44], "yiaddr": data[16:20], "mac": ":".join(f"{b:02x}" for b in data[28:34])}
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


def reply(head, options, kind, server_ip, filename, lease=None):
    """A proxy answer, or with [lease] (address, options from the router's offer) a full one."""
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
    if lease:
        out[16:20] = lease[0]
        for code in LEASE_OPTIONS:
            if code in lease[1]:
                opt(code, lease[1][code])
    opt(60, b"PXEClient")
    if 97 in options:
        opt(97, options[97])
    # UEFI firmware gets the plain answer pixiecore gives, file and server only in the header; a Dell Latitude
    # ignored offers that also carried PXE options
    if not filename.endswith(".efi"):
        # PXE discovery control 8: take the file from this offer, no menu and no boot server discovery
        opt(43, bytes([6, 1, 8, 255]))
        opt(66, server_ip.encode())
        opt(67, name)
    out.append(255)
    if len(out) < MIN_SIZE:
        out.extend(bytes(MIN_SIZE - len(out)))
    return bytes(out)


class ProxyDHCP:
    def __init__(self, iface, server_ip, log=print, seen=None, known=None):
        self.iface = iface
        self.server_ip = server_ip
        self.log = log
        self.seen = seen or (lambda mac: None)
        # PCs of the module get full offers, see the top of this file
        self.known = known or (lambda mac: False)
        self.socks = []
        self.running = False
        self.offers = {}  # xid -> (time, address, options) of the router's latest offer
        self.leases = {}  # mac -> (address, options) we offered
        self.cond = threading.Condition()
        self.sniffer = None

    def open(self, port, address="0.0.0.0", device=True):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if device:
            s.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, self.iface.encode())
        # No "don't fragment" flag, like the router's replies
        s.setsockopt(socket.IPPROTO_IP, IP_MTU_DISCOVER, IP_PMTUDISC_DONT)
        s.bind((address, port))
        return s

    def start(self):
        """Raises OSError when the ports cannot be opened."""
        # Broadcasts on 67 are taken from the interface. Requests to 4011 are sent to our address and bound to it, not
        # to the interface: on Unraid they arrive on shim-br0, which carries the same address as br0.
        self.socks = [self.open(67), self.open(4011, self.server_ip, device=False)]
        self.running = True
        for s in self.socks:
            threading.Thread(target=self.serve, args=(s,), daemon=True).start()
        try:
            self.sniffer = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_IP))
            self.sniffer.bind((self.iface, 0))
            self.sniffer.settimeout(1)
            threading.Thread(target=self.watch_router, daemon=True).start()
        except OSError as exc:
            self.sniffer = None
            self.log(f"proxydhcp: Angebote des Routers nicht lesbar, nur Proxy-Antworten ({exc})")

    def stop(self):
        self.running = False
        for s in self.socks:
            try:
                s.close()
            except OSError:
                pass
        self.socks = []
        if self.sniffer:
            self.sniffer.close()
            self.sniffer = None

    def alive(self):
        return self.running

    def watch_router(self):
        """Remembers the router's offers, which our socket on port 67 never sees (they go to port 68)."""
        while self.running and self.sniffer:
            try:
                frame = self.sniffer.recv(4096)
            except TimeoutError:
                continue
            except OSError:
                break
            ip = frame[14:]
            if len(ip) < 28 or ip[9] != 17:
                continue
            ihl = (ip[0] & 15) * 4
            sport, dport = struct.unpack("!HH", ip[ihl:ihl + 4])
            if sport != 67 or dport != 68 or ip[12:16] == socket.inet_aton(self.server_ip):
                continue
            parsed = parse(ip[ihl + 8:], op=2)
            if not parsed or parsed[1].get(53) != bytes([OFFER]) or parsed[0]["yiaddr"] == bytes(4):
                continue
            head, options = parsed
            with self.cond:
                now = time.time()
                self.offers = {x: o for x, o in self.offers.items() if now - o[0] < 60}
                self.offers[head["xid"]] = (now, head["yiaddr"], options)
                self.cond.notify_all()

    def router_offer(self, xid):
        with self.cond:
            self.cond.wait_for(lambda: xid in self.offers, timeout=ROUTER_WAIT)
            found = self.offers.get(xid)
        return (found[1], found[2]) if found else None

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
        mac = head["mac"]
        # On port 67 a request is meant for the router, unless it names this server
        if port == 67 and kind != DISCOVER and options.get(54) != socket.inet_aton(self.server_ip):
            return
        lease = None
        if port == 67 and self.known(mac) and self.sniffer:
            if kind == DISCOVER:
                # Waiting for the router must not hold up other clients
                threading.Thread(target=self.offer_full, args=(sock, head, options, filename), daemon=True).start()
                return
            lease = self.leases.get(mac)
            if not lease:
                return
        elif port == 4011:
            lease = self.leases.get(mac)
        self.send(sock, port, addr, head, options, OFFER if kind == DISCOVER else ACK, filename, lease)

    def offer_full(self, sock, head, options, filename):
        lease = self.router_offer(head["xid"])
        if lease:
            self.leases[head["mac"]] = lease
        try:
            self.send(sock, 67, None, head, options, OFFER, filename, lease)
        except OSError as exc:
            self.log(f"proxydhcp: {type(exc).__name__}: {exc}")

    def send(self, sock, port, addr, head, options, kind, filename, lease):
        answer = reply(head, options, kind, self.server_ip, filename, lease)
        if port == 4011:
            sock.sendto(answer, addr)
        else:
            sock.sendto(answer, ("255.255.255.255", 68))
        arch = options.get(93, b"").hex() or "-"
        how = f"mit Adresse {socket.inet_ntoa(lease[0])}" if lease else "proxy"
        self.log(f"proxydhcp: {head['mac']} {'Angebot' if kind == OFFER else 'Bestätigung'} {how} arch {arch} "
                 f"port {port} -> {filename}")
        self.seen(head["mac"])
