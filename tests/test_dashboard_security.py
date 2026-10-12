"""Dashboard: encrypt-then-MAC, random session keys, login lockout (roadmap #33/#34)."""
import base64, hashlib, hmac, os
import pytest

pytest.importorskip("cryptography")
pytest.importorskip("fastapi")
from dashboard import server as srv


def _client_encrypt(skey, text, tag=True):
    """Mirror of app.html _encrypt()."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    aes = hashlib.sha256(skey.encode() + srv._AES_SALT).digest()
    mac = hashlib.sha256(b"MAC|" + skey.encode() + srv._AES_SALT).digest()
    iv = os.urandom(16)
    p = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(aes), modes.CBC(iv)).encryptor()
    body = iv + enc.update(p.update(text.encode()) + p.finalize()) + enc.finalize()
    if tag:
        body += hmac.new(mac, body, hashlib.sha256).digest()
    return base64.b64encode(body).decode()


def _dec(skey, enc):
    return srv._decrypt_cbc(srv._derive_key(skey), enc, srv._derive_mac_key(skey))


def test_roundtrip():
    k = srv._new_session_key()
    assert _dec(k, _client_encrypt(k, "open notepad")) == "open notepad"


def test_session_key_is_random_and_long():
    a, b = srv._new_session_key(), srv._new_session_key()
    assert a != b and len(a) >= 40


def test_tampered_ciphertext_rejected():
    k = srv._new_session_key()
    raw = bytearray(base64.b64decode(_client_encrypt(k, "hello world")))
    raw[20] ^= 1
    with pytest.raises(ValueError):
        _dec(k, base64.b64encode(bytes(raw)).decode())


def test_missing_or_wrong_mac_rejected():
    k = srv._new_session_key()
    with pytest.raises(ValueError):
        _dec(k, _client_encrypt(k, "x", tag=False))
    with pytest.raises(ValueError):
        _dec(srv._new_session_key(), _client_encrypt(k, "x"))


def test_mac_key_is_required():
    k = srv._new_session_key()
    with pytest.raises(ValueError):
        srv._decrypt_cbc(srv._derive_key(k), _client_encrypt(k, "x"), b"")


def test_garbage_input_rejected():
    k = srv._new_session_key()
    for bad in ("", "not base64!!", base64.b64encode(b"short").decode()):
        with pytest.raises(Exception):
            _dec(k, bad)


def test_limiter_locks_after_five_and_expires():
    t = [1000.0]
    lim = srv._LoginLimiter(clock=lambda: t[0])
    for _ in range(4):
        lim.fail("1.2.3.4")
    assert lim.locked("1.2.3.4") == 0
    lim.fail("1.2.3.4")
    assert lim.locked("1.2.3.4") > 0
    assert lim.locked("9.9.9.9") == 0                      # per-IP
    t[0] += srv._LOCKOUT_S + 1
    assert lim.locked("1.2.3.4") == 0


def test_limiter_window_forgets_old_failures_and_ok_resets():
    t = [0.0]
    lim = srv._LoginLimiter(clock=lambda: t[0])
    for _ in range(4):
        lim.fail("a")
    t[0] += srv._FAIL_WINDOW + 1
    lim.fail("a")
    assert lim.locked("a") == 0
    for _ in range(3):
        lim.fail("a")
    lim.ok("a")
    lim.fail("a")
    assert lim.locked("a") == 0
