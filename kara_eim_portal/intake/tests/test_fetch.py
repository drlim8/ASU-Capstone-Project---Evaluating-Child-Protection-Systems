import io
import socket
from unittest import TestCase
from unittest.mock import Mock, patch

import requests

from intake.services.fetch import (
    BlockedURLError,
    FetchError,
    HostThrottle,
    assert_public_url,
    fetch_bytes,
    fetch_to_file,
)

UA = "KARA-EIM-Intake/1.0"


def fake_getaddrinfo(mapping):
    def _impl(host, port, *args, **kwargs):
        ips = mapping[host]
        if isinstance(ips, str):
            ips = [ips]
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
            for ip in ips
        ]

    return _impl


def resp(status=200, headers=None, chunks=(b"data",)):
    r = Mock()
    r.status_code = status
    r.headers = headers or {}
    r.iter_content = Mock(side_effect=lambda size: iter(chunks))
    r.close = Mock()
    return r


def run(session, url="http://a.gov/x", max_bytes=1000, **kw):
    return fetch_to_file(
        url, io.BytesIO(), max_bytes=max_bytes, timeout=5, user_agent=UA, session=session, **kw
    )


PUBLIC = {"a.gov": "93.184.216.34", "b.gov": "93.184.216.35"}


class AssertPublicUrlTests(TestCase):
    def test_blocks_non_http_scheme(self):
        for url in ("file:///etc/passwd", "ftp://x"):
            with self.assertRaises(BlockedURLError):
                assert_public_url(url)

    def test_blocks_missing_host(self):
        with self.assertRaises(BlockedURLError):
            assert_public_url("http:///path")

    def test_blocks_private_and_loopback(self):
        for ip in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "0.0.0.0", "224.0.0.1"):
            with patch("intake.services.fetch.socket.getaddrinfo", fake_getaddrinfo({"h": ip})):
                with self.assertRaises(BlockedURLError, msg=ip) as cm:
                    assert_public_url("http://h/")
                self.assertFalse(cm.exception.retryable)

    def test_blocks_ipv4_mapped_ipv6(self):
        for ip in ("::ffff:127.0.0.1", "::ffff:10.0.0.1"):
            with patch("intake.services.fetch.socket.getaddrinfo", fake_getaddrinfo({"h": ip})):
                with self.assertRaises(BlockedURLError, msg=ip):
                    assert_public_url("http://h/")

    def test_blocks_if_any_resolved_address_is_private(self):
        with patch(
            "intake.services.fetch.socket.getaddrinfo",
            fake_getaddrinfo({"h": ["93.184.216.34", "10.0.0.1"]}),
        ):
            with self.assertRaises(BlockedURLError):
                assert_public_url("http://h/")

    def test_unresolvable_host_is_retryable_fetch_error(self):
        with patch("intake.services.fetch.socket.getaddrinfo", side_effect=socket.gaierror):
            with self.assertRaises(FetchError) as cm:
                assert_public_url("http://nope.invalid/")
        self.assertNotIsInstance(cm.exception, BlockedURLError)
        self.assertTrue(cm.exception.retryable)

    def test_blocks_backslash_authority_confusion(self):
        with patch("intake.services.fetch.socket.getaddrinfo", fake_getaddrinfo({"a.gov": "93.184.216.34", "127.0.0.1": "127.0.0.1"})):
            for url in ("http://127.0.0.1\\@a.gov/", "http://a.gov/ x", "http://a.gov/\x00"):
                with self.assertRaises(BlockedURLError, msg=url):
                    assert_public_url(url)

    def test_malformed_and_unencodable_hosts_are_blocked(self):
        with self.assertRaises(BlockedURLError):
            assert_public_url("http://[::1/")
        with patch("intake.services.fetch.socket.getaddrinfo", side_effect=UnicodeError):
            with self.assertRaises(BlockedURLError):
                assert_public_url("http://" + "a" * 70 + ".gov/")

    def test_allows_public(self):
        with patch("intake.services.fetch.socket.getaddrinfo", fake_getaddrinfo(PUBLIC)):
            assert_public_url("https://a.gov/")


