import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import requests

from builder.auth import OpenApiError, TouTiaoAuth, TouTiaoOAuth
from tou_tiao_api import TouTiaoApi
from tou_tiao_creator_api import TouTiaoCreatorApi
from tou_tiao_creator_web_api import TouTiaoCreatorWebApi


class FakeResponse:
    def __init__(self, *, text="", json_data=None, status=200, headers=None):
        self.text = text
        self.json_data = json_data
        self.status = status
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.json_data


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


class AuthContractTest(unittest.TestCase):
    def test_cookie_constructor_and_legacy_alias(self):
        auth = TouTiaoAuth.from_cookie("msToken=a=b;ttwid=two")
        self.assertEqual(auth.cookie, {"msToken": "a=b", "ttwid": "two"})
        self.assertIs(auth.perepare_auth("x=1"), auth)
        self.assertEqual(auth.cookie, {"x": "1"})
        auth.close()
        combined = TouTiaoAuth.from_access_token(
            "access", "open-id", cookie_str="ttwid=web-cookie", session=FakeSession([])
        )
        self.assertEqual(combined.cookie, {"ttwid": "web-cookie"})
        self.assertEqual((combined.access_token, combined.open_id), ("access", "open-id"))

    def test_oauth_authorize_and_exchange(self):
        url = TouTiaoOAuth.authorize_url(
            "client", "https://example.test/callback", ["toutiao.video.create"], "nonce"
        )
        parsed = urlparse(url)
        self.assertEqual(parsed.netloc, "open.snssdk.com")
        self.assertEqual(parsed.path, "/oauth/authorize/")
        self.assertEqual(parse_qs(parsed.query)["scope"], ["toutiao.video.create"])

        http = FakeSession(
            [FakeResponse(json_data={"data": {
                "error_code": 0, "access_token": "token", "open_id": "user",
                "refresh_token": "refresh-one", "expires_in": "86400",
                "refresh_expires_in": "2592000", "scope": "toutiao.video.create",
            }})]
        )
        auth = TouTiaoOAuth.exchange_code("client", "secret", "code", session=http)
        self.assertEqual((auth.access_token, auth.open_id), ("token", "user"))
        self.assertEqual(auth.refresh_token, "refresh-one")
        self.assertEqual((auth.expires_in, auth.refresh_expires_in), (86400, 2592000))
        self.assertEqual(auth.scope, "toutiao.video.create")
        self.assertGreater(auth.expires_at, 0)
        self.assertGreater(auth.refresh_expires_at, auth.expires_at)
        method, endpoint, kwargs = http.calls[0]
        self.assertEqual((method, endpoint), ("POST", "https://open.snssdk.com/oauth/access_token/"))
        self.assertEqual(kwargs["data"]["grant_type"], "authorization_code")
        self.assertEqual(kwargs["data"]["code"], "code")
        prepared = requests.Request(method, endpoint, data=kwargs["data"]).prepare()
        self.assertEqual(prepared.headers["Content-Type"], "application/x-www-form-urlencoded")

    def test_oauth_callback_checks_redirect_and_state_before_code(self):
        redirect = "https://example.test/callback?flow=toutiao"
        callback = redirect + "&code=one-time-code&state=expected"
        self.assertEqual(
            TouTiaoOAuth.code_from_callback(
                callback, redirect_uri=redirect, expected_state="expected"
            ),
            "one-time-code",
        )
        invalid = (
            "https://evil.test/callback?flow=toutiao&code=secret&state=expected",
            redirect + "&code=secret&state=wrong",
            redirect + "&code=secret&code=second&state=expected",
            redirect + "&error=access_denied&state=expected",
        )
        for value in invalid:
            with self.subTest(callback=value), self.assertRaises(ValueError) as caught:
                TouTiaoOAuth.code_from_callback(
                    value, redirect_uri=redirect, expected_state="expected"
                )
            self.assertNotIn("secret", str(caught.exception))
        with self.assertRaises(ValueError):
            TouTiaoOAuth.authorize_url("client", "javascript:alert(1)", ["user_info"], "nonce")
        with self.assertRaises(ValueError):
            TouTiaoOAuth.authorize_url("client", redirect, "user_info", "nonce")

    def test_refresh_access_token_uses_toutiao_multipart_and_updates_session(self):
        http = FakeSession([FakeResponse(json_data={"data": {
            "error_code": 0, "access_token": "token-two", "open_id": "user",
            "refresh_token": "refresh-one", "expires_in": "7200",
            "refresh_expires_in": "2500000", "scope": "toutiao.video.create",
        }})])
        auth = TouTiaoAuth.from_access_token(
            "token-one", "user", refresh_token="refresh-one",
            expires_in=60, refresh_expires_in=2592000, session=http,
        )
        original_refresh_expiry = auth.refresh_expires_at
        self.assertIs(TouTiaoOAuth.refresh_access_token("client", auth), auth)
        self.assertEqual((auth.access_token, auth.refresh_token), ("token-two", "refresh-one"))
        self.assertEqual((auth.expires_in, auth.refresh_expires_in), (7200, 2500000))
        self.assertEqual(auth.scope, "toutiao.video.create")
        self.assertGreater(auth.refresh_expires_at, auth.expires_at)
        self.assertLessEqual(auth.refresh_expires_at, original_refresh_expiry)
        method, endpoint, kwargs = http.calls[0]
        self.assertEqual((method, endpoint), ("POST", "https://open.snssdk.com/oauth/refresh_token/"))
        self.assertEqual(kwargs["files"], {
            "client_key": (None, "client"),
            "grant_type": (None, "refresh_token"),
            "refresh_token": (None, "refresh-one"),
        })
        self.assertNotIn("data", kwargs)
        self.assertNotIn("client_secret", kwargs["files"])
        prepared = requests.Request(method, endpoint, files=kwargs["files"]).prepare()
        self.assertTrue(prepared.headers["Content-Type"].startswith("multipart/form-data; boundary="))

    def test_refresh_error_keeps_existing_credentials(self):
        http = FakeSession([FakeResponse(json_data={"data": {"error_code": 10010}})])
        auth = TouTiaoAuth.from_access_token(
            "token-one", "user", refresh_token="refresh-one", session=http,
        )
        with self.assertRaises(OpenApiError):
            TouTiaoOAuth.refresh_access_token("client", auth)
        self.assertEqual((auth.access_token, auth.refresh_token), ("token-one", "refresh-one"))
        with self.assertRaises(ValueError):
            TouTiaoOAuth.refresh_access_token("", auth)
        self.assertEqual(len(http.calls), 1)

    def test_error_in_extra_without_data_raises_platform_error(self):
        http = FakeSession([FakeResponse(json_data={"extra": {"error_code": 10010}})])
        auth = TouTiaoAuth.from_access_token("token", "user", session=http)
        with self.assertRaises(OpenApiError) as caught:
            TouTiaoCreatorApi(auth).list_videos()
        self.assertEqual(caught.exception.code, 10010)


