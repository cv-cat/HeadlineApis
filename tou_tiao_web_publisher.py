"""头条号纯 HTTP 发布。

2026-11-20 实测闭环（``headline_dianping_closure_20261118/tt_mssdk``）：

1. ``gen_mstoken.js`` 在 Node 沙箱内运行 bdms.js VMP（滚动 XOR + raw deflate
   解出的字节码），``bdms.init({aid:1231,pageId:25897,paths})`` 触发环境采集，
   沙箱 XHR 拦截到 ``POST mssdk.bytedance.com/web/r/token`` 的 strData。
2. 把 strData 原样 POST 给 mssdk，响应头 ``x-ms-token`` 即 msToken。
3. ``static/dy.js`` 纯算 a_bogus（query 含新 msToken + POST body）。
4. Go+utls（Chrome131 ClientHello + 手工 H2 帧）重放 ``POST /mp/agw/article/publish``，
   ``save=1`` 正式发布 ``code=0``；连发三篇 + HeadlineApis 前台回读命中。

``save=0``（存草稿）被 7050 单点门禁拦截（草稿端点状态，与账号信誉、TLS 栈
无关——CDP 页内 fetch 同刻 A/B 实证）；``5009`` 表示 body 残留旧 ``pgc_id``，
本实现固定发送空 ``pgc_id``。

本模块不内嵌 Node/Go 工具链：调用方通过 ``MsTokenProvider`` 与
``PublishTransport`` 注入，便于测试与替换。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode, urlparse

class PublishError(RuntimeError):
    """发布失败；保留平台 code，不含账号资料。"""

    def __init__(self, code: object, message: str = ""):
        self.code = int(code) if str(code).lstrip("-").isdigit() else "unknown"
        text = f"Toutiao web publish code={self.code}"
        if message:
            text = f"{text}: {message}"
        super().__init__(text)


class MsTokenProvider:
    """msToken 供给：外部注入生成函数（Node 沙箱 + mssdk r/token）。"""

    def __init__(self, fetch_token: Callable[[], str]):
        if not callable(fetch_token):
            raise TypeError("fetch_token must be callable returning the msToken string")
        self._fetch_token = fetch_token

    def token(self) -> str:
        value = self._fetch_token()
        if not isinstance(value, str) or not value:
            raise ValueError("msToken provider returned an empty value")
        return value


class PublishTransport:
    """HTTP 传输层：外部注入（utls 重放器或任何能通过发布门禁的栈）。"""

    def __init__(self, send: Callable[[str, str, str], dict]):
        """``send(url, body, referer) -> response dict（含 code/message/data）。"""
        if not callable(send):
            raise TypeError("send must be callable")
        self._send = send

    def post(self, url: str, body: str, referer: str) -> dict:
        payload = self._send(url, body, referer)
        if not isinstance(payload, Mapping):
            raise ValueError("publish transport must return a mapping")
        return dict(payload)

    def post_json(self, url: str, body: str, referer: str) -> dict:
        """JSON body 的 POST。默认复用 send；实现方可按 Content-Type 分流。"""
        return self.post(url, body, referer)


PUBLISH_QUERY = {
    "source": "mp",
    "type": "article",
    "aid": "1231",
    "mp_publish_ab_val": "0",
}
PUBLISH_PATH = "/mp/agw/article/publish"
WTT_PATH = "/mp/agw/article/wtt"
UGCDRAFT_PATH = "/mp/agw/draft/save_ugc_draft"
WTT_QUERY = {
    "source": "mp",
    "type": "wtt",
    "aid": "1231",
    "mp_publish_ab_val": "0",
}


class TouTiaoWebPublisher:
    """组合 msToken + a_bogus + 传输层的纯 HTTP 发布器。"""

    BASE_URL = "https://mp.toutiao.com"
    REFERER = f"{BASE_URL}/profile_v4/graphic/publish"

    def __init__(self, transport: PublishTransport, *, a_bogus_fn: Callable[[str, str], str]):
        self.transport = transport
        if not callable(a_bogus_fn):
            raise TypeError("a_bogus_fn must be callable(query, body) -> a_bogus")
        self._a_bogus_fn = a_bogus_fn
        self._token_provider: MsTokenProvider | None = None

    def with_token_provider(self, provider: MsTokenProvider) -> "TouTiaoWebPublisher":
        self._token_provider = provider
        return self

    @staticmethod
    def build_body(title: str, content_html: str, *, save: str = "1") -> str:
        """27 字段 publish 契约 body（urlencode）。"""
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be non-empty")
        if not isinstance(content_html, str) or not content_html.strip():
            raise ValueError("content_html must be non-empty")
        if save not in {"0", "1"}:
            raise ValueError("save must be '0' (draft) or '1' (publish)")
        extra = json.dumps(
            {
                "content_source": 100000000402,
                "content_word_cnt": len(content_html),
                "is_multi_title": 0,
                "sub_titles": [],
                "gd_ext": {
                    "entrance": "",
                    "from_page": "publisher_mp",
                    "enter_from": "PC",
                    "device_platform": "mp",
                    "is_message": 0,
                },
                "tuwen_wtt_transfer_switch": "1",
            },
            separators=(",", ":"),
        )
        fields = [
            ("pgc_id", ""),
            ("source", "29"),
            ("extra", extra),
            ("content", content_html),
            ("title", title.strip()),
            ("search_creation_info", '{"searchTopOne": 0, "abstract": "", "clue_id": ""}'),
            ("title_id", f"{int(time.time() * 1000)}_"),
            ("mp_editor_stat", "{}"),
            ("is_refute_rumor", "0"),
            ("save", save),
            ("entrance", ""),
            ("timer_status", "0"),
            ("timer_time", ""),
            ("educluecard", ""),
            ("draft_form_data", '{"coverType": 2}'),
            ("pgc_feed_covers", "[]"),
            ("article_ad_type", "3"),
            ("is_fans_article", "0"),
            ("govern_forward", "0"),
            ("praise", "0"),
            ("disable_praise", "0"),
            ("tree_plan_article", "0"),
            ("star_order_id", ""),
            ("star_order_name", ""),
            ("activity_tag", "0"),
            ("trends_writing_tag", "0"),
            ("claim_exclusive", "0"),
        ]
        return urlencode(fields)

    def publish(self, title: str, content_html: str, *, save: str = "1") -> dict:
        """发布（save='1'）或存草稿（save='0'，可能被 7050 门禁拒绝）。"""
        body = self.build_body(title, content_html, save=save)
        query = dict(PUBLISH_QUERY)
        if self._token_provider is not None:
            query["msToken"] = self._token_provider.token()
        query_str = urlencode(query)
        a_bogus = self._a_bogus_fn(query_str, body)
        if not isinstance(a_bogus, str) or not a_bogus:
            raise ValueError("a_bogus function returned an empty value")
        url = f"{self.BASE_URL}{PUBLISH_PATH}?{query_str}&a_bogus={urlencode({'a_bogus': a_bogus})[len('a_bogus='):]}"
        payload = self.transport.post(url, body, self.REFERER)
        code = str(payload.get("code"))
        if code != "0":
            raise PublishError(payload.get("code"), str(payload.get("message") or ""))
        data = payload.get("data")
        if not isinstance(data, Mapping) or not data.get("pgc_id"):
            raise ValueError("publish response has no pgc_id")
        return dict(data)

    def publish_weitoutiao(self, content: str, *, save: str = "1") -> dict:
        """发微头条（2026-11-20 实测 code=0，返回 thread_id）。

        与图文发布共用 token/传输层；body 只需 ``content/save/source``。
        """
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be non-empty")
        if save not in {"0", "1"}:
            raise ValueError("save must be '0' (draft) or '1' (publish)")
        body = urlencode([("content", content.strip()), ("save", save), ("source", "mp")])
        query = dict(WTT_QUERY)
        if self._token_provider is not None:
            query["msToken"] = self._token_provider.token()
        query_str = urlencode(query)
        a_bogus = self._a_bogus_fn(query_str, body)
        if not isinstance(a_bogus, str) or not a_bogus:
            raise ValueError("a_bogus function returned an empty value")
        url = f"{self.BASE_URL}{WTT_PATH}?{query_str}&a_bogus={urlencode({'a_bogus': a_bogus})[len('a_bogus='):]}"
        referer = f"{self.BASE_URL}/profile_v4/weibogroup/publish"
        payload = self.transport.post(url, body, referer)
        code = str(payload.get("code"))
        if code != "0":
            raise PublishError(payload.get("code"), str(payload.get("message") or ""))
        data = payload.get("data")
        if not isinstance(data, Mapping) or not data.get("thread_id"):
            raise ValueError("weitoutiao response has no thread_id")
        return dict(data)

    def save_weitoutiao_draft(self, content: str, *, images: list[str] | None = None) -> str:
        """存微头条草稿（POST /mp/agw/draft/save_ugc_draft，2026-11-20 实测 code=0）。

        body 是 JSON ``{"draft_type": 1, "draft_info": "<JSON字符串>", "post_id": ""}``；
        ``draft_type=1`` 即微头条（Weitoutiao 枚举），``draft_info`` 内层是
        ``{schema: "sslocal://send_thread", content, images, update_time: <unix秒>}``。
        返回新草稿 gid。
        """
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be non-empty")
        draft_info = {
            "schema": "sslocal://send_thread",
            "content": content.strip(),
            "images": list(images or []),
            "update_time": int(time.time()),
        }
        body = json.dumps(
            {"draft_type": 1, "draft_info": json.dumps(draft_info, ensure_ascii=False), "post_id": ""},
            ensure_ascii=False,
        )
        query = {"mp_publish_ab_val": "0"}
        if self._token_provider is not None:
            query["msToken"] = self._token_provider.token()
        query_str = urlencode(query)
        a_bogus = self._a_bogus_fn(query_str, body)
        if not isinstance(a_bogus, str) or not a_bogus:
            raise ValueError("a_bogus function returned an empty value")
        url = (
            f"{self.BASE_URL}{UGCDRAFT_PATH}?{query_str}"
            f"&a_bogus={urlencode({'a_bogus': a_bogus})[len('a_bogus='):]}"
        )
        referer = f"{self.BASE_URL}/profile_v4/weibogroup/publish"
        payload = self.transport.post_json(url, body, referer)
        code = str(payload.get("code"))
        if code != "0":
            raise PublishError(payload.get("code"), str(payload.get("message") or ""))
        gid = payload.get("gid") or (payload.get("data") or {}).get("gid")
        if not gid:
            raise ValueError("ugc draft response has no gid")
        return str(gid)
