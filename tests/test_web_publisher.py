import json
import unittest
from urllib.parse import parse_qs, parse_qsl, urlparse

from tou_tiao_web_publisher import (
    MsTokenProvider,
    PublishError,
    PublishTransport,
    TouTiaoWebPublisher,
)


def _make_publisher(token="tok123", response=None):
    calls = []

    def send(url, body, referer):
        calls.append((url, body, referer))
        return response or {"code": 0, "data": {"pgc_id": "771", "content": "<p>ok</p>"}}

    publisher = TouTiaoWebPublisher(
        PublishTransport(send),
        a_bogus_fn=lambda query, body: "AB" * 10,
    )
    if token is not None:
        publisher.with_token_provider(MsTokenProvider(lambda: token))
    return publisher, calls


class BodyContractTests(unittest.TestCase):
    def test_build_body_contains_27_fields_and_clean_pgc_id(self):
        body = TouTiaoWebPublisher.build_body("标题", "<p>正文</p>", save="1")
        fields = parse_qs(body, keep_blank_values=True)
        self.assertEqual(fields["save"], ["1"])
        self.assertEqual(fields["pgc_id"], [""])
        self.assertEqual(fields["title"], ["标题"])
        self.assertEqual(fields["content"], ["<p>正文</p>"])
        self.assertEqual(len(fields), 27)

    def test_build_body_rejects_empty_and_bad_save(self):
        with self.assertRaises(ValueError):
            TouTiaoWebPublisher.build_body("", "<p>x</p>")
        with self.assertRaises(ValueError):
            TouTiaoWebPublisher.build_body("t", "   ")
        with self.assertRaises(ValueError):
            TouTiaoWebPublisher.build_body("t", "<p>x</p>", save="2")


class PublishTests(unittest.TestCase):
    def test_publish_sends_msToken_a_bogus_and_save_one(self):
        publisher, calls = _make_publisher()
        data = publisher.publish("标题", "<p>正文</p>")
        self.assertEqual(data["pgc_id"], "771")
        url, body, referer = calls[0]
        parsed = urlparse(url)
        self.assertEqual(parsed.path, "/mp/agw/article/publish")
        query = parse_qs(parsed.query)
        self.assertEqual(query["msToken"], ["tok123"])
        self.assertEqual(query["a_bogus"], ["AB" * 10])
        self.assertIn("aid=1231", parsed.query)
        self.assertEqual(parse_qs(body, keep_blank_values=True)["save"], ["1"])
        self.assertEqual(referer, "https://mp.toutiao.com/profile_v4/graphic/publish")

    def test_publish_without_token_provider_omits_msToken(self):
        publisher, calls = _make_publisher(token=None)
        publisher.publish("t", "<p>x</p>")
        self.assertNotIn("msToken", parse_qs(urlparse(calls[0][0]).query))

    def test_publish_wraps_nonzero_code(self):
        publisher, _ = _make_publisher(response={"code": 7050, "message": "保存失败"})
        with self.assertRaises(PublishError) as ctx:
            publisher.publish("t", "<p>x</p>", save="0")
        self.assertEqual(ctx.exception.code, 7050)

    def test_publish_requires_pgc_id_in_success(self):
        publisher, _ = _make_publisher(response={"code": 0, "data": {}})
        with self.assertRaises(ValueError):
            publisher.publish("t", "<p>x</p>")

    def test_a_bogus_fn_receives_query_and_body(self):
        seen = []

        def bogus(query, body):
            seen.append((query, body))
            return "ab"

        publisher = TouTiaoWebPublisher(
            PublishTransport(lambda u, b, r: {"code": 0, "data": {"pgc_id": "1"}}),
            a_bogus_fn=bogus,
        )
        publisher.publish("t", "<p>x</p>")
        query, body = seen[0]
        self.assertIn("source=mp", query)
        self.assertIn("save=1", body)

    def test_empty_a_bogus_is_rejected(self):
        publisher = TouTiaoWebPublisher(
            PublishTransport(lambda u, b, r: {"code": 0, "data": {"pgc_id": "1"}}),
            a_bogus_fn=lambda q, b: "",
        )
        with self.assertRaises(ValueError):
            publisher.publish("t", "<p>x</p>")

    def test_provider_rejects_empty_token(self):
        publisher = TouTiaoWebPublisher(
            PublishTransport(lambda u, b, r: {}),
            a_bogus_fn=lambda q, b: "ab",
        ).with_token_provider(MsTokenProvider(lambda: ""))
        with self.assertRaises(ValueError):
            publisher.publish("t", "<p>x</p>")

    def test_constructors_reject_non_callable(self):
        with self.assertRaises(TypeError):
            PublishTransport("not-callable")
        with self.assertRaises(TypeError):
            MsTokenProvider(None)
        with self.assertRaises(TypeError):
            TouTiaoWebPublisher(PublishTransport(lambda u, b, r: {}), a_bogus_fn="x")