class ReadContractTest(unittest.TestCase):
    def test_search_and_legacy_return_shape(self):
        html = '<a href="https://www.toutiao.com/article/123/">标题</a>'
        http = FakeSession([FakeResponse(text=html), FakeResponse(text=html)])
        api = TouTiaoApi(session=http)
        auth = TouTiaoAuth.from_cookie("ttwid=abc")
        result = api.search("人工智能", 0, auth)
        self.assertEqual(result["items"], [{"url": "https://www.toutiao.com/article/123/", "title": "标题"}])
        self.assertEqual(api.getSearchInfo("人工智能", 0, auth), (True, "成功", html))
        method, endpoint, kwargs = http.calls[0]
        self.assertEqual((method, endpoint), ("GET", "https://so.toutiao.com/search"))
        self.assertEqual(kwargs["params"]["keyword"], "人工智能")
        self.assertEqual(kwargs["cookies"], {"ttwid": "abc"})
        self.assertNotIn("search_id", kwargs["params"])

    def test_search_pagination_fails_before_returning_duplicate_page(self):
        http = FakeSession([])
        api = TouTiaoApi(session=http)
        auth = TouTiaoAuth()
        with self.assertRaisesRegex(NotImplementedError, "only page_num=0"):
            api.search("人工智能", 1, auth)
        with self.assertRaisesRegex(NotImplementedError, "only page_num=0"):
            api.search("人工智能", 1, auth, search_id="old-id")
        success, message, data = api.getSearchInfo("人工智能", 1, auth)
        self.assertFalse(success)
        self.assertIn("only page_num=0", message)
        self.assertIsNone(data)
        self.assertEqual(http.calls, [])

    def test_search_extracts_current_jump_link(self):
        target = "https://toutiao.com/group/123/"
        bridge = "https://article.zlink.toutiao.com/J4dQM?h5_url=" + quote(target, safe="")
        jump = "/search/jump?url=" + quote(bridge, safe="")
        http = FakeSession([FakeResponse(text=f'<a href="{jump}">标题</a>')])
        result = TouTiaoApi(session=http).search("人工智能", 0, TouTiaoAuth())
        self.assertEqual(result["items"], [{"url": "https://www.toutiao.com/group/123/", "title": "标题"}])

    def test_item_parses_article_and_rejects_other_hosts(self):
        html = ('<script type="application/ld+json">'
                '{"headline":"文章标题","image":"https://img.test/a.jpg"}'
                '</script><article>正文</article>')
        http = FakeSession([FakeResponse(text=html)])
        api = TouTiaoApi(session=http)
        auth = TouTiaoAuth.from_cookie("ttwid=abc")
        result = api.item("https://www.toutiao.com/article/123/", auth)
        self.assertEqual(result["type"], "article")
        self.assertEqual(result["images"], ["https://img.test/a.jpg"])
        self.assertEqual(result["content"], "正文")
        with self.assertRaises(ValueError):
            api.item("https://example.test/article/123/", auth)
        self.assertEqual(len(http.calls), 1)

    def test_group_search_result_uses_rendered_canonical_video(self):
        state = {"data": {"initialVideo": {"videoPlayInfo": {"dynamic_video": {
            "dynamic_video_list": [{
                "main_url": "https://v1-web.toutiaovod.com/video-stream",
                "video_meta": {"definition": "720p", "vwidth": 1280, "vheight": 720},
            }],
            "dynamic_audio_list": [{"main_url": "https://v1-web.toutiaovod.com/audio-stream"}],
        }}}}}
        video_html = (
            '<script type="application/ld+json">'
            '{"@type":"VideoObject","name":"视频标题",'
            '"description":"视频简介","thumbnailUrl":"https://img.test/cover.jpg"}'
            '</script>'
            f'<script id="RENDER_DATA">{quote(json.dumps(state))}</script>'
        )
        http = FakeSession([FakeResponse(text="<html>JSVM</html>")])
        api = TouTiaoApi(session=http)
        rendered = []

        def render(url, auth):
            rendered.append(url)
            return video_html, "https://www.toutiao.com/video/123/"

        api._render_work_page = render
        auth = TouTiaoAuth.from_cookie("ttwid=own-cookie")
        result = api.item("https://www.toutiao.com/group/123/", auth)
        self.assertEqual(rendered, ["https://www.toutiao.com/article/123/"])
        self.assertEqual(http.calls[0][1], "https://www.toutiao.com/article/123/")
        self.assertFalse(http.calls[0][2]["allow_redirects"])
        self.assertEqual(result["url"], "https://www.toutiao.com/video/123/")
        self.assertEqual(result["type"], "video")
        self.assertEqual((result["title"], result["content"]), ("视频标题", "视频简介"))
        self.assertEqual(result["images"], ["https://img.test/cover.jpg"])
        self.assertEqual(result["videos"], [])
        self.assertEqual(result["media_streams"]["video"][0]["vheight"], 720)
        self.assertEqual(
            result["media_streams"]["audio"][0]["url"],
            "https://v1-web.toutiaovod.com/audio-stream",
        )

    def test_item_follows_same_site_redirect_and_rejects_external_redirect(self):
        video_html = (
            '<script type="application/ld+json">'
            '{"@type":"VideoObject","name":"视频",'
            '"description":"简介","contentUrl":"https://media.test/video.mp4"}'
            '</script>'
        )
        http = FakeSession([
            FakeResponse(status=302, headers={"Location": "/video/123/"}),
            FakeResponse(text=video_html),
        ])
        result = TouTiaoApi(session=http).item(
            "https://www.toutiao.com/article/123/", TouTiaoAuth()
        )
        self.assertEqual(result["type"], "video")
        self.assertEqual(result["videos"], ["https://media.test/video.mp4"])
        self.assertEqual(len(http.calls), 2)

        http = FakeSession([
            FakeResponse(status=302, headers={"Location": "https://example.test/item/123/"})
        ])
        with self.assertRaisesRegex(ValueError, "HTTPS toutiao.com"):
            TouTiaoApi(session=http).item(
                "https://www.toutiao.com/article/123/", TouTiaoAuth.from_cookie("ttwid=own-cookie")
            )
        self.assertEqual(len(http.calls), 1)

    def test_import_does_not_replace_subprocess_popen_class(self):
        self.assertIsInstance(subprocess.Popen, type)

    def test_legacy_video_endpoint_reports_absent_single_file(self):
        response = FakeResponse(text='tt__video__9n4f3t({"data":{"video_list":[]}})')
        api = TouTiaoApi(session=FakeSession([response]))
        with self.assertRaisesRegex(ValueError, "no single-file URL"):
            api.get_video_url("public-video-id", TouTiaoAuth())

    def test_one_auth_keeps_creator_login_and_consumer_collection_cookies_separate(self):
        search_html = '<a href="https://www.toutiao.com/article/123/">公开作品</a>'
        item_html = (
            '<script type="application/ld+json">'
            '{"headline":"公开作品","description":"简介"}</script>'
        )
        http = FakeSession([
            FakeResponse(json_data={"code": 0, "data": {"is_login": True}}),
            FakeResponse(text=search_html),
            FakeResponse(text=item_html),
        ])
        auth = TouTiaoAuth.from_cookie("parent=shared", session=http)
        auth.prepare_creator_auth("creator=private; parent=shared")
        self.assertTrue(TouTiaoCreatorWebApi(auth).is_logged_in())
        api = TouTiaoApi(session=auth.session)
        search = api.search("咖啡", 0, auth)
        item = api.item(search["items"][0]["url"], auth)
        self.assertEqual((item["type"], item["title"]), ("article", "公开作品"))
        self.assertEqual(http.calls[0][2]["cookies"], {"creator": "private", "parent": "shared"})
        self.assertEqual(http.calls[1][2]["cookies"], {"parent": "shared"})
        self.assertEqual(http.calls[2][2]["cookies"], {"parent": "shared"})


