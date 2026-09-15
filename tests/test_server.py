"""The HTTP print service."""

import socket
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

    # Same class serve() uses, so these tests exercise the real configuration.
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


def raw_post(url, headers, body=b""):
    """Send a request by hand, so invalid headers can be tested."""
    host, port = url.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=10) as connection:
        request = "POST /print HTTP/1.1\r\nHost: x\r\n" + headers + "\r\n\r\n"
        connection.sendall(request.encode() + body)
        return connection.recv(4096).decode("latin-1", "replace")


def test_a_negative_content_length_is_refused(printed):
    """read(-1) would read to EOF, sailing straight past the size limit."""
    httpd, url = start(printed)
    try:
        response = raw_post(url, "Content-Length: -1\r\n", b"x" * 5000)
        assert "413" in response.splitlines()[0]
        assert printed == []
    finally:
        httpd.shutdown()


def test_a_nonsense_content_length_is_refused_not_a_traceback(printed):
    httpd, url = start(printed)
    try:
        response = raw_post(url, "Content-Length: banana\r\n")
        assert "413" in response.splitlines()[0]
        assert printed == []
    finally:
        httpd.shutdown()


def test_an_oversized_body_is_refused(printed):
    httpd, url = start(printed)
    try:
        response = raw_post(url, f"Content-Length: {server.MAX_BODY_BYTES + 1}\r\n")
        assert "413" in response.splitlines()[0]
        assert printed == []
    finally:
        httpd.shutdown()


def test_an_idle_connection_does_not_block_other_clients(printed):
    """One silent socket used to hold the single-threaded server hostage."""
    httpd, url = start(printed)
    idle = socket.create_connection(("127.0.0.1", httpd.server_address[1]), timeout=10)
    try:
        assert post(url, "hello").status == 200
        assert printed == [["hello"]]
    finally:
        idle.close()
        httpd.shutdown()
