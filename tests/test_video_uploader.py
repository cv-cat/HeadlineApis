import json
import unittest
from urllib.parse import parse_qs

from tou_tiao_video_uploader import (
    TouTiaoVideoUploader,
    UploadError,
    crc32_hex,
)


def _ok_response(body):
    return 200, body


def _make_uploader(data=b"x" * 32):
    """五步链全 mock：STS → Apply → PUT → Commit → get_thumb。"""
    calls = []

    def vod_request(method, url, *, params=None, data=None, json_body=None, headers=None):
        calls.append(("vod", method, url, headers))
        if "auth_token" in url:
            return _ok_response(None)
        return _ok_response({"not": "a vod call"})

    def mp_request(method, url, *, params=None, data=None, json_body=None, headers=None):
        calls.append(("mp", method, url, params))
        return _ok_response(None)

    return vod_request, mp_request, calls


class FakeVOD:
    """按步骤返回预设响应，记录请求。"""

    STS = {
        "access_key_id": "AKTEST",
        "secret_access_key": "SKTEST",
        "session_token": "TOKTEST",
        "space_name": "article_video",
    }
    NODE = {
        "UploadHost": "tos-lf-x.snssdk.com",
        "SessionKey": "sess-key-1",
        "StoreInfos": [{"StoreUri": "tos-cn-v-e5b010/obj1", "Auth": "Bearer auth1"}],
    }
    COMMIT = {
        "Result": {"Results": [{
            "Vid": "v0d3f0", "VideoMeta": {"Duration": 3, "Width": 720, "Height": 1280},
        }]}
    }
    THUMB = {"items": [{"uri": "tos-cn-p-e5b010/th1", "url": "https://p3-sign.toutiaoimg.com/th1"}]}

    def __init__(self):
        self.vod_calls = []
        self.mp_calls = []
        self.put_data = None

    def vod(self, method, url, *, params=None, data=None, json_body=None, headers=None):
        self.vod_calls.append((method, url, params, data, headers))
        if "ApplyUploadInner" in url:
            return 200, {"Result": {"InnerUploadAddress": {"UploadNodes": [self.NODE]}}}
        if "CommitUploadInner" in url:
            return 200, self.COMMIT
        return 200, {}

    def mp(self, method, url, *, params=None, data=None, json_body=None, headers=None):
        self.mp_calls.append((method, url, params))
        if "auth_token" in url:
            return 200, self.STS
        if "get_thumb" in url:
            return 200, self.THUMB
        return 200, {}


class Crc32Tests(unittest.TestCase):
    def test_known_vector(self):
        # CRC-32("123456789") = 0xCBF43926
        self.assertEqual(crc32_hex(b"123456789"), "cbf43926")

    def test_padding_to_eight_hex_digits(self):
        self.assertEqual(len(crc32_hex(b"a")), 8)
        self.assertEqual(crc32_hex(b"\x00" * 4), "2144df1c")


class PgcVideoMarkerTests(unittest.TestCase):
    def test_marker_contains_all_fields(self):
        html = TouTiaoVideoUploader.build_pgc_video_html(
            "v0d3f0", vname="demo.mp4", vposter="https://x/p.jpg",
            thumb_url="tos-cn-p-e5b010/th1", duration=3,
        )
        self.assertTrue(html.startswith("<p>{!-- PGC_VIDEO:{"))
        self.assertTrue(html.endswith("} --}</p>"))
        payload = json.loads(html[len("<p>{!-- PGC_VIDEO:"):-len(" --}</p>")])
        self.assertEqual(payload["sp"], "toutiao")
        self.assertEqual(payload["vid"], "v0d3f0")
        self.assertEqual(payload["vu"], "v0d3f0")
        self.assertEqual(payload["vname"], "demo.mp4")
        self.assertEqual(payload["vposter"], "https://x/p.jpg")
        self.assertEqual(payload["thumb_url"], "tos-cn-p-e5b010/th1")
        self.assertEqual(payload["duration"], 3)


