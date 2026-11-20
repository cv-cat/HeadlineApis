import unittest
from urllib.parse import parse_qs, urlparse

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


if __name__ == "__main__":
    unittest.main()
