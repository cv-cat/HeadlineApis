import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from builder.auth import OpenApiError, TouTiaoAuth, TouTiaoOAuth
from tou_tiao_api import TouTiaoApi
from tou_tiao_creator_api import TouTiaoCreatorApi


class FakeResponse:
    def __init__(self, *, text="", json_data=None, status=200):
        self.text = text
        self.json_data = json_data
        self.status = status

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

    def test_oauth_authorize_and_exchange(self):
        url = TouTiaoOAuth.authorize_url(
            "client", "https://example.test/callback", ["toutiao.video.create"], "nonce"
        )
        parsed = urlparse(url)
        self.assertEqual(parsed.netloc, "open.snssdk.com")
        self.assertEqual(parsed.path, "/oauth/authorize/")
        self.assertEqual(parse_qs(parsed.query)["scope"], ["toutiao.video.create"])

        http = FakeSession(
            [FakeResponse(json_data={"data": {"error_code": 0, "access_token": "token", "open_id": "user"}})]
        )
        auth = TouTiaoOAuth.exchange_code("client", "secret", "code", session=http)
        self.assertEqual((auth.access_token, auth.open_id), ("token", "user"))
        method, endpoint, kwargs = http.calls[0]
        self.assertEqual((method, endpoint), ("POST", "https://open.snssdk.com/oauth/access_token/"))
        self.assertEqual(kwargs["data"]["grant_type"], "authorization_code")
        self.assertEqual(kwargs["data"]["code"], "code")


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

    def test_search_pagination_uses_fresh_search_id(self):
        initial = '<script>search_id:"20261007131933E9F565D6E83B28AC64EE"</script>'
        http = FakeSession([FakeResponse(text=initial), FakeResponse(text="page two")])
        api = TouTiaoApi(session=http)
        api.search("人工智能", 1, TouTiaoAuth())
        self.assertEqual(http.calls[1][2]["params"]["search_id"], "20261007131933E9F565D6E83B28AC64EE")
        self.assertEqual(http.calls[1][2]["params"]["source"], "pagination")

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
        self.assertEqual(result["images"], ["https://img.test/a.jpg"])
        self.assertEqual(result["content"], "正文")
        with self.assertRaises(ValueError):
            api.item("https://example.test/article/123/", auth)
        self.assertEqual(len(http.calls), 1)


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
