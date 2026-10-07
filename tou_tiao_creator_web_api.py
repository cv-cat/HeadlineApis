"""头条号网页会话的已观察只读接口；与 OAuth Creator Open API 分开。"""

from __future__ import annotations

from builder.auth import TouTiaoAuth


class CreatorWebApiError(RuntimeError):
    """只保留平台错误码，避免异常包含账号资料或 Cookie。"""

    def __init__(self, code: object):
        self.code = int(code) if str(code).isdigit() else "unknown"
        super().__init__(f"Toutiao Creator Web API code={self.code}")


class TouTiaoCreatorWebApi:
    """使用 ``mp.toutiao.com`` Cookie 的只读账号状态和草稿列表。"""

    BASE_URL = "https://mp.toutiao.com"

    def __init__(self, auth: TouTiaoAuth):
        if not auth.creator_cookie:
            raise ValueError("Creator web Cookie is required")
        self.auth = auth

    def _get(self, path: str, *, params: dict | None = None) -> dict:
        response = self.auth.session.get(
            f"{self.BASE_URL}{path}",
            params=params,
            cookies=self.auth.creator_cookie,
            headers={"Accept": "application/json, text/plain, */*",
                     "Referer": f"{self.BASE_URL}/profile_v4/"},
            timeout=30,
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise CreatorWebApiError(response.status_code)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Creator web response must be a JSON object")
        code = payload.get("code")
        if str(code) != "0":
            raise CreatorWebApiError(code)
        return payload

    def is_logged_in(self) -> bool:
        """GET 网页登录状态；只返回 ``data.is_login``。"""
        payload = self._get("/mp/agw/media/user_login_status_api")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("is_login"), bool):
            raise ValueError("Creator login status has no is_login boolean")
        return data["is_login"]

    def has_account_auth(self) -> bool:
        """GET 账号资料授权标志；不是最终发布许可。"""
        payload = self._get("/mp/agw/media/check_user_auth")
        if not isinstance(payload.get("has_auth"), bool):
            raise ValueError("Creator account status has no has_auth boolean")
        return payload["has_auth"]

    def list_drafts(self, *, count: int = 20) -> list[dict]:
        """读取最近的草稿。已观察 ``type=0``，不推测其他分类。"""
        if type(count) is not int or not 1 <= count <= 20:
            raise ValueError("count must be an integer from 1 to 20")
        payload = self._get(
            "/mp/agw/creator_center/draft_list",
            params={"type": 0, "count": count},
        )
        drafts = payload.get("draft_list")
        if not isinstance(drafts, list) or not all(isinstance(item, dict) for item in drafts):
            raise ValueError("Creator draft_list must be an array of objects")
        return drafts
