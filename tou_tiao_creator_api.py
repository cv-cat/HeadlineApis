"""今日头条创作者 Open API：OAuth 视频上传、发布与作品查询。"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Mapping

from builder.auth import TouTiaoAuth, read_open_api_data


class TouTiaoCreatorApi:
    """头条官方开放平台客户端。

    需要已经授权 ``toutiao.video.create`` / ``toutiao.video.data`` 的
    ``TouTiaoAuth``。上传与发布分开调用；上传本身不会公开作品。
    """

    BASE_URL = "https://open.douyin.com"
    MAX_SINGLE_UPLOAD_BYTES = 128 * 1024 * 1024

    def __init__(self, auth: TouTiaoAuth):
        auth.require_open_api()
        self.auth = auth

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
    ) -> dict:
        query = {"open_id": self.auth.open_id}
        if params:
            query.update(params)
        response = self.auth.session.request(
            method,
            f"{self.BASE_URL}{path}",
            params=query,
            headers={"access-token": self.auth.access_token},
            json=json,
            files=files,
            timeout=60 if files else 30,
        )
        return read_open_api_data(response)

    def upload_video(self, path: str | Path) -> dict:
        """单文件上传，返回含 ``video.video_id`` 的原始 data 对象。

        官方文档要求单文件不超过 128 MiB；更大文件应走分片上传。
        """
        video_path = Path(path)
        if not video_path.is_file():
            raise FileNotFoundError(video_path)
        if video_path.stat().st_size > self.MAX_SINGLE_UPLOAD_BYTES:
            raise ValueError("video exceeds the 128 MiB single-upload limit")
        mime = mimetypes.guess_type(video_path.name)[0] or "application/octet-stream"
        with video_path.open("rb") as video:
            return self._request(
                "POST",
                "/toutiao/video/upload/",
                files={"video": (video_path.name, video, mime)},
            )

    def publish_video(
        self,
        video_id: str,
        text: str,
        *,
        options: Mapping[str, Any] | None = None,
    ) -> dict:
        """提交视频审核，返回含 ``item_id`` 的原始 data 对象。

        官方文档证实端点，但当前页面未给出完整请求字段；
        ``video_id`` / ``text`` 基于相邻平台的开放接口约定，待帐号实测。
        """
        if not video_id or not text:
            raise ValueError("video_id and text are required")
        payload = {"video_id": video_id, "text": text}
        if options:
            if {"video_id", "text"} & options.keys():
                raise ValueError("options cannot override video_id or text")
            payload.update(options)
        return self._request("POST", "/toutiao/video/create/", json=payload)

    def list_videos(self, *, cursor: int = 0, count: int = 10) -> dict:
        """分页获取已授权帐号的视频列表。"""
        if cursor < 0 or count < 1:
            raise ValueError("cursor must be >= 0 and count must be positive")
        return self._request(
            "GET", "/toutiao/video/list/", params={"cursor": cursor, "count": count}
        )

    def get_video_data(self, payload: Mapping[str, Any]) -> dict:
        """转发调用方已确认的查询体，返回作品数据。

        官方文档证实端点，但当前页面没有公开请求体字段；因此不推测
        item_id 到请求字段的映射。
        """
        if not payload:
            raise ValueError("payload is required")
        return self._request("POST", "/toutiao/video/data/", json=dict(payload))

    def publish_article(self, *_args, **_kwargs) -> None:
        raise NotImplementedError(
            "官方头条内容发布 Open API 暂不支持文章；需另行验证创作者网页接口"
        )
