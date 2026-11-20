"""Fallback dynamic rule sources.

Given/When/Then:
1. Given the chosen source fails, when a request is signed, then a fallback
   source's rule is used and the source used is logged.
2. Given every source fails, when a request is signed, then
   DynamicRuleUnavailable lists each source's reason, says the auth isn't the
   problem, and nothing is cached, so the next request tries again.
3. Given the chosen source works, then no other source is asked.
4. Given the chosen source sends an unusable rule (incomplete, or obsolete),
   then a fallback source's rule is used.
5. Given every source fails, when the auth is checked, then it's reported as
   couldn't check, not as bad auth.
"""

import json
import logging
import ssl
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

import ofscraper.data.api.init as init
import ofscraper.utils.auth.request as request

URLS = {
    "datawhores": "DATAWHORES_URL",
    "xagler": "XAGLER_URL",
    "rafa": "RAFA_URL",
    "digitalcriminals": "DIGITALCRIMINALS",
}


def rule(prefix):
    return {
        "static_param": "abc",
        "format": f"{prefix}:{{}}:{{:x}}:2",
        "checksum_indexes": [1, 2],
        "checksum_constant": 3,
    }


def timeout(url):
    return httpx.ConnectTimeout("timed out", request=httpx.Request("GET", url))


def not_found(url):
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        "not found", request=req, response=httpx.Response(404, request=req)
    )


class NotJson:
    pass


@pytest.fixture
def hosts(monkeypatch):
    """Each source's host answers with replies[source]; requests are recorded."""
    replies = {}
    asked = []
    by_url = {}
    for source, key in URLS.items():
        url = f"https://{source}.rules.example/rule.json"
        monkeypatch.setenv(key, url)
        by_url[url] = source

    @contextmanager
    def requests(url, *args, **kwargs):
        source = by_url[url]
        asked.append(source)
        reply = replies[source]
        if isinstance(reply, Exception):
            raise reply
        if reply is NotJson:
            yield SimpleNamespace(
                json_=lambda: json.loads("<html>block page</html>")
            )
            return
        yield SimpleNamespace(json_=lambda: reply)

    @contextmanager
    def get_session(*args, **kwargs):
        yield SimpleNamespace(requests=requests)

    monkeypatch.setattr(
        request.manager,
        "Manager",
        SimpleNamespace(session=SimpleNamespace(get_session=get_session)),
    )
    monkeypatch.setattr(request, "curr_auth", None)
    monkeypatch.setattr(request, "last_check", None)
    cached = []
    monkeypatch.setattr(request.cache, "set", lambda *a, **k: cached.append(a))
    return SimpleNamespace(replies=replies, asked=asked, cached=cached)


def choose(monkeypatch, source):
    monkeypatch.setattr(
        request.settings,
        "get_settings",
        lambda: SimpleNamespace(anon=False, dynamic_rules=source),
    )


# 1. the chosen source fails


def test_failed_source_falls_back(hosts, monkeypatch, caplog):
    choose(monkeypatch, "xagler")
    hosts.replies.update(
        xagler=timeout("https://xagler.rules.example/rule.json"),
        digitalcriminals=rule("100"),
    )
    with caplog.at_level(logging.INFO, logger="shared"):
        auth = request.get_request_auth()

    assert auth[1] == "100:{}:{:x}:2"
    assert hosts.asked == ["xagler", "digitalcriminals"]
    assert (
        "Using the digitalcriminals dynamic rule instead (xagler: couldn't reach "
        "xagler.rules.example (timed out))" in caplog.text
    )
    # the fallback's rule is cached like any other
    assert request.curr_auth == auth
    assert hosts.cached


def test_failed_default_source_is_not_tried_twice(hosts, monkeypatch):
    choose(monkeypatch, "dc")
    hosts.replies.update(
        digitalcriminals=not_found("https://digitalcriminals.rules.example/rule.json"),
        rafa=rule("200"),
    )
    auth = request.get_request_auth()

    assert auth[1] == "200:{}:{:x}:2"
    assert hosts.asked == ["digitalcriminals", "rafa"]


def test_manual_rule_not_set_falls_back(hosts, monkeypatch, caplog):
    choose(monkeypatch, "manual")
    monkeypatch.delenv("DYNAMIC_RULE_MANUAL", raising=False)
    monkeypatch.delenv("OFSC_DYNAMIC_RULE_MANUAL", raising=False)
    hosts.replies.update(digitalcriminals=rule("100"))
    with caplog.at_level(logging.INFO, logger="shared"):
        auth = request.get_request_auth()

    assert auth[1] == "100:{}:{:x}:2"
    assert "manual: OFSC_DYNAMIC_RULE_MANUAL isn't set" in caplog.text


