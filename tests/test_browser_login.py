import unittest
from unittest.mock import call, patch

from builder.auth import TouTiaoAuth
from builder.browser_login import (
    BrowserLoginError,
    CREATOR_HOME_URL,
    CREATOR_HOME_PATTERN,
    CREATOR_LOGIN_URL,
    CREATOR_LOGIN_STATUS_PATH,
    _complete_browser_login,
    _is_creator_home,
    browser_qrcode_login,
)


def cookie(name, value, domain=".toutiao.com", path="/"):
    return {"name": name, "value": value, "domain": domain, "path": path}


class FakePage:
    def __init__(self, *, final_url=CREATOR_HOME_URL, wait_error=None, login_status=None):
        self.url = "about:blank"
        self.final_url = final_url
        self.wait_error = wait_error
        self.navigations = []
        self.wait_args = None
        self.pause_ms = None
        self.login_status = login_status or {"http_ok": True, "code": 0, "is_login": True}
        self.evaluated_script = ""

    def goto(self, url, **kwargs):
        self.navigations.append((url, kwargs))
        self.url = CREATOR_LOGIN_URL if url == CREATOR_LOGIN_URL else self.final_url

    def wait_for_url(self, pattern, **kwargs):
        self.wait_args = (pattern, kwargs)
        if self.wait_error:
            raise self.wait_error
        self.url = CREATOR_HOME_URL

    def wait_for_timeout(self, timeout_ms):
        self.pause_ms = timeout_ms

    def evaluate(self, script):
        self.evaluated_script = script
        return self.login_status


class FakeContext:
    def __init__(self, page, before, after):
        self.page = page
        self.before = before
        self.after = after
        self.cookie_calls = []
        self.closed = False

    def new_page(self):
        return self.page

    def cookies(self, url):
        self.cookie_calls.append(url)
        return self.before if len(self.cookie_calls) == 1 else self.after

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, context):
        self.context = context
        self.context_calls = 0
        self.closed = False

    def new_context(self):
        self.context_calls += 1
        return self.context

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.launch_args = None

    def launch(self, **kwargs):
        self.launch_args = kwargs
        return self.browser


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


def workflow(*, final_url=CREATOR_HOME_URL, before=None, after=None,
             wait_error=None, login_status=None):
    page = FakePage(
        final_url=final_url, wait_error=wait_error, login_status=login_status
    )
    context = FakeContext(page, before or [], after or [])
    browser = FakeBrowser(context)
    chromium = FakeChromium(browser)
    return FakePlaywright(chromium), browser, context, page


class BrowserLoginTest(unittest.TestCase):
    def test_visible_isolated_browser_captures_only_new_creator_session(self):
        initial = [cookie("anon", "one")]
        final = initial + [
            cookie("shared", "two"),
            cookie("creator", "three", domain="mp.toutiao.com"),
        ]
        playwright, browser, context, page = workflow(before=initial, after=final)
        with patch("builtins.print") as prompt:
            auth = _complete_browser_login(playwright, TouTiaoAuth, 120)
        self.assertIn("扫码", prompt.call_args.args[0])
        self.assertIn("手机验证码", prompt.call_args.args[0])
        self.assertIn("滑块", prompt.call_args.args[0])
        self.assertEqual(playwright.chromium.launch_args, {"headless": False})
        self.assertEqual(browser.context_calls, 1)
        self.assertTrue(browser.closed and context.closed)
        self.assertEqual(page.navigations[0][0], CREATOR_LOGIN_URL)
        self.assertEqual(page.navigations[-1][0], CREATOR_HOME_URL)
        self.assertEqual(page.wait_args, (
            CREATOR_HOME_PATTERN,
            {"wait_until": "domcontentloaded", "timeout": 120000},
        ))
        self.assertEqual(page.pause_ms, 1500)
        self.assertIn(CREATOR_LOGIN_STATUS_PATH, page.evaluated_script)
        self.assertEqual(context.cookie_calls, [CREATOR_HOME_URL, CREATOR_HOME_URL])
        self.assertTrue(auth.creator_login_verified)
        self.assertEqual(auth.creator_cookie["creator"], "three")
        self.assertEqual(auth.cookie, {"anon": "one", "shared": "two"})
        self.assertNotIn("creator", auth.cookie)
        self.assertEqual((auth.access_token, auth.open_id), ("", ""))
        with self.assertRaises(ValueError):
            auth.require_open_api()
        auth.close()

    def test_rejects_redirect_and_unchanged_cookies(self):
        final = [cookie("shared", "one")]
        cases = [
            (CREATOR_LOGIN_URL, [], final),
            (CREATOR_HOME_URL, final, final),
        ]
        for final_url, before, after in cases:
            with self.subTest(final_url=final_url, before=before):
                playwright, browser, context, _ = workflow(
                    final_url=final_url, before=before, after=after
                )
                with patch("builtins.print"), self.assertRaises(BrowserLoginError):
                    _complete_browser_login(playwright, TouTiaoAuth, 5)
                self.assertTrue(browser.closed and context.closed)

    def test_wait_error_closes_fresh_browser(self):
        playwright, browser, context, _ = workflow(wait_error=TimeoutError())
        with patch("builtins.print"), self.assertRaises(TimeoutError):
            _complete_browser_login(playwright, TouTiaoAuth, 5)
        self.assertTrue(browser.closed and context.closed)

    def test_rejects_browser_cookie_when_status_api_says_logged_out(self):
        final = [cookie("creator", "three", domain="mp.toutiao.com")]
        playwright, browser, context, _ = workflow(
            after=final,
            login_status={"http_ok": True, "code": 0, "is_login": False},
        )
        with patch("builtins.print"), self.assertRaises(BrowserLoginError):
            _complete_browser_login(playwright, TouTiaoAuth, 5)
        self.assertTrue(browser.closed and context.closed)

    def test_creator_route_requires_exact_https_host(self):
        self.assertTrue(_is_creator_home("https://mp.toutiao.com/profile_v4/"))
        self.assertFalse(_is_creator_home("https://evil.test/profile_v4/"))
        self.assertFalse(_is_creator_home("https://mp.toutiao.com/auth/page/login"))
        self.assertIsNone(CREATOR_HOME_PATTERN.match(
            "https://mp.toutiao.com/auth/page/login?next=/profile_v4/"
        ))

    def test_generic_and_legacy_auth_entries_share_browser_flow(self):
        instance = TouTiaoAuth()
        with patch("builder.browser_login.browser_login", return_value=instance) as login:
            self.assertIs(TouTiaoAuth.from_browser_login(), instance)
            self.assertIs(TouTiaoAuth.from_browser_login(timeout_seconds=45), instance)
            self.assertIs(TouTiaoAuth.from_qrcode_login(timeout_seconds=60), instance)
        self.assertEqual(login.call_args_list, [
            call(TouTiaoAuth, timeout_seconds=600),
            call(TouTiaoAuth, timeout_seconds=45),
            call(TouTiaoAuth, timeout_seconds=60),
        ])
        instance.close()

    def test_legacy_helper_alias_delegates_to_generic_login(self):
        instance = TouTiaoAuth()
        with patch("builder.browser_login.browser_login", return_value=instance) as login:
            self.assertIs(
                browser_qrcode_login(TouTiaoAuth, timeout_seconds=75), instance
            )
        login.assert_called_once_with(TouTiaoAuth, timeout_seconds=75)
        instance.close()