class CreatorContractTest(unittest.TestCase):
    def setUp(self):
        self.success = {"data": {"error_code": 0, "item_id": "item"}, "extra": {"error_code": 0}}

    def test_upload_publish_and_list_are_separate_requests(self):
        http = FakeSession(
            [
                FakeResponse(json_data={"data": {"error_code": 0, "video": {"video_id": "v1"}}}),
                FakeResponse(json_data=self.success),
                FakeResponse(json_data={"data": {"error_code": 0, "list": []}}),
            ]
        )
        auth = TouTiaoAuth.from_access_token("secret-token", "open-id", session=http)
        creator = TouTiaoCreatorApi(auth)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clip.mp4"
            path.write_bytes(b"video")
            self.assertEqual(creator.upload_video(path)["video"]["video_id"], "v1")
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(creator.publish_video("v1", "演示视频")["item_id"], "item")
        self.assertEqual(creator.list_videos()["list"], [])
        self.assertEqual([call[1] for call in http.calls], [
            "https://open.douyin.com/toutiao/video/upload/",
            "https://open.douyin.com/toutiao/video/create/",
            "https://open.douyin.com/toutiao/video/list/",
        ])
        self.assertEqual(http.calls[0][2]["headers"], {"access-token": "secret-token"})
        self.assertEqual(http.calls[0][2]["params"], {"open_id": "open-id"})
        self.assertEqual(http.calls[1][2]["json"], {"video_id": "v1", "text": "演示视频"})
        self.assertEqual(http.calls[2][2]["params"], {"open_id": "open-id", "cursor": 0, "count": 10})

    def test_platform_error_raises_without_leaking_token(self):
        http = FakeSession([FakeResponse(json_data={"data": {"error_code": 2100005}})])
        auth = TouTiaoAuth.from_access_token("secret-token", "open-id", session=http)
        with self.assertRaises(OpenApiError) as caught:
            TouTiaoCreatorApi(auth).list_videos()
        self.assertIn("2100005", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))

    def test_article_is_explicitly_unsupported_by_official_api(self):
        auth = TouTiaoAuth.from_access_token("token", "user", session=FakeSession([]))
        with self.assertRaises(NotImplementedError):
            TouTiaoCreatorApi(auth).publish_article("title", "body")


if __name__ == "__main__":
    unittest.main()
