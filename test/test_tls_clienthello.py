import socket
import ssl
import struct
import threading
import warnings

import ofscraper.__main__  # noqa: F401  resolves the settings/console import cycle
from ofscraper.managers.sessionmanager.cert import create_custom_ssl_context

# OP_NO_SSLv3 | OP_NO_TLSv1 | OP_NO_TLSv1_1 as raw bits; the named constants
# are deprecated
OLD_PROTOCOL_BITS = 0x02000000 | 0x04000000 | 0x10000000


def _client_hello(ctx):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    out = {}

    def accept():
        conn, _ = srv.accept()
        data = b""
        while len(data) < 5 or len(data) < 5 + struct.unpack(">H", data[3:5])[0]:
            data += conn.recv(65536)
        out["hello"] = data
        conn.close()

    t = threading.Thread(target=accept)
    t.start()
    sock = socket.create_connection(srv.getsockname())
    try:
        ctx.wrap_socket(sock, server_hostname="onlyfans.com").do_handshake()
    except (ssl.SSLError, OSError):
        pass
    t.join()
    srv.close()
    return out["hello"]


def _fingerprint(hello):
    """Fields a TLS fingerprint can see, minus the per-connection random values."""
    p = 5 + 4 + 2 + 32
    session_id_len = hello[p]
    p += 1 + session_id_len
    n = struct.unpack(">H", hello[p : p + 2])[0]
    ciphers = hello[p + 2 : p + 2 + n]
    p += 2 + n
    p += 1 + hello[p]
    end = p + 2 + struct.unpack(">H", hello[p : p + 2])[0]
    p += 2
    exts = {}
    order = []
    while p < end:
        t, length = struct.unpack(">HH", hello[p : p + 4])
        order.append(t)
        exts[t] = hello[p + 4 : p + 4 + length]
        p += 4 + length
    exts.pop(51, None)  # key_share is random per connection
    return {
        "session_id_len": session_id_len,
        "ciphers": ciphers,
        "extension_order": order,
        "extensions": exts,
        "length": len(hello),
    }


def test_context_builds_without_deprecated_ssl_flags():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        ctx = create_custom_ssl_context()
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2


def test_client_hello_matches_previous_options():
    new = create_custom_ssl_context()
    old = create_custom_ssl_context()
    old.options = ssl.Options(int(old.options) | OLD_PROTOCOL_BITS)
    assert _fingerprint(_client_hello(new)) == _fingerprint(_client_hello(old))


def test_client_hello_session_id_stays_empty():
    # Python's defaults would turn on middlebox compat and send a 32-byte
    # session ID, which changes the fingerprint
    assert _fingerprint(_client_hello(create_custom_ssl_context()))["session_id_len"] == 0
