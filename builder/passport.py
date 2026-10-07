"""今日头条 SSO 网页登录的纯 HTTP 客户端。

头条创作者登录页使用 ``sso.toutiao.com`` 的公开 JSON 接口。此模块只
复现已观察到的请求、轮询和响应归一化，不启动浏览器，也不处理或绕过
滑块验证。遇到平台返回的人机校验挑战时，调用方应让账号持有人按官方
流程完成挑战后，再把平台给出的挑战结果交回请求。
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urljoin, urlsplit

import requests


class PassportError(RuntimeError):
    """SSO 接口返回了非成功业务码。"""

    def __init__(self, code: object, description: str = "", *, challenge: object = None):
        self.code = int(code) if str(code).isdigit() else "unknown"
        self.description = description if isinstance(description, str) else ""
        # challenge 只在内存中提供给调用方；异常字符串不带出响应正文或 Cookie。
        self.challenge = challenge
        suffix = f": {self.description[:120]}" if self.description else ""
        super().__init__(f"Toutiao SSO error_code={self.code}{suffix}")


class CaptchaRequired(PassportError):
    """请求被人机校验拦截，需要账号持有人完成官方挑战。"""


@dataclass(frozen=True)
class QRCodeChallenge:
    """可交给终端、GUI 或二维码扫描器的二维码数据。"""

    token: str
    image_data_uri: str
    service: str
    raw: Mapping[str, object]

    def save(self, path: str) -> None:
        """把二维码 PNG 保存到调用方指定路径，不保存会话 Cookie。"""
        prefix = "data:image/png;base64,"
        if not self.image_data_uri.startswith(prefix):
            raise ValueError("SSO QR code is not a PNG data URI")
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(self.image_data_uri[len(prefix):], validate=True))


def _safe_description(payload: Mapping[str, object]) -> str:
    description = payload.get("description")
    if isinstance(description, str):
        # 错误描述可能含账号输入或风控链接；只保留短的人类提示。
        return re.sub(r"https?://\S+", "[link]", description)[:160]
    return ""


class TouTiaoPassport:
    """头条 SSO 纯 HTTP 登录协议。"""

    BASE_URL = "https://sso.toutiao.com"
    AID = 1231
    UI_VERSION = "3.3.2"
    SDK_VERSION = "2.2.6"
    LANGUAGE = "zh"
    LOGIN_SERVICE = "https://mp.toutiao.com/profile_v4/"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/154.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        service: str = LOGIN_SERVICE,
        fingerprint: str = "",
        csrf_token: str = "",
        ui_version: str = UI_VERSION,
        language: str = LANGUAGE,
        user_agent: str | None = None,
    ):
        if not isinstance(service, str) or not service:
            raise ValueError("service is required")
        parsed = urlsplit(service)
        if parsed.scheme != "https" or parsed.hostname != "mp.toutiao.com":
            raise ValueError("service must be an HTTPS mp.toutiao.com URL")
        self.session = session or requests.Session()
        self.service = service
        self.fingerprint = fingerprint
        self.csrf_token = csrf_token
        self.ui_version = ui_version
        self.language = language
        self.user_agent = user_agent or self.USER_AGENT
        self._owns_session = session is None

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def __enter__(self) -> "TouTiaoPassport":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _cookie(self, *names: str, preferred_domain: str = "") -> str:
        """Read a value from either a requests CookieJar or a test mapping.

        The login SDK obtains ``s_v_web_id`` and the CSRF token from cookies at
        request time.  Looking them up lazily is important because the first
        QR request sets ``passport_csrf_token`` on the same session.
        """
        wanted = {name for name in names}
        cookies = getattr(self.session, "cookies", ())
        records: list[tuple[str, str, str, int]] = []

        # RequestsCookieJar is also a Mapping, but its mapping view loses the
        # domain when the same cookie name exists on multiple hosts.  Iterate
        # the Cookie objects first so mp.toutiao.com wins for s_v_web_id.
        if isinstance(cookies, Mapping) and not hasattr(cookies, "_cookies"):
            for index, (name, value) in enumerate(cookies.items()):
                if name in wanted:
                    records.append((name, str(value), "", index))
        else:
            try:
                iterator = iter(cookies)
            except TypeError:
                iterator = iter(())
            for index, item in enumerate(iterator):
                name = getattr(item, "name", None)
                value = getattr(item, "value", None)
                domain = getattr(item, "domain", "") or ""
                if name in wanted and value is not None:
                    records.append((str(name), str(value), str(domain), index))

        if not records:
            return ""
        preferred = preferred_domain.lower().lstrip(".")

        def rank(record: tuple[str, str, str, int]) -> tuple[int, int]:
            domain = record[2].lower().lstrip(".")
            if preferred and (domain == preferred or domain.endswith("." + preferred)):
                return (0, record[3])
            return (1, record[3])

        records.sort(key=rank)
        return records[0][1]

    def _fingerprint(self) -> str:
        """Return the SDK FP, preferring an explicit value then ``s_v_web_id``.

        ``login0.js`` shows that the SDK's FP provider returns this cookie
        verbatim; it does not hash or re-encode it.  An empty value is retained
        when a fresh requests session has not received the cookie yet.
        """
        if self.fingerprint:
            return self.fingerprint
        return self._cookie("s_v_web_id", preferred_domain="mp.toutiao.com")

    def _csrf(self) -> str:
        if self.csrf_token:
            return self.csrf_token
        return self._cookie("passport_csrf_token", "passport_csrf_token_default")

    @property
    def _headers(self) -> dict[str, str]:
        # These are the SDK/browser request headers observed on the SSO XHR.
        # requests supplies Cookie and Accept-Encoding from the session.  The
        # Sec-CH/Sec-Fetch values are stable for the captured desktop profile;
        # callers can override the UA when using another browser profile.
        return {
            "Accept": "application/json, text/javascript",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cache-Control": "no-cache",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://mp.toutiao.com",
            "Pragma": "no-cache",
            "Priority": "u=1, i",
            "Referer": "https://mp.toutiao.com/",
            "sec-ch-ua": '"Chromium";v="154", "Google Chrome";v="154", "Not A(Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": self.user_agent,
            "x-tt-passport-csrf-token": self._csrf(),
        }

    def _common_params(self) -> dict[str, str | int]:
        fingerprint = self._fingerprint()
        # Keep insertion order identical to SsoInterfaceSdk.  The browser
        # sends service/operation fields first, followed by this common block.
        return {
            "ui_version": self.ui_version,
            "aid": self.AID,
            "account_sdk_source": "sso",
            "sdk_version": self.SDK_VERSION,
            "language": self.language,
            "verifyFp": fingerprint,
            "fp": fingerprint,
        }

    @staticmethod
    def _payload(response: requests.Response) -> dict:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Toutiao SSO response must be a JSON object")
        return payload

    @classmethod
    def _unwrap(cls, payload: Mapping[str, object]) -> dict:
        if str(payload.get("message")) == "success" and str(payload.get("error_code", 0)) == "0":
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError("Toutiao SSO success response has no data object")
            return data
        data = payload.get("data")
        nested_code = data.get("error_code") if isinstance(data, dict) else None
        code = payload.get("error_code", nested_code if nested_code is not None else "unknown")
        challenge = data if isinstance(data, dict) and (
            "verify_center_decision_conf" in data
            or "verify_center_secondary_decision_conf" in data
            or "sms_code_key" in data
            or "requestData" in data
            or "request_data" in data
            or any(key in data for key in ("captcha", "captcha_code", "risk_control"))
        ) else None
        if challenge is not None:
            raise CaptchaRequired(code, _safe_description(payload), challenge=challenge)
        raise PassportError(code, _safe_description(payload))

    def _get(self, path: str, *, params: Mapping[str, object] | None = None) -> dict:
        # JS object spread puts operation parameters before the SDK common
        # parameters.  This also preserves duplicate-free wire order in
        # requests' prepared URL.
        query: dict[str, object] = {}
        if params:
            query.update(params)
        query.update(self._common_params())
        return self._unwrap(self._payload(self.session.get(
            f"{self.BASE_URL}{path}",
            params=query,
            headers=self._headers,
            timeout=30,
        )))

    def _post(self, path: str, data: Mapping[str, object]) -> dict:
        # Axios serializes the SSO SDK body as form data and places the common
        # fields in the query string. Keep that split so curl and requests
        # produce the same wire shape.
        return self._unwrap(self._payload(self.session.post(
            f"{self.BASE_URL}{path}",
            params=self._common_params(),
            data=dict(data),
            headers=self._headers,
            timeout=30,
        )))

    def get_qrcode(self, *, need_logo: bool = True) -> QRCodeChallenge:
        data = self._get("/get_qrcode/", params={
            "service": self.service,
            "need_logo": "true" if need_logo else "false",
        })
        token = data.get("token")
        image = data.get("qrcode")
        if not isinstance(token, str) or not token:
            raise ValueError("SSO QR response has no token")
        if not isinstance(image, str) or not image.startswith("data:image/"):
            raise ValueError("SSO QR response has no image data URI")
        return QRCodeChallenge(token, image, self.service, data)

    def check_qrcode(self, token: str, *, need_logo: bool = True) -> dict:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("token is required")
        return self._get("/check_qrconnect/", params={
            "service": self.service,
            "token": token,
            "need_logo": "true" if need_logo else "false",
        })

    def wait_qrcode(
        self,
        challenge: QRCodeChallenge,
        *,
        timeout_seconds: int = 600,
        poll_interval: float = 1.0,
        on_status: Callable[[dict], None] | None = None,
    ) -> dict:
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive integer")
        if not isinstance(poll_interval, (int, float)) or poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self.check_qrcode(challenge.token)
            if on_status:
                on_status(status)
            state = str(status.get("status", ""))
            if state in {"3", "confirmed"}:
                redirect_url = status.get("redirect_url")
                if redirect_url:
                    self._follow_redirect(redirect_url)
                return status
            if state in {"4", "5", "expired"}:
                raise PassportError("expired", "二维码已失效，请重新获取")
            time.sleep(min(float(poll_interval), max(0.01, deadline - time.monotonic())))
        raise TimeoutError("Toutiao QR login timed out")

    def _follow_redirect(self, redirect_url: object) -> None:
        if not isinstance(redirect_url, str):
            return
        parsed = urlsplit(redirect_url)
        if parsed.scheme != "https" or parsed.hostname not in {"mp.toutiao.com", "sso.toutiao.com"}:
            raise ValueError("SSO redirect must remain on Toutiao login hosts")
        current = redirect_url
        for _ in range(6):
            response = self.session.get(
                current,
                headers=self._headers,
                timeout=30,
                allow_redirects=False,
            )
            status = getattr(response, "status_code", 200)
            if 300 <= status < 400:
                location = response.headers.get("Location")
                if not location:
                    raise PassportError(status, "SSO redirect has no location")
                # urljoin handles relative locations while the host check below
                # keeps the session inside the two observed login hosts.
                current = urljoin(current, location)
                next_parts = urlsplit(current)
                if next_parts.scheme != "https" or next_parts.hostname not in {
                    "mp.toutiao.com", "sso.toutiao.com"
                }:
                    raise ValueError("SSO redirect must remain on Toutiao login hosts")
                continue
            response.raise_for_status()
            return
        raise PassportError("redirect", "SSO redirect chain is too long")

    def send_sms_code(self, mobile: str, *, extra_params: Mapping[str, object] | None = None) -> dict:
        mobile = str(mobile).strip()
        if not re.fullmatch(r"\+?\d{6,15}", mobile):
            raise ValueError("mobile must be an international phone number")
        data: dict[str, object] = {"mobile": mobile, "type": 24}
        if extra_params:
            data.update(extra_params)
        return self._post("/send_activation_code/v2/", data)

    def sms_login(
        self,
        mobile: str,
        code: str,
        *,
        extra_params: Mapping[str, object] | None = None,
    ) -> dict:
        mobile = str(mobile).strip()
        code = str(code).strip()
        if not re.fullmatch(r"\+?\d{6,15}", mobile):
            raise ValueError("mobile must be an international phone number")
        if not re.fullmatch(r"\d{4,8}", code):
            raise ValueError("code must contain 4 to 8 digits")
        data: dict[str, object] = {"mobile": mobile, "code": code, "service": self.service}
        if extra_params:
            data.update(extra_params)
        return self._post("/quick_login/v2/", data)
