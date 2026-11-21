"""头条 VOD 视频上传（mp 网页端上传协议，非 OpenAPI）。

2026-11-21 实测闭环（39KB 测试视频，720x1280 / h264 / 3s）：

1. ``GET /toutiao/upload/auth_token/v1/?scene_id=10020002&type=video&aid=1231&device_platform=web``
   返回 VOD STS 凭证（AccessKeyID / SecretAccessKey / SessionToken，24h 有效）。
2. ``GET vod.bytedanceapi.com/?Action=ApplyUploadInner&Version=2020-11-19&...``
   AWS4 签名（GET、query 排序 RFC3986 编码、scope 终结符 ``aws4_request``、
   服务名 ``vod``、区域 ``cn-north-1``），返回 ``StoreInfos[{StoreUri, Auth}]``
   / ``UploadHost`` / ``SessionKey``。
3. ``PUT https://{UploadHost}/{StoreUri}`` 直传文件字节。必带头：
   ``Authorization: {StoreInfos[0].Auth}``、``Content-Type: application/octet-stream``、
   ``Content-Disposition: attachment; filename="<url编码文件名>"``、
   ``Content-CRC32: <crc32 八位小写补零 hex>``。
4. ``GET /mp/agw/article_material/video/get_thumb?vid={vid}`` 轮询封面
   （转码完成前返回 code=1102，约 10 秒内成功），返回 ``items[0]{uri, url}``。
5. ``GET vod.bytedanceapi.com/?Action=CommitUploadInner&Version=2020-11-19&SpaceName=...``
   AWS4 签名 POST，body JSON ``{"SessionKey": ..., "Functions": []}``，
   返回 ``Results[0].Vid`` 与视频元信息（宽高/时长/编码/Md5）。

封面 url 带时效签名参数（``x-expires`` 等），直接使用返回值即可。

本模块只做协议封装，不内嵌 HTTP 栈：调用方注入可用的 ``request`` 可调用
（签名与上传均无浏览器指纹要求，常规 TLS 栈即可通过）。
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import random
import string
import time
import zlib
from collections.abc import Callable, Mapping
from urllib.parse import quote

VOD_HOST = "vod.bytedanceapi.com"
VOD_REGION = "cn-north-1"
VOD_SERVICE = "vod"
VOD_API_VERSION = "2020-11-19"
AUTH_TOKEN_PATH = "/toutiao/upload/auth_token/v1/"
AUTH_TOKEN_QUERY = {"scene_id": "10020002", "type": "video", "aid": "1231", "device_platform": "web"}
GET_THUMB_PATH = "/mp/agw/article_material/video/get_thumb"


class UploadError(RuntimeError):
    """上传链失败；保留平台返回码。"""

    def __init__(self, stage: str, code: object, message: str = ""):
        self.stage = stage
        self.code = code
        text = f"toutiao vod upload [{stage}] code={code}"
        if message:
            text = f"{text}: {message}"
        super().__init__(text)


def crc32_hex(data: bytes) -> str:
    """TOS 校验和：CRC-32 的八位小写十六进制（前补零）。"""
    return format(zlib.crc32(data) & 0xFFFFFFFF, "08x")


def _uri_encode(value: object) -> str:
    return quote(str(value), safe="-_~.")


def _aws4_sign(method: str, params: Mapping[str, object], body: bytes,
               access_key: str, secret_key: str, session_token: str) -> tuple[str, dict[str, str]]:
    """VOD 内部版 AWS4 签名。返回 (canonical_query, 请求头)。"""
    now = datetime.datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_query = "&".join(
        f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(params.items())
    )
    signed = {
        "host": VOD_HOST,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "x-amz-security-token": session_token,
    }
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted(signed.items()))
    signed_header_names = ";".join(sorted(signed))
    canonical_request = (
        f"{method}\n/\n{canonical_query}\n{canonical_headers}\n{signed_header_names}\n{payload_hash}"
    )
    scope = f"{datestamp}/{VOD_REGION}/{VOD_SERVICE}/aws4_request"
    string_to_sign = (
        "AWS4-HMAC-SHA256\n"
        f"{amz_date}\n{scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )

    def _derive(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    key = _derive(("AWS4" + secret_key).encode(), datestamp)
    for part in (VOD_REGION, VOD_SERVICE, "aws4_request"):
        key = _derive(key, part)
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={signed_header_names}, Signature={signature}"
        ),
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "x-amz-security-token": session_token,
    }
    return canonical_query, headers


class TouTiaoVideoUploader:
    """VOD 视频上传五步链（STS → Apply → PUT → Commit → get_thumb）。

    ``request`` 注入 ``request(method, url, *, params=None, data=None,
    json_body=None, headers=None) -> (status_code, parsed_body)``。
    ``mp_request`` 注入同签名，但走 mp.toutiao.com 域（带登录 cookie）。
    """

    def __init__(self, request: Callable, mp_request: Callable):
        if not callable(request) or not callable(mp_request):
            raise TypeError("request and mp_request must be callables")
        self._request = request
        self._mp_request = mp_request

    @staticmethod
    def build_pgc_video_html(vid: str, *, vname: str, vposter: str, thumb_url: str, duration: int) -> str:
        """视频在图文正文中的标记格式：``<p>{!-- PGC_VIDEO:... --}</p>``。"""
        payload = {
            "sp": "toutiao",
            "vid": vid,
            "vu": vid,
            "vname": vname,
            "vposter": vposter,
            "thumb_url": thumb_url,
            "duration": duration,
        }
        marker = "{!-- PGC_VIDEO:" + json.dumps(payload, separators=(",", ":")) + " --}"
        return f"<p>{marker}</p>"

    def fetch_sts(self) -> dict:
        """第 1 步：mp 域 STS 凭证（auth_token/v1）。"""
        status, body = self._mp_request(
            "GET", f"https://mp.toutiao.com{AUTH_TOKEN_PATH}",
            params=AUTH_TOKEN_QUERY, headers={},
        )
        creds = body if isinstance(body, Mapping) else {}
        for key in ("access_key_id", "secret_access_key", "session_token", "space_name"):
            if not creds.get(key):
                raise UploadError("sts", status, str(body)[:120])
        return dict(creds)

    def apply_upload(self, sts: Mapping, file_size: int) -> dict:
        """第 2 步：ApplyUploadInner（AWS4 GET）→ UploadNodes[0]（含 StoreUri/Auth/SessionKey）。"""
        salt = "".join(random.choices(string.digits + string.ascii_lowercase, k=11))
        params = {
            "Action": "ApplyUploadInner",
            "Version": VOD_API_VERSION,
            "SpaceName": sts["space_name"],
            "FileType": "video",
            "IsInner": "1",
            "fileSize": file_size,
            "s": salt,
        }
        canonical_query, headers = _aws4_sign(
            "GET", params, b"", sts["access_key_id"], sts["secret_access_key"], sts["session_token"],
        )
        url = f"https://{VOD_HOST}/?{canonical_query}"
        status, body = self._request("GET", url, headers=headers)
        nodes = ((body or {}).get("Result") or {}).get("InnerUploadAddress", {}).get("UploadNodes")
        if not nodes:
            raise UploadError("apply", status, str(body)[:160])
        return nodes[0]

    def put_object(self, node: Mapping, data: bytes, *, filename: str) -> None:
        """第 3 步：TOS 直传 PUT（octet-stream + Content-CRC32）。"""
        store = node["StoreInfos"][0]
        url = f"https://{node['UploadHost']}/{store['StoreUri']}"
        headers = {
            "Authorization": store["Auth"],
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f'attachment; filename="{quote(filename)}"',
            "Content-CRC32": crc32_hex(data),
        }
        status, body = self._request("PUT", url, data=data, headers=headers)
        if status != 200:
            raise UploadError("put", status, str(body)[:160])

    def commit_upload(self, sts: Mapping, node: Mapping) -> dict:
        """第 4 步：CommitUploadInner（AWS4 POST JSON）→ Results[0]{Vid, VideoMeta}。"""
        params = {
            "Action": "CommitUploadInner",
            "Version": VOD_API_VERSION,
            "SpaceName": sts["space_name"],
        }
        commit_body = json.dumps(
            {"SessionKey": node["SessionKey"], "Functions": []}, separators=(",", ":")
        ).encode()
        canonical_query, headers = _aws4_sign(
            "POST", params, commit_body,
            sts["access_key_id"], sts["secret_access_key"], sts["session_token"],
        )
        headers["Content-Type"] = "application/json"
        url = f"https://{VOD_HOST}/?{canonical_query}"
        status, body = self._request("POST", url, data=commit_body, headers=headers)
        results = ((body or {}).get("Result") or {}).get("Results")
        if not results or not results[0].get("Vid"):
            raise UploadError("commit", status, str(body)[:160])
        return results[0]

    def get_thumb(self, vid: str, *, timeout: float = 60.0, interval: float = 3.0) -> dict:
        """第 5 步：轮询视频封面（转码完成前 code=1102）。返回 items[0]{uri, url}。"""
        deadline = time.time() + timeout
        last_code: object = None
        while time.time() < deadline:
            status, body = self._mp_request(
                "GET", f"https://mp.toutiao.com{GET_THUMB_PATH}",
                params={"vid": vid}, headers={},
            )
            if isinstance(body, Mapping) and body.get("items"):
                return dict(body["items"][0])
            last_code = body.get("code") if isinstance(body, Mapping) else status
            time.sleep(interval)
        raise UploadError("get_thumb", last_code, "timeout waiting video poster")

    def upload(self, data: bytes, *, filename: str) -> dict:
        """一键五步：返回 ``{vid, meta, thumb, pgc_video_html}``。"""
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("data must be non-empty bytes")
        sts = self.fetch_sts()
        node = self.apply_upload(sts, len(data))
        self.put_object(node, bytes(data), filename=filename or "video.mp4")
        result = self.commit_upload(sts, node)
        vid = result["Vid"]
        meta = result.get("VideoMeta") or {}
        thumb = self.get_thumb(vid)
        html = self.build_pgc_video_html(
            vid,
            vname=filename or "video.mp4",
            vposter=thumb.get("url", ""),
            thumb_url=thumb.get("uri", ""),
            duration=int(meta.get("Duration") or 0),
        )
        return {"vid": vid, "meta": meta, "thumb": thumb, "pgc_video_html": html}
