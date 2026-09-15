"""The HTTP print service."""
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from nemonic import server


@pytest.fixture
def printed():
    return []


def start(printed, token=None, fail=False):
    def print_lines(lines):
        if fail:
            raise RuntimeError("printer on fire")
        printed.append(lines)
        return f"printed {len(lines)} line(s)"

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(print_lines, token))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def post(url, body, token=None):
    request = urllib.request.Request(url + "/print", data=body.encode(), method="POST")
    if token:
        request.add_header("X-Token", token)
    return urllib.request.urlopen(request, timeout=10)


def test_health_is_ok(printed):
    httpd, url = start(printed)
    try:
        with urllib.request.urlopen(url + "/health", timeout=10) as response:
            assert response.status == 200
            assert b"ok" in response.read()
    finally:
        httpd.shutdown()


def test_posting_text_prints_it(printed):
    httpd, url = start(printed)
    try:
        assert post(url, "one\ntwo").status == 200
        assert printed == [["one", "two"]]
    finally:
        httpd.shutdown()


def test_an_empty_body_is_rejected(printed):
    httpd, url = start(printed)
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            post(url, "   \n  ")
        assert caught.value.code == 400
        assert printed == []
    finally:
        httpd.shutdown()


def test_a_wrong_token_is_refused(printed):
    httpd, url = start(printed, token="secret")
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            post(url, "hello", token="wrong")
        assert caught.value.code == 403
        assert printed == []
    finally:
        httpd.shutdown()


def test_a_missing_token_is_refused_when_one_is_required(printed):
    httpd, url = start(printed, token="secret")
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            post(url, "hello")
        assert caught.value.code == 403
    finally:
        httpd.shutdown()


def test_the_right_token_is_accepted(printed):
    httpd, url = start(printed, token="secret")
    try:
        assert post(url, "hello", token="secret").status == 200
        assert printed == [["hello"]]
    finally:
        httpd.shutdown()


def test_an_unknown_path_is_not_found(printed):
    httpd, url = start(printed)
    try:
        request = urllib.request.Request(url + "/elsewhere", data=b"x", method="POST")
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        assert caught.value.code == 404
    finally:
        httpd.shutdown()


def test_a_printer_failure_becomes_a_500_not_a_hang(printed):
    httpd, url = start(printed, fail=True)
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            post(url, "hello")
        assert caught.value.code == 500
        assert b"printer on fire" in caught.value.read()
    finally:
        httpd.shutdown()