# 2. every source fails


def test_no_usable_rule_raises_with_each_reason(hosts, monkeypatch):
    choose(monkeypatch, "xagler")
    hosts.replies.update(
        xagler=timeout("https://xagler.rules.example/rule.json"),
        digitalcriminals=not_found("https://digitalcriminals.rules.example/rule.json"),
        rafa=NotJson,
    )
    with pytest.raises(request.DynamicRuleUnavailable) as raised:
        request.get_request_auth()

    message = str(raised.value)
    assert "xagler: couldn't reach xagler.rules.example (timed out)" in message
    assert "digitalcriminals: digitalcriminals.rules.example returned 404" in message
    assert "rafa: the reply isn't JSON" in message
    assert "This isn't a problem with your auth" in message
    assert [s for s, _ in raised.value.failures] == [
        "xagler",
        "digitalcriminals",
        "rafa",
    ]
    # not cached: the next request asks again
    assert request.curr_auth is None
    assert not hosts.cached
    hosts.asked.clear()
    with pytest.raises(request.DynamicRuleUnavailable):
        request.get_request_auth()
    assert hosts.asked == ["xagler", "digitalcriminals", "rafa"]


def test_certificate_failure_reason_names_the_certificate():
    cert = ssl.SSLCertVerificationError("certificate verify failed")
    cert.verify_message = "self-signed certificate in certificate chain"
    error = httpx.ConnectError(
        "failed", request=httpx.Request("GET", "https://rafa.rules.example/r")
    )
    error.__cause__ = cert
    assert request.failure_reason(error) == (
        "couldn't verify the certificate of rafa.rules.example "
        "(self-signed certificate in certificate chain)"
    )


# 3. a working source


@pytest.mark.parametrize("source", ["xagler", "rafa", "digitalcriminals"])
def test_working_source_asks_no_other(hosts, monkeypatch, caplog, source):
    choose(monkeypatch, source)
    hosts.replies[source] = rule("300")
    with caplog.at_level(logging.INFO, logger="shared"):
        auth = request.get_request_auth()

    assert auth == ("abc", "300:{}:{:x}:2", [1, 2], 3)
    assert hosts.asked == [source]
    assert "instead" not in caplog.text


# 4. an unusable rule


OBSOLETE_PREFIX_RULE = {
    "static_param": "abc",
    "prefix": "26974",
    "suffix": "2",
    "checksum_indexes": [1, 2],
    "checksum_constant": 3,
}


@pytest.mark.parametrize(
    "reply,reason",
    [
        ({"static_param": "abc"}, "unusable rule: missing format"),
        (OBSOLETE_PREFIX_RULE, "unusable rule: obsolete"),
        (rule("26974"), "unusable rule: obsolete"),
    ],
    ids=["incomplete", "obsolete-prefix", "obsolete-format"],
)
def test_unusable_rule_falls_back(hosts, monkeypatch, caplog, reply, reason):
    choose(monkeypatch, "datawhores")
    hosts.replies.update(datawhores=reply, digitalcriminals=rule("100"))
    with caplog.at_level(logging.INFO, logger="shared"):
        auth = request.get_request_auth()

    assert auth[1] == "100:{}:{:x}:2"
    assert f"datawhores: {reason}" in caplog.text


def test_check_rule_rejects_the_obsolete_rule():
    for content in (OBSOLETE_PREFIX_RULE, rule("26974")):
        with pytest.raises(request.UnusableDynamicRule, match="obsolete"):
            request.check_rule(content)
    request.check_rule(rule("100"))


# 5. the auth check


def test_auth_check_reports_no_rule_as_unavailable(hosts, monkeypatch, caplog):
    choose(monkeypatch, "xagler")
    monkeypatch.setattr(init.log, "traceback_", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(init.sites, "is_fansly", lambda: False)
    for source in ["xagler", *request.FALLBACK_SOURCES]:
        hosts.replies[source] = timeout(f"https://{source}.rules.example/rule.json")

    with caplog.at_level(logging.INFO, logger="shared"):
        assert init.getstatus() == "UNAVAILABLE"
    assert "This isn't a problem with your auth" in caplog.text