class WeitoutiaoTests(unittest.TestCase):
    def _publisher(self, response):
        calls = []

        def send(url, body, referer):
            calls.append((url, body, referer))
            return response

        return (
            TouTiaoWebPublisher(PublishTransport(send), a_bogus_fn=lambda q, b: "ab"),
            calls,
        )

    def test_publish_weitoutiao_posts_wtt_with_three_fields(self):
        publisher, calls = self._publisher(
            {"code": 0, "data": {"thread_id": 1882375498097675, "add_thread_limit": 0}}
        )
        data = publisher.publish_weitoutiao("微头条文本")
        self.assertEqual(data["thread_id"], 1882375498097675)
        url, body, referer = calls[0]
        parsed = urlparse(url)
        self.assertEqual(parsed.path, "/mp/agw/article/wtt")
        self.assertEqual(parse_qs(parsed.query)["type"], ["wtt"])
        fields = parse_qs(body, keep_blank_values=True)
        self.assertEqual(fields["content"], ["微头条文本"])
        self.assertEqual(fields["save"], ["1"])
        self.assertEqual(fields["source"], ["mp"])
        self.assertEqual(len(fields), 3)
        self.assertEqual(referer, "https://mp.toutiao.com/profile_v4/weibogroup/publish")

    def test_publish_weitoutiao_rejects_empty_content_and_bad_save(self):
        publisher, _ = self._publisher({"code": 0, "data": {"thread_id": 1}})
        with self.assertRaises(ValueError):
            publisher.publish_weitoutiao("   ")
        with self.assertRaises(ValueError):
            publisher.publish_weitoutiao("x", save="2")

    def test_publish_weitoutiao_wraps_error_code(self):
        publisher, _ = self._publisher({"code": 7050, "message": "保存失败"})
        with self.assertRaises(PublishError) as ctx:
            publisher.publish_weitoutiao("x", save="0")
        self.assertEqual(ctx.exception.code, 7050)

    def test_publish_weitoutiao_requires_thread_id(self):
        publisher, _ = self._publisher({"code": 0, "data": {}})
        with self.assertRaises(ValueError):
            publisher.publish_weitoutiao("x")




class DraftContractTests(unittest.TestCase):
    def test_draft_body_uses_draft_field_set(self):
        body = TouTiaoWebPublisher.build_body("", "<p>draft</p>", save="0")
        keys = [k for k, _ in parse_qsl(body, keep_blank_values=True)]
        self.assertNotIn("pgc_id", keys)
        self.assertIn("customer_nick_name", keys)
        self.assertEqual(len(keys), 27)
        # 字段序与网页编辑器实测一致
        self.assertEqual(keys.index("customer_nick_name"), keys.index("star_order_name") + 1)

    def test_publish_body_keeps_pgc_id_and_no_nick_name(self):
        body = TouTiaoWebPublisher.build_body("标题", "<p>x</p>", save="1")
        keys = [k for k, _ in parse_qsl(body, keep_blank_values=True)]
        self.assertEqual(keys[0], "pgc_id")
        self.assertNotIn("customer_nick_name", keys)

    def test_draft_allows_empty_title(self):
        body = TouTiaoWebPublisher.build_body("", "<p>d</p>", save="0")
        self.assertIn("content=", body)

    def test_publish_requires_title(self):
        with self.assertRaises(ValueError):
            TouTiaoWebPublisher.build_body("", "<p>x</p>", save="1")




