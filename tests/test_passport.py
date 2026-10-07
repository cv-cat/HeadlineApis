import base64
import unittest
from unittest.mock import patch

import requests
from requests.cookies import create_cookie

from builder.auth import TouTiaoAuth
from builder.passport import CaptchaRequired, PassportError, QRCodeChallenge, TouTiaoPassport


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.cookies = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)


class PassportContractTest(unittest.TestCase):
    def test_browser_wire_contract_uses_cookie_fp_csrf_and_order(self):
        image = "data:image/png;base64," + base64.b64encode(b"png").decode()
        session = Session([
            Response({"message": "success", "error_code": 0,
                      "data": {"token": "token", "qrcode": image}}),
        ])
        jar = requests.cookies.RequestsCookieJar()
        # Keep both domains to prove the mp.toutiao.com cookie is preferred.
        jar.set("s_v_web_id", "shared-fingerprint", domain=".toutiao.com")
        jar.set("s_v_web_id", "mp-fingerprint", domain="mp.toutiao.com")
        jar.set("passport_csrf_token", "csrf-token", domain=".toutiao.com")
        session.cookies = jar

        passport = TouTiaoPassport(session)
        passport.get_qrcode()
        kwargs = session.calls[0][2]
        self.assertEqual(list(kwargs["params"]), [
            "service", "need_logo", "ui_version", "aid", "account_sdk_source",
            "sdk_version", "language", "verifyFp", "fp",
        ])
        self.assertEqual(kwargs["params"]["verifyFp"], "mp-fingerprint")
        self.assertEqual(kwargs["params"]["fp"], "mp-fingerprint")
        self.assertEqual(kwargs["headers"]["x-tt-passport-csrf-token"], "csrf-token")
        self.assertEqual(kwargs["headers"]["Accept"], "application/json, text/javascript")
        self.assertEqual(kwargs["headers"]["Referer"], "https://mp.toutiao.com/")
        self.assertNotIn("X-Requested-With", kwargs["headers"])

        prepared = requests.Request(
            "GET", session.calls[0][1], params=kwargs["params"], headers=kwargs["headers"]
        ).prepare()
        self.assertEqual(
            list(dict(pair.split("=", 1) for pair in prepared.url.split("?", 1)[1].split("&")).keys()),
            list(kwargs["params"]),
        )

    def test_explicit_fingerprint_overrides_session_cookie(self):
        session = Session([Response({"message": "success", "error_code": 0,
                                     "data": {"status": "1"}})])
        session.cookies = [create_cookie("s_v_web_id", "cookie-fingerprint", domain="mp.toutiao.com")]
        passport = TouTiaoPassport(session, fingerprint="explicit-fingerprint")
        passport.check_qrcode("token")
        params = session.calls[0][2]["params"]
        self.assertEqual(params["verifyFp"], "explicit-fingerprint")
        self.assertEqual(params["fp"], "explicit-fingerprint")

    def test_qrcode_and_status_are_pure_http(self):
        image = "data:image/png;base64," + base64.b64encode(b"png").decode()
        session = Session([
            Response({"message": "success", "error_code": 0,
                      "data": {"token": "token", "qrcode": image}}),
            Response({"message": "success", "error_code": 0,
                      "data": {"status": "1"}}),
        ])
        passport = TouTiaoPassport(session)
        challenge = passport.get_qrcode()
        self.assertIsInstance(challenge, QRCodeChallenge)
        self.assertEqual(challenge.token, "token")
        self.assertEqual(passport.check_qrcode(challenge.token)["status"], "1")
        self.assertEqual(session.calls[0][0:2], ("GET", "https://sso.toutiao.com/get_qrcode/"))
        self.assertEqual(session.calls[0][2]["params"]["service"], passport.service)
        self.assertEqual(session.calls[1][2]["params"]["token"], "token")

    def test_qrcode_poll_follows_only_toutiao_redirect(self):
        session = Session([
            Response({"message": "success", "error_code": 0,
                      "data": {"status": "3", "redirect_url": "https://mp.toutiao.com/profile_v4/"}}),
            Response({"status_code": 0, "message": "callback success"}),
        ])
        passport = TouTiaoPassport(session)
        challenge = QRCodeChallenge("token", "data:image/png;base64,cG5n", passport.service, {})
        with patch("builder.passport.time.sleep"):
            self.assertEqual(passport.wait_qrcode(challenge, timeout_seconds=1)["status"], "3")
        self.assertEqual(session.calls[1][1], "https://mp.toutiao.com/profile_v4/")

    def test_auth_qrcode_flow_extracts_session_cookies_by_domain(self):
        image = "data:image/png;base64," + base64.b64encode(b"png").decode()
        session = Session([
            Response({"message": "success", "error_code": 0,
                      "data": {"token": "token", "qrcode": image}}),
            Response({"message": "success", "error_code": 0,
                      "data": {"status": "3",
                               "redirect_url": "https://mp.toutiao.com/profile_v4/"}}),
            Response({"status_code": 0}),
        ])
        session.cookies = [
            create_cookie("creator_sid", "private", domain="mp.toutiao.com"),
            create_cookie("shared_sid", "shared", domain=".toutiao.com"),
        ]
        shown = []
        with patch("builder.passport.time.sleep"):
            auth = TouTiaoAuth.from_qrcode_login(
                timeout_seconds=1, session=session, on_qrcode=shown.append
            )
        self.assertEqual(len(shown), 1)
        self.assertEqual(auth.creator_cookie, {"creator_sid": "private"})
        self.assertEqual(auth.cookie, {"shared_sid": "shared"})
        self.assertTrue(auth.creator_login_verified)

    def test_sms_wire_contract_uses_sso_paths_and_form_body(self):
        session = Session([
            Response({"message": "success", "error_code": 0, "data": {"retry_time": 60}}),
            Response({"message": "success", "error_code": 0, "data": {"redirect_url": "https://mp.toutiao.com/profile_v4/"}}),
        ])
        passport = TouTiaoPassport(session)
        self.assertEqual(passport.send_sms_code("+8613800138000")["retry_time"], 60)
        self.assertIn("mobile", session.calls[0][2]["data"])
        self.assertEqual(session.calls[0][1], "https://sso.toutiao.com/send_activation_code/v2/")
        passport.sms_login("+8613800138000", "1234")
        self.assertEqual(session.calls[1][1], "https://sso.toutiao.com/quick_login/v2/")
        self.assertEqual(session.calls[1][2]["data"]["service"], passport.service)

    def test_challenge_is_exposed_without_attempting_to_bypass(self):
        session = Session([Response({
            "message": "error", "error_code": 2046,
            "data": {"verify_center_decision_conf": "challenge"},
            "description": "verification required",
        })])
        with self.assertRaises(CaptchaRequired) as caught:
            TouTiaoPassport(session).send_sms_code("+8613800138000")
        self.assertEqual(caught.exception.code, 2046)
        self.assertEqual(caught.exception.challenge["verify_center_decision_conf"], "challenge")

    def test_invalid_input_is_rejected_before_network(self):
        session = Session([])
        passport = TouTiaoPassport(session)
        for mobile in ("", "abc", "123"):
            with self.subTest(mobile=mobile), self.assertRaises(ValueError):
                passport.send_sms_code(mobile)
        with self.assertRaises(ValueError):
            passport.sms_login("+8613800138000", "x")
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