@patch("intake.services.fetch.socket.getaddrinfo")
class FetchTests(TestCase):
    def setUp(self):
        self.session = Mock()

    def test_blocks_redirect_to_private_ip(self, gai):
        gai.side_effect = fake_getaddrinfo({"a.gov": "93.184.216.34", "internal": "10.0.0.1"})
        first = resp(302, {"Location": "http://internal/"})
        self.session.get.side_effect = [first]
        with self.assertRaises(BlockedURLError):
            run(self.session)
        first.close.assert_called()
        self.assertEqual(self.session.get.call_count, 1)

    def test_follows_redirect_and_records_final_url(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        r1 = resp(302, {"Location": "http://b.gov/y"})
        r2 = resp(200, {"Content-Type": "application/pdf"}, [b"abc"])
        self.session.get.side_effect = [r1, r2]
        result = run(self.session)
        self.assertEqual(result.final_url, "http://b.gov/y")
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.content_type, "application/pdf")
        self.assertEqual(result.size_bytes, 3)
        r1.close.assert_called()
        r2.close.assert_called()

    def test_relative_location_resolved(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        self.session.get.side_effect = [resp(301, {"Location": "/z"}), resp(200)]
        result = run(self.session, url="http://a.gov/x/y")
        self.assertEqual(result.final_url, "http://a.gov/z")

    def test_redirect_to_non_http_scheme_blocked(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        self.session.get.side_effect = [resp(302, {"Location": "file:///etc/passwd"})]
        with self.assertRaises(BlockedURLError):
            run(self.session)

    def test_backslash_url_direct_never_requests(self, gai):
        gai.side_effect = fake_getaddrinfo({"a.gov": "93.184.216.34", "127.0.0.1": "127.0.0.1"})
        with self.assertRaises(BlockedURLError):
            run(self.session, url="http://127.0.0.1\\@a.gov/")
        self.session.get.assert_not_called()

    def test_backslash_url_in_location_blocked(self, gai):
        gai.side_effect = fake_getaddrinfo({"a.gov": "93.184.216.34", "127.0.0.1": "127.0.0.1"})
        self.session.get.side_effect = [resp(302, {"Location": "http://127.0.0.1\\@a.gov/"})]
        with self.assertRaises(BlockedURLError):
            run(self.session)
        self.assertEqual(self.session.get.call_count, 1)

    def test_malformed_urls_raise_blocked(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        for url in ("http://[::1/", "nohost", "http://"):
            with self.assertRaises(BlockedURLError, msg=url):
                run(self.session, url=url)
        self.session.get.side_effect = [resp(302, {"Location": "http://[::1/"})]
        with self.assertRaises(BlockedURLError):
            run(self.session)

    def test_content_decoding_error_is_non_retryable_fetch_error(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        r = resp(200)
        r.iter_content = Mock(side_effect=requests.exceptions.ContentDecodingError("bad"))
        self.session.get.side_effect = [r]
        with self.assertRaises(FetchError) as cm:
            run(self.session)
        self.assertFalse(cm.exception.retryable)
        r.close.assert_called()

    def test_redirect_without_location_fails(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        self.session.get.side_effect = [resp(302, {})]
        with self.assertRaises(FetchError) as cm:
            run(self.session)
        self.assertFalse(cm.exception.retryable)

    def test_too_many_redirects(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        self.session.get.side_effect = [resp(302, {"Location": "http://a.gov/n"}) for _ in range(6)]
        with self.assertRaises(FetchError) as cm:
            run(self.session)
        self.assertFalse(cm.exception.retryable)

    def test_aborts_over_max_bytes(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        r = resp(200, chunks=[b"x" * 6, b"x" * 5])
        self.session.get.side_effect = [r]
        with self.assertRaises(FetchError) as cm:
            run(self.session, max_bytes=10)
        self.assertFalse(cm.exception.retryable)
        r.close.assert_called()

    def test_exactly_max_bytes_ok(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        self.session.get.side_effect = [resp(200, chunks=[b"x" * 10])]
        self.assertEqual(run(self.session, max_bytes=10).size_bytes, 10)

    def test_content_length_over_max_aborts_before_download(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        r = resp(200, {"Content-Length": "11"})
        self.session.get.side_effect = [r]
        with self.assertRaises(FetchError) as cm:
            run(self.session, max_bytes=10)
        self.assertFalse(cm.exception.retryable)
        r.iter_content.assert_not_called()
        r.close.assert_called()

    def test_bot_challenge_is_rejected_with_clear_message(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        for status, headers in [
            (202, {"x-amzn-waf-action": "challenge", "Content-Type": "text/html"}),
            (200, {"cf-mitigated": "challenge"}),
            (202, {}),
        ]:
            r = resp(status, headers, chunks=[])
            self.session.get.side_effect = [r]
            with self.assertRaises(FetchError) as cm:
                run(self.session)
            self.assertFalse(cm.exception.retryable)
            self.assertEqual(cm.exception.http_status, status)
            self.assertIn("blocked the automated download", str(cm.exception))
            r.iter_content.assert_not_called()

    def test_empty_response_body_fails(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        self.session.get.side_effect = [resp(200, {"Content-Type": "text/html"}, chunks=[])]
        with self.assertRaises(FetchError) as cm:
            run(self.session)
        self.assertFalse(cm.exception.retryable)
        self.assertIn("empty response", str(cm.exception))

    def test_4xx_not_retryable_5xx_retryable(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        self.session.get.side_effect = [resp(404)]
        with self.assertRaises(FetchError) as cm:
            run(self.session)
        self.assertFalse(cm.exception.retryable)
        self.assertEqual(cm.exception.http_status, 404)
        self.session.get.side_effect = [resp(503)]
        with self.assertRaises(FetchError) as cm:
            run(self.session)
        self.assertTrue(cm.exception.retryable)
        self.assertEqual(cm.exception.http_status, 503)

    def test_timeout_and_connection_error_are_retryable(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        for exc in (requests.Timeout, requests.ConnectionError):
            self.session.get.side_effect = exc("boom")
            with self.assertRaises(FetchError) as cm:
                run(self.session)
            self.assertTrue(cm.exception.retryable)

    def test_sends_user_agent_and_disables_redirects(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        self.session.get.side_effect = [resp(200)]
        run(self.session)
        kwargs = self.session.get.call_args.kwargs
        self.assertEqual(kwargs["headers"]["User-Agent"], "KARA-EIM-Intake/1.0")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(kwargs["stream"])

    def test_fetch_bytes(self, gai):
        gai.side_effect = fake_getaddrinfo(PUBLIC)
        self.session.get.side_effect = [resp(200, {"Content-Type": "image/png"}, [b"ab", b"cd"])]
        with patch("intake.services.fetch.requests.Session", return_value=self.session):
            data, ctype = fetch_bytes("http://a.gov/i.png", max_bytes=100, timeout=5, user_agent=UA)
        self.assertEqual((data, ctype), (b"abcd", "image/png"))


class HostThrottleTests(TestCase):
    def test_host_throttle_waits_only_for_same_host(self):
        now = [0.0]
        sleeps = []

        def sleep(s):
            sleeps.append(s)
            now[0] += s

        t = HostThrottle(2.0, clock=lambda: now[0], sleep=sleep)
        t.wait("https://a.gov/1")
        now[0] = 0.5
        t.wait("https://a.gov/2")
        self.assertEqual(sleeps, [1.5])
        t.wait("https://b.gov/")
        self.assertEqual(sleeps, [1.5])
