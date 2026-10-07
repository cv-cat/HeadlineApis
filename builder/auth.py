"""今日头条 Cookie 会话与头条开放平台 OAuth 会话。"""

from __future__ import annotations

from urllib.parse import urlencode

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
    if not isinstance(data, dict):
        raise ValueError("Toutiao Open API response has no data object")
    extra = payload.get("extra") or {}
    code = data.get("error_code", extra.get("error_code", 0))
    if str(code) != "0":
        raise OpenApiError(code)
    extra_code = extra.get("error_code", 0)
    if str(extra_code) != "0":
        raise OpenApiError(extra_code)
    return data


class TouTiaoAuth:
    """兼容旧 ``auth.cookie``，并承载 Creator Open API 的 token。"""

    def __init__(
        self,
        cookie_str: str = "",
        *,
        access_token: str = "",
        open_id: str = "",
        session: requests.Session | None = None,
    ):
        self.cookie: dict[str, str] = {}
        self.cookie_str = ""
        self.access_token = access_token
        self.open_id = open_id
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
    def authorize_url(
        client_key: str, redirect_uri: str, scopes: list[str], state: str
    ) -> str:
        if not all((client_key, redirect_uri, scopes, state)):
            raise ValueError("client_key, redirect_uri, scopes and state are required")
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
                data["access_token"], data["open_id"], session=http
            )
            auth._owns_session = owned
            return auth
        except Exception:
            if owned:
                http.close()
            raise
