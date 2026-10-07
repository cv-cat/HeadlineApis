"""借助平台网页完成扫码；不复刻私有二维码接口，也不复用浏览器配置。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

CREATOR_LOGIN_URL = "https://mp.toutiao.com/auth/page/login"
CREATOR_HOME_URL = "https://mp.toutiao.com/profile_v4/"
CREATOR_HOME_PATTERN = re.compile(r"^https://mp\.toutiao\.com/profile_v4(?:/|\?|$)")


class BrowserLoginError(RuntimeError):
    """浏览器扫码完成后的只读核验失败。"""


def _is_creator_home(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "mp.toutiao.com"
        and (parsed.path == "/profile_v4" or parsed.path.startswith("/profile_v4/"))
    )


def _cookie_identity(cookie: dict) -> tuple[str, str, str, str]:
    return (
        cookie["name"], cookie["value"], cookie["domain"], cookie["path"]
    )


def _cookie_header(cookies: list[dict]) -> str:
    """同名 Cookie 选更具体的 domain/path，避免 dict 随机覆盖。"""
    selected: dict[str, str] = {}
    ordered = sorted(
        cookies,
        key=lambda item: (len(item["domain"].lstrip(".")), len(item["path"])),
    )
    for item in ordered:
        selected[item["name"]] = item["value"]
    return "; ".join(f"{name}={value}" for name, value in selected.items())


def _read_shared_cookies(cookies: list[dict]) -> str:
    # 只有显式父域 Cookie 才能用于 www/so 的旧只读接口。
    return _cookie_header([
        item for item in cookies
        if item["domain"].lower() == ".toutiao.com" and item["path"] == "/"
    ])


def _complete_browser_login(playwright, auth_class, timeout_seconds: int):
    browser = playwright.chromium.launch(headless=False)
    try:
        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(CREATOR_LOGIN_URL, wait_until="domcontentloaded")
            initial_cookies = context.cookies(CREATOR_HOME_URL)
            print("请在新开的 Chromium 窗口用今日头条 App 扫码并确认；成功后进入创作者首页。")
            page.wait_for_url(
                CREATOR_HOME_PATTERN,
                wait_until="domcontentloaded",
                timeout=timeout_seconds * 1000,
            )

            # GET 创作者首页，只读检查扫码后是否仍被重定向到登录页。
            page.goto(CREATOR_HOME_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)  # 留出时间让前端执行登录态重定向。
            if not _is_creator_home(page.url):
                raise BrowserLoginError("Creator homepage redirected away after QR login")
            creator_cookies = context.cookies(CREATOR_HOME_URL)
            before = {_cookie_identity(cookie) for cookie in initial_cookies}
            after = {_cookie_identity(cookie) for cookie in creator_cookies}
            if not creator_cookies or not after.difference(before):
                raise BrowserLoginError("No new Creator cookies after QR login")

            auth = auth_class()
            auth.prepare_creator_auth(_cookie_header(creator_cookies), verified=True)
            shared = _read_shared_cookies(creator_cookies)
            if shared:
                auth.prepare_auth(shared)
            return auth
        finally:
            context.close()
    finally:
        browser.close()


def browser_qrcode_login(auth_class, *, timeout_seconds: int = 300):
    """启动新 Chromium 实例；平台网页自行轮询二维码状态。"""
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Browser QR login requires playwright; install requirements-browser.txt and chromium"
        ) from exc
    with sync_playwright() as playwright:
        try:
            return _complete_browser_login(playwright, auth_class, timeout_seconds)
        except PlaywrightTimeoutError:
            raise BrowserLoginError("QR login or Creator homepage timed out") from None