class UploadChainTests(unittest.TestCase):
    def test_full_chain_returns_vid_thumb_and_html(self):
        fake = FakeVOD()
        uploader = TouTiaoVideoUploader(fake.vod, fake.mp)
        result = uploader.upload(b"file-bytes", filename="demo.mp4")
        self.assertEqual(result["vid"], "v0d3f0")
        self.assertEqual(result["thumb"]["uri"], "tos-cn-p-e5b010/th1")
        self.assertIn("PGC_VIDEO", result["pgc_video_html"])
        # 五步链：sts / apply(GET) / put(PUT) / commit(POST) 走 vod_request，thumb 走 mp_request
        self.assertEqual(len(fake.mp_calls), 2)
        methods = [m for m, _, _, _, _ in fake.vod_calls]
        self.assertEqual(methods, ["GET", "PUT", "POST"])

    def test_put_object_sends_crc32_and_octet_stream(self):
        fake = FakeVOD()
        seen = {}

        def vod(method, url, *, params=None, data=None, json_body=None, headers=None):
            seen.setdefault(method, []).append((url, data, headers))
            if "ApplyUploadInner" in url:
                return 200, {"Result": {"InnerUploadAddress": {"UploadNodes": [fake.NODE]}}}
            return 200, {}

        uploader = TouTiaoVideoUploader(vod, fake.mp)
        data = b"123456789"
        uploader.put_object(fake.NODE, data, filename="demo.mp4")
        url, sent, headers = seen["PUT"][0]
        self.assertEqual(url, "https://tos-lf-x.snssdk.com/tos-cn-v-e5b010/obj1")
        self.assertEqual(sent, data)
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertEqual(headers["Content-CRC32"], "cbf43926")
        self.assertEqual(headers["Authorization"], "Bearer auth1")
        self.assertIn("demo.mp4", headers["Content-Disposition"])

    def test_apply_upload_signs_get_with_sorted_query(self):
        fake = FakeVOD()
        captured = {}

        def vod(method, url, *, params=None, data=None, json_body=None, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return 200, {"Result": {"InnerUploadAddress": {"UploadNodes": [fake.NODE]}}}

        uploader = TouTiaoVideoUploader(vod, fake.mp)
        uploader.apply_upload(fake.STS, 39111)
        # canonical query 按字母序
        q = captured["url"].split("?", 1)[1]
        keys = [pair.split("=", 1)[0] for pair in q.split("&")]
        self.assertEqual(keys, sorted(keys))
        self.assertIn("Action=ApplyUploadInner", q)
        self.assertIn("fileSize=39111", q)
        # 签名头齐全
        auth = captured["headers"]["Authorization"]
        self.assertTrue(auth.startswith("AWS4-HMAC-SHA256 Credential=AKTEST/"))
        self.assertIn("/cn-north-1/vod/aws4_request", auth)
        self.assertIn("SignedHeaders=host;x-amz-content-sha256;x-amz-date;x-amz-security-token", auth)

    def test_commit_upload_posts_json_and_signs_body_hash(self):
        fake = FakeVOD()
        captured = {}

        def vod(method, url, *, params=None, data=None, json_body=None, headers=None):
            captured[method] = (url, data, headers)
            return 200, fake.COMMIT

        uploader = TouTiaoVideoUploader(vod, fake.mp)
        result = uploader.commit_upload(fake.STS, fake.NODE)
        self.assertEqual(result["Vid"], "v0d3f0")
        url, body, headers = captured["POST"]
        self.assertIn("Action=CommitUploadInner", url)
        self.assertEqual(json.loads(body), {"SessionKey": "sess-key-1", "Functions": []})
        self.assertEqual(headers["Content-Type"], "application/json")
        import hashlib
        self.assertEqual(headers["x-amz-content-sha256"], hashlib.sha256(body).hexdigest())

    def test_get_thumb_polls_until_items(self):
        fake = FakeVOD()
        state = {"n": 0}

        def mp(method, url, *, params=None, data=None, json_body=None, headers=None):
            if "get_thumb" in url:
                state["n"] += 1
                if state["n"] < 3:
                    return 200, {"code": 1102, "items": [], "message": "获取视频封面失败"}
                return 200, fake.THUMB
            return 200, fake.STS

        uploader = TouTiaoVideoUploader(FakeVOD().vod, mp)
        import tou_tiao_video_uploader as mod
        orig_sleep = mod.time.sleep
        mod.time.sleep = lambda s: None
        try:
            thumb = uploader.get_thumb("v0d3f0", timeout=5, interval=0)
        finally:
            mod.time.sleep = orig_sleep
        self.assertEqual(thumb["uri"], "tos-cn-p-e5b010/th1")
        self.assertEqual(state["n"], 3)

    def test_get_thumb_timeout_raises(self):
        def mp(method, url, *, params=None, data=None, json_body=None, headers=None):
            if "get_thumb" in url:
                return 200, {"code": 1102, "items": []}
            return 200, FakeVOD.STS

        uploader = TouTiaoVideoUploader(FakeVOD().vod, mp)
        import tou_tiao_video_uploader as mod
        orig_sleep = mod.time.sleep
        mod.time.sleep = lambda s: None
        try:
            with self.assertRaises(UploadError) as ctx:
                uploader.get_thumb("v0d3f0", timeout=0.01, interval=0)
        finally:
            mod.time.sleep = orig_sleep
        self.assertEqual(ctx.exception.stage, "get_thumb")

    def test_fetch_sts_requires_credentials(self):
        def mp(method, url, **kwargs):
            return 200, {"code": 0}

        uploader = TouTiaoVideoUploader(FakeVOD().vod, mp)
        with self.assertRaises(UploadError) as ctx:
            uploader.fetch_sts()
        self.assertEqual(ctx.exception.stage, "sts")

    def test_upload_rejects_empty_data(self):
        uploader = TouTiaoVideoUploader(FakeVOD().vod, FakeVOD().mp)
        with self.assertRaises(ValueError):
            uploader.upload(b"", filename="x.mp4")

    def test_constructor_requires_callables(self):
        with self.assertRaises(TypeError):
            TouTiaoVideoUploader(None, FakeVOD().mp)


if __name__ == "__main__":
    unittest.main()
