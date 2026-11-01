"""The small part of WebAuthn this gateway needs: ES256 passkeys, no attestation."""
import base64
import hashlib
import hmac
import json
import struct

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

ES256 = -7
FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04
FLAG_ATTESTED = 0x40


class WebAuthnError(Exception):
    pass


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text):
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise WebAuthnError("Ungültige Kodierung") from exc


def cbor_decode(data, pos=0):
    """Enough CBOR for attestation objects and COSE keys; returns (value, next position)."""
    try:
        head = data[pos]
        major, info = head >> 5, head & 0x1F
        pos += 1
        if info < 24:
            arg = info
        elif info in (24, 25, 26, 27):
            size = 1 << (info - 24)
            arg = int.from_bytes(data[pos:pos + size], "big")
            pos += size
        else:
            raise WebAuthnError("CBOR nicht unterstützt")
        if major == 0:
            return arg, pos
        if major == 1:
            return -1 - arg, pos
        if major == 2:
            return bytes(data[pos:pos + arg]), pos + arg
        if major == 3:
            return bytes(data[pos:pos + arg]).decode(), pos + arg
        if major == 4:
            items = []
            for _ in range(arg):
                item, pos = cbor_decode(data, pos)
                items.append(item)
            return items, pos
        if major == 5:
            out = {}
            for _ in range(arg):
                key, pos = cbor_decode(data, pos)
                out[key], pos = cbor_decode(data, pos)
            return out, pos
    except (IndexError, UnicodeDecodeError) as exc:
        raise WebAuthnError("CBOR fehlerhaft") from exc
    raise WebAuthnError("CBOR nicht unterstützt")


def check_client_data(raw, expected_type, challenge, origin):
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise WebAuthnError("Client-Daten fehlerhaft") from exc
    if data.get("type") != expected_type:
        raise WebAuthnError("Falscher Vorgang")
    if not hmac.compare_digest(str(data.get("challenge", "")), challenge):
        raise WebAuthnError("Falsche Challenge")
    if data.get("origin") != origin:
        raise WebAuthnError("Falsche Adresse")


def parse_auth_data(auth, rp_id):
    if len(auth) < 37:
        raise WebAuthnError("Authenticator-Daten zu kurz")
    if not hmac.compare_digest(auth[:32], hashlib.sha256(rp_id.encode()).digest()):
        raise WebAuthnError("Falsche Domain")
    flags = auth[32]
    if not flags & FLAG_USER_PRESENT or not flags & FLAG_USER_VERIFIED:
        raise WebAuthnError("Keine Bestätigung auf dem Gerät")
    return flags, struct.unpack(">I", auth[33:37])[0]


def verify_registration(client_data_b64, attestation_b64, challenge, origin, rp_id):
    """Returns (credential id, public key x, y, sign count) of a new passkey."""
    client_data = unb64url(client_data_b64)
    check_client_data(client_data, "webauthn.create", challenge, origin)
    attestation, _ = cbor_decode(unb64url(attestation_b64))
    if not isinstance(attestation, dict) or "authData" not in attestation:
        raise WebAuthnError("Attestation fehlerhaft")
    auth = attestation["authData"]
    flags, count = parse_auth_data(auth, rp_id)
    if not flags & FLAG_ATTESTED:
        raise WebAuthnError("Kein Schlüssel enthalten")
    pos = 37 + 16
    id_len = int.from_bytes(auth[pos:pos + 2], "big")
    pos += 2
    credential_id = auth[pos:pos + id_len]
    pos += id_len
    key, _ = cbor_decode(auth, pos)
    if not isinstance(key, dict) or key.get(1) != 2 or key.get(3) != ES256 or key.get(-1) != 1:
        raise WebAuthnError("Nur ES256-Passkeys werden unterstützt")
    return b64url(credential_id), b64url(key[-2]), b64url(key[-3]), count


def verify_assertion(client_data_b64, auth_data_b64, signature_b64, passkey, challenge, origin, rp_id):
    """Checks a login; returns the new sign count."""
    client_data = unb64url(client_data_b64)
    check_client_data(client_data, "webauthn.get", challenge, origin)
    auth = unb64url(auth_data_b64)
    _, count = parse_auth_data(auth, rp_id)
    if (count or passkey.get("count")) and count <= passkey.get("count", 0):
        raise WebAuthnError("Der Zähler des Passkeys ist nicht gestiegen")
    numbers = ec.EllipticCurvePublicNumbers(int.from_bytes(unb64url(passkey["x"]), "big"),
                                            int.from_bytes(unb64url(passkey["y"]), "big"), ec.SECP256R1())
    try:
        numbers.public_key().verify(unb64url(signature_b64), auth + hashlib.sha256(client_data).digest(),
                                    ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError) as exc:
        raise WebAuthnError("Signatur ungültig") from exc
    return count
