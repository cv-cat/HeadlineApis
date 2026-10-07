"""头条号网页会话的已观察接口；与 OAuth Creator Open API 分开。"""

from __future__ import annotations

from builder.auth import TouTiaoAuth


class CreatorWebApiError(RuntimeError):
    """只保留平台错误码，避免异常包含账号资料或 Cookie。"""

    def __init__(self, code: object):
        self.code = int(code) if str(code).isdigit() else "unknown"
        super().__init__(f"Toutiao Creator Web API code={self.code}")


class TouTiaoCreatorWebApi:
    """使用 ``mp.toutiao.com`` Cookie 读取状态、草稿并显式删除指定草稿。"""

    BASE_URL = "https://mp.toutiao.com"

    def __init__(self, auth: TouTiaoAuth):
        if not auth.creator_cookie:
            raise ValueError("Creator web Cookie is required")
        self.auth = auth

    @staticmethod
    def _check_response(response) -> dict:
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
        return self._check_response(response)

    def is_logged_in(self) -> bool:
        """GET 网页登录状态；只返回 ``data.is_login``。"""
        payload = self._get("/mp/agw/media/user_login_status_api")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("is_login"), bool):
            raise ValueError("Creator login status has no is_login boolean")
        return data["is_login"]

    def has_account_auth(self) -> bool:
        """GET 账号资料授权标志；非零业务码会抛错，不能据此推断发文许可。"""
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

    def delete_draft(self, gid: str, *, draft_type: int) -> None:
        """显式删除调用方指定的草稿。两个标识均应来自 ``list_drafts`` 的同一条目。"""
        if not isinstance(gid, str) or not gid.strip():
            raise ValueError("gid must be a non-empty string")
        if type(draft_type) is not int or draft_type < 0:
            raise ValueError("draft_type must be a non-negative integer")
        response = self.auth.session.post(
            f"{self.BASE_URL}/mp/agw/creator_center/delete_draft",
            params={"app_id": 1231},
            json={"drafts": [{"draft_type": draft_type, "gid": gid}]},
            cookies=self.auth.creator_cookie,
            headers={"Accept": "application/json, text/plain, */*",
                     "Referer": f"{self.BASE_URL}/profile_v4/manage/draft"},
            timeout=30,
            allow_redirects=False,
        )
        self._check_response(response)
