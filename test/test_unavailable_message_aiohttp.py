"""
unavailable_message only knew httpx errors. The async session (the GUI's
model list) raises aiohttp errors, so a network failure there was treated
as a problem with the auth.
"""

import ssl

import aiohttp
import httpx
import pytest
from aiohttp.client_reqrep import ConnectionKey, RequestInfo
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

import ofscraper.__main__  # noqa: F401  resolves the settings/console import cycle
from ofscraper.data.api.init import unavailable_message

HOST = "onlyfans.com"
URL_ = f"https://{HOST}/api2/v2/subscriptions/subscribes"


def _conn_key():
    return ConnectionKey(
        host=HOST,
        port=443,
        is_ssl=True,
        ssl=True,
        proxy=None,
        proxy_auth=None,
        proxy_headers_hash=None,
    )


def _response_error(status):
    info = RequestInfo(URL(URL_), "GET", CIMultiDictProxy(CIMultiDict()))
    return aiohttp.ClientResponseError(info, (), status=status, message="x")


def test_connector_error_names_the_host_and_cause():
    e = aiohttp.ClientConnectorError(_conn_key(), OSError(11001, "getaddrinfo failed"))
    message = unavailable_message(e)
    assert message.startswith(
        f"Couldn't reach {HOST} (connection failed). Check your connection or VPN. "
        "Error: "
    )
    assert "getaddrinfo failed" in message


def test_server_disconnected_is_a_connection_failure():
    assert unavailable_message(aiohttp.ServerDisconnectedError()) == (
        "Couldn't reach the server (connection failed). "
        "Check your connection or VPN. Error: Server disconnected"
    )


@pytest.mark.parametrize(
    "error",
    [
        aiohttp.ServerTimeoutError("Timeout on reading data from socket"),
        aiohttp.ConnectionTimeoutError("Connection timeout to host"),
        aiohttp.SocketTimeoutError("Timeout on reading data from socket"),
    ],
)
def test_timeouts_say_timed_out(error):
    assert unavailable_message(error) == (
        "Couldn't reach the server (timed out). Check your connection or VPN."
    )


def test_certificate_error_gets_the_certificate_message():
    cause = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
    )
    cause.verify_message = "unable to get local issuer certificate"
    e = aiohttp.ClientConnectorCertificateError(_conn_key(), cause)
    assert unavailable_message(e) == (
        f"Couldn't verify the certificate of {HOST} "
        "(unable to get local issuer certificate). A proxy, VPN or antivirus may "
        "be intercepting HTTPS, or this Python has no CA certificates."
    )


def test_other_ssl_error_is_a_connection_failure():
    e = aiohttp.ClientConnectorSSLError(
        _conn_key(), ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number")
    )
    message = unavailable_message(e)
    assert message.startswith(f"Couldn't reach {HOST} (connection failed).")
    assert "WRONG_VERSION_NUMBER" in message


def test_rate_limit():
    assert unavailable_message(_response_error(429)) == (
        f"{HOST} is rate limiting requests. Try again later."
    )


@pytest.mark.parametrize("status", [500, 503])
def test_server_error(status):
    assert unavailable_message(_response_error(status)) == (
        f"{HOST} returned {status}: the server had an error. Try again later."
    )


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_left_to_the_auth_check(status):
    assert unavailable_message(_response_error(status)) is None


# the httpx messages are unchanged


def _httpx_status_error(status):
    request = httpx.Request("GET", URL_)
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("x", request=request, response=response)


def test_httpx_messages_unchanged():
    request = httpx.Request("GET", URL_)
    assert unavailable_message(httpx.ReadTimeout("", request=request)) == (
        f"Couldn't reach {HOST} (timed out). Check your connection or VPN."
    )
    assert unavailable_message(_httpx_status_error(429)) == (
        f"{HOST} is rate limiting requests. Try again later."
    )
    assert unavailable_message(_httpx_status_error(503)) == (
        f"{HOST} returned 503: the server had an error. Try again later."
    )
    assert unavailable_message(_httpx_status_error(401)) is None
    assert unavailable_message(ValueError("x")) is None
