import ipaddress

import pytest

from pdfread.urlfetch import (
    UrlRejected,
    _blocked_ip,
    normalize_url,
    validate_url,
)


class TestNormalize:
    def test_arxiv_abs_to_pdf(self):
        assert (
            normalize_url("https://arxiv.org/abs/2501.01423")
            == "https://arxiv.org/pdf/2501.01423"
        )

    def test_arxiv_html_to_pdf(self):
        assert (
            normalize_url("https://arxiv.org/html/2501.01423v2")
            == "https://arxiv.org/pdf/2501.01423v2"
        )

    def test_arxiv_pdf_unchanged(self):
        url = "https://arxiv.org/pdf/2501.01423"
        assert normalize_url(url) == url

    def test_other_urls_unchanged(self):
        url = "https://example.com/papers/a.pdf"
        assert normalize_url(url) == url


class TestValidateUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.com/a.pdf",
            "file:///etc/passwd",
            "https://user:pass@example.com/a.pdf",
            "https:///no-host.pdf",
            "not-a-url",
        ],
    )
    def test_reject_malformed(self, url):
        with pytest.raises(UrlRejected):
            validate_url(url, resolve=False)

    def test_reject_literal_loopback(self):
        with pytest.raises(UrlRejected):
            validate_url("http://127.0.0.1/x.pdf", resolve=False)

    def test_reject_literal_private(self):
        with pytest.raises(UrlRejected):
            validate_url("http://192.168.1.10/x.pdf", resolve=False)

    def test_accept_literal_public(self):
        assert validate_url("https://1.1.1.1/x.pdf", resolve=False)


class TestBlockedIp:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1", "10.1.2.3", "11.0.0.9", "192.168.1.1",
            "169.254.1.1", "172.16.0.1", "172.31.255.1",
            "9.1.1.1", "21.0.0.1", "30.0.0.5",
            "100.64.1.1", "0.1.2.3", "224.0.0.1", "240.0.0.1",
            "::1", "fd00::1", "fe80::1", "::ffff:127.0.0.1",
        ],
    )
    def test_blocked(self, ip):
        assert _blocked_ip(ipaddress.ip_address(ip))

    @pytest.mark.parametrize("ip", ["1.1.1.1", "8.8.8.8", "140.82.112.3"])
    def test_allowed(self, ip):
        assert not _blocked_ip(ipaddress.ip_address(ip))
