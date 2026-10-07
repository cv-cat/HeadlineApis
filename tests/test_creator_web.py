import unittest

from builder.auth import TouTiaoAuth
from tou_tiao_creator_web_api import CreatorWebApiError, TouTiaoCreatorWebApi


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError("HTTP error")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class CreatorWebApiTest(unittest.TestCase):
    def test_read_only_status_permission_and_drafts_use_creator_cookie(self):
        session = FakeSession([
            FakeResponse({"code": 0, "data": {"is_login": True, "user": {"secret": "x"}}}),
            FakeResponse({"code": 0, "has_auth": False}),
            FakeResponse({"code": 0, "draft_list": [{"gid": "draft-id"}]}),
        ])
        auth = TouTiaoAuth(session=session)
        auth.prepare_creator_auth("creator_session=web-secret", verified=True)
        api = TouTiaoCreatorWebApi(auth)
        self.assertTrue(api.is_logged_in())
        self.assertFalse(api.has_account_auth())
        self.assertEqual(api.list_drafts(count=10), [{"gid": "draft-id"}])
        self.assertEqual([url for url, _ in session.calls], [
            "https://mp.toutiao.com/mp/agw/media/user_login_status_api",
            "https://mp.toutiao.com/mp/agw/media/check_user_auth",
            "https://mp.toutiao.com/mp/agw/creator_center/draft_list",
        ])
        for _, kwargs in session.calls:
            self.assertEqual(kwargs["cookies"], {"creator_session": "web-secret"})
            self.assertNotIn("access-token", kwargs["headers"])
            self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(session.calls[2][1]["params"], {"type": 0, "count": 10})
        self.assertEqual((auth.access_token, auth.open_id), ("", ""))

    def test_requires_creator_cookie_and_rejects_bad_contract(self):
        with self.assertRaises(ValueError):
            TouTiaoCreatorWebApi(TouTiaoAuth.from_access_token("oauth", "user"))
        session = FakeSession([
            FakeResponse({"code": 100004, "message": "private info"}),
            FakeResponse({"code": 0, "draft_list": "bad"}),
        ])
        auth = TouTiaoAuth(session=session)
        auth.prepare_creator_auth("creator_session=web-secret")
        api = TouTiaoCreatorWebApi(auth)
        with self.assertRaises(CreatorWebApiError) as caught:
            api.is_logged_in()
        self.assertEqual(caught.exception.code, 100004)
        self.assertNotIn("private info", str(caught.exception))
        with self.assertRaises(ValueError):
            api.list_drafts()
        with self.assertRaises(ValueError):
            api.list_drafts(count=21)
        self.assertEqual(len(session.calls), 2)