class DraftApiTests(unittest.TestCase):
    def _publisher(self, send=None, get=None):
        class _T(PublishTransport):
            def __init__(self):
                super().__init__(send or (lambda u, b, r: {"code": 0, "data": {"pgc_id": "1"}}))
                self._get = get

            def get(self, url, referer):
                if self._get is None:
                    raise NotImplementedError
                return self._get(url, referer)
        return TouTiaoWebPublisher(_T(), a_bogus_fn=lambda query, body: "AB" * 10)

    def test_save_article_draft_returns_pgc_id(self):
        publisher = self._publisher()
        data = publisher.save_article_draft("<p>draft</p>", title="题目")
        self.assertEqual(data, {"pgc_id": "1"})

    def test_get_weitoutiao_draft_returns_envelope(self):
        seen = {}

        def fake_get(url, referer):
            seen["url"] = url
            seen["referer"] = referer
            return {"code": 0, "draft": {"gid": "g1", "origin_draft": "{}"}}

        publisher = self._publisher(get=fake_get)
        draft = publisher.get_weitoutiao_draft("g1")
        self.assertEqual(draft["gid"], "g1")
        self.assertIn("/mp/agw/draft/get_ugc_draft", seen["url"])
        self.assertIn("gid=g1", seen["url"])
        self.assertIn("a_bogus=", seen["url"])
        self.assertIn("weibogroup/publish", seen["referer"])

    def test_get_weitoutiao_draft_rejects_bad_code(self):
        publisher = self._publisher(get=lambda u, r: {"code": 1064, "message": "err"})
        with self.assertRaises(PublishError):
            publisher.get_weitoutiao_draft("g1")

    def test_get_weitoutiao_draft_rejects_empty_gid(self):
        publisher = self._publisher()
        with self.assertRaises(ValueError):
            publisher.get_weitoutiao_draft("  ")


if __name__ == "__main__":
    unittest.main()


class WeitoutiaoDraftTests(unittest.TestCase):
    def _make(self, response=None):
        calls = []

        def send(url, body, referer):
            calls.append((url, body, referer))
            return response or {"code": 0, "gid": "1882382975576140", "message": "success"}

        publisher = TouTiaoWebPublisher(
            PublishTransport(send),
            a_bogus_fn=lambda query, body: "AB" * 10,
        )
        publisher.with_token_provider(MsTokenProvider(lambda: "tok123"))
        return publisher, calls

    def test_save_weitoutiao_draft_contract(self):
        publisher, calls = self._make()
        gid = publisher.save_weitoutiao_draft("微头条草稿")
        self.assertEqual(gid, "1882382975576140")
        url, body, referer = calls[0]
        # JSON body：draft_type=1 + draft_info JSON 字符串
        parsed = json.loads(body)
        self.assertEqual(parsed["draft_type"], 1)
        self.assertEqual(parsed["post_id"], "")
        draft_info = json.loads(parsed["draft_info"])
        self.assertEqual(draft_info["schema"], "sslocal://send_thread")
        self.assertEqual(draft_info["content"], "微头条草稿")
        self.assertEqual(draft_info["images"], [])
        self.assertIn("update_time", draft_info)
        self.assertIn("/mp/agw/draft/save_ugc_draft", url)
        self.assertIn("msToken=tok123", url)
        self.assertIn("a_bogus=", url)
        self.assertIn("weibogroup/publish", referer)

    def test_save_weitoutiao_draft_rejects_empty_content(self):
        publisher, _ = self._make()
        with self.assertRaises(ValueError):
            publisher.save_weitoutiao_draft("  ")

    def test_save_weitoutiao_draft_raises_on_error_code(self):
        publisher, _ = self._make({"code": 1064, "message": "bad"})
        with self.assertRaises(PublishError) as ctx:
            publisher.save_weitoutiao_draft("x")
        self.assertEqual(ctx.exception.code, 1064)

    def test_save_weitoutiao_draft_images_list(self):
        publisher, calls = self._make()
        publisher.save_weitoutiao_draft("带图", images=["http://img/1.jpg"])
        draft_info = json.loads(json.loads(calls[0][1])["draft_info"])
        self.assertEqual(draft_info["images"], ["http://img/1.jpg"])
