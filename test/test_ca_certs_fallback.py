"""
A Python with no system CA certificates (a python.org build on macOS before
"Install Certificates.command" is run) failed every HTTPS request, and the
auth check reported it as "connection failed. Check your connection or VPN."

1. The SSL contexts fall back to certifi's CA bundle when the system has none.
2. A certificate verification failure is reported as one.
"""

import ssl
from types import SimpleNamespace

import httpx
import pytest

import ofscraper.__main__  # noqa: F401  resolves the settings/console import cycle
import ofscraper.managers.sessionmanager.cert as cert
import ofscraper.managers.sessionmanager.sessionmanager as sessionmanager
import ofscraper.utils.settings as settings
from ofscraper.data.api.init import unavailable_message


@pytest.fixture
def no_system_certs(monkeypatch, tmp_path):
    cafile = tmp_path / "cert.pem"
    cafile.write_text("")
    capath = tmp_path / "certs"
    capath.mkdir()
    monkeypatch.setenv("SSL_CERT_FILE", str(cafile))
    monkeypatch.setenv("SSL_CERT_DIR", str(capath))
    monkeypatch.setattr(ssl.SSLContext, "load_default_certs", _verify_paths_only)
    monkeypatch.setattr(
        cert.auth_requests.auth_file, "read_auth", lambda: {"auth_id": "11111"}
    )
    monkeypatch.setattr(cert, "_CONFIG_CACHE", {})
    return capath


def _verify_paths_only(self, purpose=ssl.Purpose.SERVER_AUTH):
    # what load_default_certs does off Windows; on Windows it also reads the
    # system store, which the environment variables above don't empty
    self.set_default_verify_paths()


def _session_context(monkeypatch, ssl_verify):
    monkeypatch.setattr(
        settings, "get_settings", lambda: SimpleNamespace(ssl_verify=ssl_verify)
    )
    return sessionmanager.sessionManager._get_ssl_context(None)


@pytest.mark.parametrize("ssl_verify", ["custom", "default"])
def test_falls_back_to_certifi_without_system_certs(
    monkeypatch, no_system_certs, ssl_verify
):
    context = _session_context(monkeypatch, ssl_verify)
    assert context.cert_store_stats()["x509_ca"] > 0


def test_system_certs_are_used_alone_when_present(monkeypatch, no_system_certs):
    # a CA directory's certificates load on demand and aren't in the stats
    (no_system_certs / "0a1b2c3d.0").write_text("")
    context = _session_context(monkeypatch, "custom")
    assert context.cert_store_stats()["x509_ca"] == 0


def test_no_fallback_when_verification_is_off(no_system_certs):
    context = cert.create_custom_ssl_context(
        check_hostname=False, verify_mode=ssl.CERT_NONE
    )
    assert context.cert_store_stats()["x509_ca"] == 0


def _connect_error(cause):
    request = httpx.Request("GET", "https://raw.githubusercontent.com/a/b.json")
    try:
        try:
            raise cause
        except Exception as inner:
            raise httpx.ConnectError(str(inner), request=request) from inner
    except httpx.ConnectError as e:
        return e


def test_certificate_failure_is_not_called_a_connection_failure():
    cause = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
    )
    cause.verify_message = "unable to get local issuer certificate"
    message = unavailable_message(_connect_error(cause))
    assert "certificate" in message
    assert "unable to get local issuer certificate" in message
    assert "raw.githubusercontent.com" in message
    assert "connection failed" not in message


def test_connect_error_message_carries_the_cause():
    message = unavailable_message(
        _connect_error(OSError("[Errno 11001] getaddrinfo failed"))
    )
    assert message == (
        "Couldn't reach raw.githubusercontent.com (connection failed). "
        "Check your connection or VPN. Error: [Errno 11001] getaddrinfo failed"
    )


def test_connect_error_without_text_has_no_empty_cause():
    request = httpx.Request("GET", "https://onlyfans.com/api2/v2/users/me")
    message = unavailable_message(httpx.ReadError("", request=request))
    assert message == (
        "Couldn't reach onlyfans.com (connection failed). "
        "Check your connection or VPN."
    )
