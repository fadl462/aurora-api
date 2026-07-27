from unittest.mock import MagicMock, patch

from app.login_activity import get_client_ip, parse_device, resolve_location


def test_parse_device_chrome_windows():
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    assert parse_device(ua) == "Chrome on Windows"


def test_parse_device_safari_iphone():
    ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    )
    assert parse_device(ua) == "Safari on iPhone"


def test_parse_device_firefox_mac():
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0"
    assert parse_device(ua) == "Firefox on Mac"


def test_parse_device_edge_windows_not_misreported_as_chrome():
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    )
    assert parse_device(ua) == "Edge on Windows"


def test_parse_device_android():
    ua = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36"
    assert parse_device(ua) == "Chrome on Android"


def test_parse_device_none_returns_unknown():
    assert parse_device(None) == "Unknown device"


def test_parse_device_empty_string_returns_unknown():
    assert parse_device("") == "Unknown device"


def test_parse_device_unrecognized_ua_still_returns_a_label():
    assert parse_device("SomeBotCrawler/1.0") == "Unknown browser on Unknown OS"


def test_get_client_ip_prefers_x_forwarded_for():
    headers = {"x-forwarded-for": "203.0.113.5, 10.0.0.1"}
    assert get_client_ip(headers, "10.0.0.1") == "203.0.113.5"


def test_get_client_ip_falls_back_to_direct_client_when_no_proxy_header():
    assert get_client_ip({}, "198.51.100.7") == "198.51.100.7"


def test_get_client_ip_returns_unknown_when_nothing_available():
    assert get_client_ip({}, None) == "unknown"


def test_resolve_location_returns_none_for_loopback():
    assert resolve_location("127.0.0.1") is None


def test_resolve_location_returns_none_for_private_network():
    assert resolve_location("192.168.1.50") is None
    assert resolve_location("10.0.0.5") is None


def test_resolve_location_returns_none_for_garbage_input():
    assert resolve_location("not-an-ip-address") is None


def test_resolve_location_returns_none_for_empty_string():
    assert resolve_location("") is None


def test_resolve_location_returns_real_label_on_successful_lookup():
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "status": "success",
        "city": "Accra",
        "regionName": "Greater Accra",
        "country": "Ghana",
    }
    with patch("app.login_activity.httpx.get", return_value=fake_response):
        assert resolve_location("8.8.8.8") == "Accra, Greater Accra, Ghana"


def test_resolve_location_returns_none_when_service_reports_failure():
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": "fail", "message": "invalid query"}
    with patch("app.login_activity.httpx.get", return_value=fake_response):
        assert resolve_location("8.8.8.8") is None


def test_resolve_location_returns_none_on_network_error_never_raises():
    with patch("app.login_activity.httpx.get", side_effect=Exception("timeout")):
        assert resolve_location("8.8.8.8") is None


def test_resolve_location_handles_partial_data_gracefully():
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": "success", "country": "Ghana"}
    with patch("app.login_activity.httpx.get", return_value=fake_response):
        assert resolve_location("8.8.8.8") == "Ghana"
