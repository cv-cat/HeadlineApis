"""今日头条 Cookie 会话与头条开放平台 OAuth 会话。"""

from __future__ import annotations

import hmac
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from utils.tou_tiao_utils import trans_cookies


class OpenApiError(RuntimeError):
    """开放平台返回的错误。异常只包含错误码，避免带出凭据。"""

    def __init__(self, code: object):
        self.code = code
        super().__init__(f"Toutiao Open API error_code={code}")


def read_open_api_data(response: requests.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Toutiao Open API response must be a JSON object")
    data = payload.get("data")
    extra = payload.get("extra") or {}
    if not isinstance(extra, dict):
        raise ValueError("Toutiao Open API response has invalid extra data")
    for source in (data, extra):
        if isinstance(source, dict):
            code = source.get("error_code", 0)
            if str(code) != "0":
                raise OpenApiError(code)
    if not isinstance(data, dict):
        raise ValueError("Toutiao Open API response has no data object")
    return data


def _expiry_seconds(value: object) -> int | None:
    if value is None or value == "":
        return None
    seconds = int(value)
    if seconds < 0:
        raise ValueError("OAuth expiry must be non-negative")
    return seconds


class TouTiaoAuth:
    """网页 Cookie 与 Creator Open API token 各自独立。"""

    def __init__(
        self,
        cookie_str: str = "",
        *,
        access_token: str = "",
        open_id: str = "",
        refresh_token: str = "",
        expires_in: int | str | None = None,
        refresh_expires_in: int | str | None = None,
        scope: str = "",
        session: requests.Session | None = None,
    ):
        self.cookie: dict[str, str] = {}
        self.cookie_str = ""
        self.creator_cookie: dict[str, str] = {}
        self.creator_cookie_str = ""
        self.creator_login_verified = False
        self.access_token = access_token
        self.open_id = open_id
        self.refresh_token = refresh_token
        self.expires_in = _expiry_seconds(expires_in)
        self.refresh_expires_in = _expiry_seconds(refresh_expires_in)
        self.scope = scope
        issued_at = time.time()
        self.expires_at = issued_at + self.expires_in if self.expires_in is not None else None
        self.refresh_expires_at = (
            issued_at + self.refresh_expires_in
            if self.refresh_expires_in is not None else None
        )
        self.session = session or requests.Session()
        self._owns_session = session is None
        if cookie_str:
            self.prepare_auth(cookie_str)

    @classmethod
    def from_cookie(cls, cookie_str: str, **kwargs) -> "TouTiaoAuth":
        if not cookie_str or not cookie_str.strip():
            raise ValueError("cookie_str is required")
        return cls(cookie_str, **kwargs)

    @classmethod
    def from_qrcode_login(
        cls,
        timeout_seconds: int = 600,
        *,
        poll_interval: float = 1.0,
        service: str = "https://mp.toutiao.com/profile_v4/",
        on_qrcode=None,
        session: requests.Session | None = None,
        fingerprint: str = "",
        csrf_token: str = "",
        user_agent: str | None = None,
    ) -> "TouTiaoAuth":
        """用纯 HTTP 获取二维码并轮询登录状态。

        ``on_qrcode`` 接收一个 ``QRCodeChallenge``。调用方自行把其
        ``image_data_uri`` 展示给本人扫码；本库不启动浏览器、不读取浏览器
        配置，也不自动处理滑块等人机验证。
        """
        from builder.passport import TouTiaoPassport

        auth = cls(session=session)
        passport = TouTiaoPassport(
            auth.session,
            service=service,
            fingerprint=fingerprint,
            csrf_token=csrf_token,
            user_agent=user_agent,
        )
        try:
            challenge = passport.get_qrcode()
            if on_qrcode is not None:
                on_qrcode(challenge)
            else:
                # 不打印二维码内容本身，避免把大段 base64 淹没终端；调用方
                # 可以通过返回对象的 on_qrcode 回调或 QRCodeChallenge.save() 展示。
                print("已获取头条二维码，请用 on_qrcode 回调展示后扫码。")
            passport.wait_qrcode(
                challenge,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
            auth._prepare_creator_cookies_from_session(verified=True)
            if not auth.creator_cookie:
                raise RuntimeError("Toutiao SSO confirmed but no creator Cookie was set")
            return auth
        except Exception:
            auth.close()
            raise
        finally:
            passport.close()

    @classmethod
    def from_browser_login(cls, timeout_seconds: int = 600, **kwargs) -> "TouTiaoAuth":
        """兼容旧名称；实现为纯 HTTP 二维码轮询，不启动浏览器。"""
        return cls.from_qrcode_login(timeout_seconds=timeout_seconds, **kwargs)

    @classmethod
    def from_sms_login(
        cls,
        mobile: str,
        code: str,
        *,
        service: str = "https://mp.toutiao.com/profile_v4/",
        session: requests.Session | None = None,
        extra_params: dict | None = None,
        fingerprint: str = "",
        csrf_token: str = "",
        user_agent: str | None = None,
    ) -> "TouTiaoAuth":
        """用调用方已取得的短信验证码完成纯 HTTP 登录。"""
        from builder.passport import TouTiaoPassport

        auth = cls(session=session)
        passport = TouTiaoPassport(
            auth.session,
            service=service,
            fingerprint=fingerprint,
            csrf_token=csrf_token,
            user_agent=user_agent,
        )
        try:
            passport.sms_login(mobile, code, extra_params=extra_params)
            auth._prepare_creator_cookies_from_session(verified=True)
            if not auth.creator_cookie:
                raise RuntimeError("Toutiao SSO confirmed but no creator Cookie was set")
            return auth
        except Exception:
            auth.close()
            raise
        finally:
            passport.close()

    @classmethod
    def from_access_token(
        cls, access_token: str, open_id: str, **kwargs
    ) -> "TouTiaoAuth":
        if not access_token or not open_id:
            raise ValueError("access_token and open_id are required")
        return cls(access_token=access_token, open_id=open_id, **kwargs)

    def prepare_auth(self, cookie_str: str) -> "TouTiaoAuth":
        self.cookie = trans_cookies(cookie_str)
        self.cookie_str = cookie_str
        return self

    def prepare_creator_auth(self, cookie_str: str, *, verified: bool = False) -> "TouTiaoAuth":
        """保存 mp.toutiao.com 的 Cookie；不把它当作 Open API 授权。"""
        self.creator_cookie = trans_cookies(cookie_str)
        self.creator_cookie_str = cookie_str
        self.creator_login_verified = verified
        return self

    def _prepare_creator_cookies_from_session(self, *, verified: bool = False) -> None:
        """按 Cookie 域从 SSO requests 会话提取创作者与共享 Cookie。"""
        creator: dict[str, str] = {}
        shared: dict[str, str] = {}
        for item in getattr(self.session, "cookies", ()):
            domain = (item.domain or "").lower()
            if domain == "mp.toutiao.com" or domain.endswith(".mp.toutiao.com"):
                creator[item.name] = item.value
            if domain == "toutiao.com" or domain == ".toutiao.com":
                shared[item.name] = item.value

        def serialize(values: dict[str, str]) -> str:
            return "; ".join(f"{name}={value}" for name, value in values.items())
        if creator:
            self.prepare_creator_auth(serialize(creator), verified=verified)
        if shared:
            self.prepare_auth(serialize(shared))

    # 原仓库公开方法拼写有误；保留它供旧调用方使用。
    def perepare_auth(self, cookie_str: str) -> "TouTiaoAuth":
        return self.prepare_auth(cookie_str)

    def require_open_api(self) -> None:
        if not self.access_token or not self.open_id:
            raise ValueError("Creator API requires an OAuth access_token and open_id")

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def __enter__(self) -> "TouTiaoAuth":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class TouTiaoOAuth:
    """头条帐号授权码流程。授权页须交由用户打开并确认。"""

    AUTH_BASE = "https://open.snssdk.com"

    @staticmethod
    def _validate_redirect_uri(redirect_uri: str) -> None:
        parsed = urlsplit(redirect_uri)
        if (parsed.scheme not in ("http", "https") or not parsed.netloc
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError("redirect_uri must be an HTTP(S) URL without credentials or fragment")

    @staticmethod
    def authorize_url(
        client_key: str, redirect_uri: str, scopes: list[str], state: str
    ) -> str:
        if not all((client_key, redirect_uri, scopes, state)):
            raise ValueError("client_key, redirect_uri, scopes and state are required")
        TouTiaoOAuth._validate_redirect_uri(redirect_uri)
        if isinstance(scopes, (str, bytes)) or any(
            not isinstance(scope, str) or not scope.strip() for scope in scopes
        ):
            raise ValueError("scopes must contain non-empty strings")
        query = urlencode(
            {
                "client_key": client_key,
                "response_type": "code",
                "scope": ",".join(scopes),
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return f"{TouTiaoOAuth.AUTH_BASE}/oauth/authorize/?{query}"

    @staticmethod
    def code_from_callback(
        callback_url: str, *, redirect_uri: str, expected_state: str
    ) -> str:
        """校验回调地址与 state 后提取一次性 code，不在错误中输出 code。"""
        if not expected_state:
            raise ValueError("expected_state is required")
        TouTiaoOAuth._validate_redirect_uri(redirect_uri)
        expected = urlsplit(redirect_uri)
        actual = urlsplit(callback_url)
        if actual.username or actual.password or actual.fragment or (
            actual.scheme.lower(), actual.hostname, actual.port, actual.path
        ) != (
            expected.scheme.lower(), expected.hostname, expected.port, expected.path
        ):
            raise ValueError("OAuth callback URL does not match redirect_uri")
        query = parse_qs(actual.query, keep_blank_values=True)
        for key, values in parse_qs(expected.query, keep_blank_values=True).items():
            if query.get(key) != values:
                raise ValueError("OAuth callback URL does not match redirect_uri")
        states = query.get("state", [])
        if len(states) != 1 or not hmac.compare_digest(states[0], expected_state):
            raise ValueError("OAuth callback state mismatch")
        if "error" in query:
            raise ValueError("OAuth authorization was not granted")
        codes = query.get("code", [])
        if len(codes) != 1 or not codes[0]:
            raise ValueError("OAuth callback has no single code")
        return codes[0]

    @classmethod
    def exchange_code(
        cls,
        client_key: str,
        client_secret: str,
        code: str,
        *,
        session: requests.Session | None = None,
    ) -> TouTiaoAuth:
        if not all((client_key, client_secret, code)):
            raise ValueError("client_key, client_secret and code are required")
        http = session or requests.Session()
        owned = session is None
        try:
            response = http.post(
                f"{cls.AUTH_BASE}/oauth/access_token/",
                data={
                    "client_key": client_key,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
                timeout=30,
            )
            data = read_open_api_data(response)
            auth = TouTiaoAuth.from_access_token(
                data["access_token"],
                data["open_id"],
                refresh_token=data.get("refresh_token") or "",
                expires_in=data.get("expires_in"),
                refresh_expires_in=data.get("refresh_expires_in"),
                scope=data.get("scope") or "",
                session=http,
            )
            auth._owns_session = owned
            return auth
        except Exception:
            if owned:
                http.close()
            raise

    @classmethod
    def refresh_access_token(cls, client_key: str, auth: TouTiaoAuth) -> TouTiaoAuth:
        """用头条 refresh_token 续期 access_token，并更新原会话。

        头条不支持续期 refresh_token；它失效后需要重新取得用户授权。
        """
        if not client_key or not auth.refresh_token:
            raise ValueError("client_key and refresh_token are required")
        response = auth.session.post(
            f"{cls.AUTH_BASE}/oauth/refresh_token/",
            files={
                "client_key": (None, client_key),
                "grant_type": (None, "refresh_token"),
                "refresh_token": (None, auth.refresh_token),
            },
            timeout=30,
        )
        data = read_open_api_data(response)
        access_token = data.get("access_token")
        open_id = data.get("open_id", auth.open_id)
        if not access_token or open_id != auth.open_id:
            raise ValueError("OAuth refresh returned no access_token or a different open_id")
        expires_in = _expiry_seconds(data.get("expires_in"))
        refresh_expires_in = _expiry_seconds(data.get("refresh_expires_in"))
        refreshed_at = time.time()
        auth.access_token = access_token
        auth.refresh_token = data.get("refresh_token") or auth.refresh_token
        auth.scope = data.get("scope") or auth.scope
        auth.expires_in = expires_in
        auth.expires_at = refreshed_at + expires_in if expires_in is not None else None
        if refresh_expires_in is not None:
            auth.refresh_expires_in = refresh_expires_in
            reported_expiry = refreshed_at + refresh_expires_in
            auth.refresh_expires_at = (
                min(auth.refresh_expires_at, reported_expiry)
                if auth.refresh_expires_at is not None else reported_expiry
            )
        return auth
