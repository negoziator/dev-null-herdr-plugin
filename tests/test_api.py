import io
import json
import subprocess
import urllib.error
import urllib.request
from unittest import mock

from devnull_herdr import api, paths
from tests.helpers import Isolated


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ApiTest(Isolated):
    def setUp(self):
        super().setUp()
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(api.key_path())], check=True)
        paths.write_json(api.account_path(), {"username": "me"})
        self.sent = []

    def opener(self, answer=b'{"data": []}', error=None):
        def open_(request, timeout):
            self.sent.append(request)
            if error:
                raise error
            return Response(answer)

        return open_

    def verify(self, request):
        """Checks the request's signature as /dev/null does, with ssh-keygen."""
        headers = {key.lower(): value for key, value in request.header_items()}
        uri = request.full_url[len(api.BASE):]
        body = (request.data or b"").decode()
        message = api.message(headers["x-devnull-timestamp"], headers["x-devnull-nonce"], request.get_method(), uri, body)
        (self.tmp / "sig").write_text("-----BEGIN SSH SIGNATURE-----\n" + headers["x-ssh-signature"] + "\n-----END SSH SIGNATURE-----\n")
        (self.tmp / "allowed").write_text("me " + (self.tmp / "config" / "device_ed25519.pub").read_text())
        return subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-f", str(self.tmp / "allowed"), "-I", "me", "-n", "devnull-api", "-s", str(self.tmp / "sig")],
            input=message, capture_output=True,
        ).returncode == 0

    def test_a_signed_get(self):
        api.request("GET", "/api/v1/log/latest", {"cursor": "a b=", "since": None}, opener=self.opener())
        request = self.sent[0]
        self.assertEqual(request.full_url, "https://dev-null.sh/api/v1/log/latest?cursor=a+b%3D")
        self.assertRegex(request.get_header("X-devnull-nonce"), r"^[0-9a-f]{32}$")
        self.assertNotIn("\n", request.get_header("X-ssh-signature"))
        self.assertTrue(self.verify(request))

    def test_a_signed_post_with_a_body(self):
        api.request("POST", "/api/v1/devices", body={"code": "c"}, opener=self.opener(b"{}"))
        self.assertEqual(self.sent[0].get_header("Content-type"), "application/json")
        self.assertTrue(self.verify(self.sent[0]))

    def test_only_allowed_requests_are_sent(self):
        for method, path in [("DELETE", "/api/v1/me"), ("POST", "/api/v1/commits"), ("PATCH", "/api/v1/me"),
                             ("GET", "/api/v1/log/latest/../../me"), ("DELETE", "/api/v1/devices")]:
            with self.assertRaises(api.ApiError) as raised:
                api.request(method, path, opener=self.opener())
            self.assertEqual(raised.exception.code, "not_allowed")
        self.assertEqual(self.sent, [])

    def test_not_signed_in(self):
        api.account_path().unlink()
        with self.assertRaises(api.NotSignedIn):
            api.me()

    def test_errors(self):
        error = urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "12"},
                                       io.BytesIO(b'{"message": "Slow down", "code": "rate_limited"}'))
        with self.assertRaises(api.ApiError) as raised:
            api.request("GET", "/api/v1/me", opener=self.opener(error=error))
        self.assertEqual((raised.exception.status, raised.exception.code, raised.exception.retry_after), (429, "rate_limited", 12))
        revoked = urllib.error.HTTPError("u", 401, "No", {}, io.BytesIO(b'{"message": "x", "code": "device_not_approved"}'))
        with self.assertRaisesRegex(api.ApiError, "signed out"):
            api.request("GET", "/api/v1/me", opener=self.opener(error=revoked))

    def test_redirects_are_never_followed(self):
        import http.server
        import threading

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://evil.example/steal")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=server.handle_request, daemon=True).start()
        try:
            with mock.patch.object(api, "BASE", f"http://127.0.0.1:{server.server_port}"):
                # The plugin's own opener, freshly built: the shared one is blocked in tests.
                opener = urllib.request.build_opener(api._NoRedirects).open
                with self.assertRaisesRegex(api.ApiError, "redirected"):
                    api.request("GET", "/api/v1/me", opener=opener)
        finally:
            server.server_close()

    def test_odd_answers(self):
        for answer in (b"<html>", b"[1, 2]"):
            with self.assertRaises(api.ApiError) as raised:
                api.request("GET", "/api/v1/me", opener=self.opener(answer))
            self.assertEqual(raised.exception.code, "bad_answer")

    def test_server_messages_are_cleaned(self):
        error = urllib.error.HTTPError("u", 422, "x", {}, io.BytesIO(b'{"message": "bad\\u001b[2J thing"}'))
        with self.assertRaises(api.ApiError) as raised:
            api.request("GET", "/api/v1/me", opener=self.opener(error=error))
        self.assertEqual(str(raised.exception), "bad[2J thing")

    def test_star_and_unstar(self):
        uuid = "01a155b8-e765-719c-b506-12744ccd0236"
        opener = self.opener(json.dumps({"data": {"starred": True}}).encode())
        api.request("POST", f"/api/v1/commits/{uuid}/star", opener=opener)
        api.request("DELETE", f"/api/v1/commits/{uuid}/star", opener=opener)
        self.assertEqual([request.get_method() for request in self.sent], ["POST", "DELETE"])
