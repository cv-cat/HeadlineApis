"""头条号私信（mp.toutiao.com IM）纯 Python 客户端。

2026-11-20 实测闭环（``headline_dianping_closure_20261118/tt_mssdk``，
详见 ``frontier_findings.md``）：私信走 ``frontier100-normal.zijieapi.com``
的 WebSocket（协议 pbbp2 = 三层手写 protobuf），不走 REST。

- WS URL: ``wss://frontier100-normal.zijieapi.com/ws/v2``
  ``?token=<mp.toutiao.com/ugc/im/v1/get_token>`` ``&aid=1231&fpid=89``
  ``&device_id=<media_id>`` ``&access_key=md5("89"+appKey+device_id+salt)``
  ``&device_platform=web``；握手必须带 Origin 与浏览器 UA，否则服务端
  握手后 1-2 秒内主动 close。
- 帧封装（全部手写 protobuf 编解码，零第三方依赖）::

      Frame   {1 seqid, 2 logid(ms), 3 service=2, 4 method=1,
              7 payload_type="pb", 8 payload}
      Request {1 cmd, 2 sequence_id, 3 sdk_version="0.7.0", 4 token,
              5 refer=3, 6 inbox_type=0, 7 build_number,
              8 body(RequestBody), 9 device_id, 11 device_platform="web",
              18 auth_type=2, 21 biz="", 22 access="web_sdk"}
      Response {1 cmd, 2 sequence_id, 3 status_code(0=OK), 4 error_desc,
              6 body(ResponseBody), 7 log_id}

  RequestBody 是 oneof：业务体挂在 IMCMD 同号字段（cmd=1002 用字段 1001 的
  ``cmd != oneof 字段号`` 例外已逐个对表）。
- 心跳：文本帧 ``"hi"`` 每 15 秒；服务端回 ``"hi"`` 并以 30 秒 ping-interval
  探活。
- 文本消息契约（mp_im_runtime.js UI 层）：
  ``message_type=7``（LEGACY_MESSAGE_TYPE_TEXT），
  ``content=JSON.stringify({"text": ...})``——必须是 JSON 字符串。

已知边界：``websocket-client`` 库把 pbbp2 binary 响应当 text 强解 utf8 抛
``WebSocketPayloadException``，故本模块用 raw socket 自己收帧。

凭据注入：``ImCredentialProvider`` 供给 (token, media_id)，可由页面
``GET /ugc/im/v1/get_token`` + ``/mp/agw/profile`` 的 ``media_id`` 实现；
本模块不内嵌取数逻辑，便于测试与替换。
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

APP_KEY = "e0f82475ab9dbf5717d18b4a9c0d7fd0"
ACCESS_KEY_SALT = "f8a69f1719916z"
FP_ID = 89
AID = 1231
FRONTIER_HOST = "frontier100-normal.zijieapi.com"
SDK_VERSION = "0.7.0"
BUILD_NUMBER = "5c9b17f:master"
WS_PROTOCOLS = "binary, base64, pbbp2"
HEARTBEAT_INTERVAL = 15
ORIGIN = "https://mp.toutiao.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
)


class ImError(RuntimeError):
    """frontier 响应错误；保留 cmd/status，不含账号资料。"""

    def __init__(self, cmd: object, status: object, message: str = ""):
        self.cmd = cmd
        self.status = status if isinstance(status, int) else -1
        text = f"frontier cmd={cmd} status={self.status}"
        if message:
            text = f"{text}: {message}"
        super().__init__(text)


class ImCredentialProvider:
    """(token, media_id) 供给：外部注入生成函数。

    token 来自 ``GET https://mp.toutiao.com/ugc/im/v1/get_token``
    （登录 Cookie 会话，响应 ``token`` 字段，56 字节，短时效）；
    media_id 即账号媒体 ID（``/mp/agw/profile`` 的 ``media_id``，
    也是 frontier URL 的 device_id）。
    """

    def __init__(self, fetch: Callable[[], tuple[str, str]]):
        if not callable(fetch):
            raise TypeError("fetch must be callable returning (token, media_id)")
        self._fetch = fetch

    def credentials(self) -> tuple[str, str]:
        token, media_id = self._fetch()
        if not isinstance(token, str) or not token:
            raise ValueError("IM credential provider returned an empty token")
        if not isinstance(media_id, str) or not media_id.strip():
            raise ValueError("IM credential provider returned an empty media_id")
        return token, media_id


def compute_access_key(device_id: str, *, app_key: str = APP_KEY) -> str:
    """``access_key = md5(fpId + appKey + device_id + salt)``（2026-11-20 全等实证）。"""
    return hashlib.md5(f"{FP_ID}{app_key}{device_id}{ACCESS_KEY_SALT}".encode()).hexdigest()


# ---------------- protobuf 手写编解码 ----------------

def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(field: int, wt: int) -> bytes:
    return _varint(field << 3 | wt)


def pbi(field: int, value: int) -> bytes:
    """proto3 int：0 省略。"""
    if value == 0:
        return b""
    return _key(field, 0) + _varint(value)


def pbi_raw(field: int, value: int) -> bytes:
    """proto3 int：0 也显式发送（SDK 行为）。"""
    return _key(field, 0) + _varint(value)


def pbs(field: int, value: str) -> bytes:
    if not value:
        return b""
    raw = value.encode()
    return _key(field, 2) + _varint(len(raw)) + raw


def pbs_raw(field: int, value: str) -> bytes:
    if not value:
        return _key(field, 2) + b"\x00"
    raw = value.encode()
    return _key(field, 2) + _varint(len(raw)) + raw


def pbm(field: int, payload: bytes) -> bytes:
    return _key(field, 2) + _varint(len(payload)) + payload


class PbReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def _varint(self) -> int:
        result = shift = 0
        while True:
            b = self.data[self.pos]
            self.pos += 1
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7

    def fields(self):
        while self.pos < len(self.data):
            k = self._varint()
            field, wt = k >> 3, k & 7
            if field == 0 or wt not in (0, 1, 2, 5):
                return
            if wt == 0:
                yield field, 0, self._varint()
            elif wt == 2:
                ln = self._varint()
                yield field, 2, self.data[self.pos : self.pos + ln]
                self.pos += ln
            elif wt == 5:
                yield field, 5, self.data[self.pos : self.pos + 4]
                self.pos += 4
            else:
                yield field, 1, self.data[self.pos : self.pos + 8]
                self.pos += 8


def parse_pb(data: bytes) -> dict:
    """protobuf -> ``{field: value}``；重复字段保留 list。bytes 尝试 utf-8。"""
    out: dict[int, list] = {}
    for field, wt, value in PbReader(data).fields():
        if wt == 2:
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                pass
        out.setdefault(field, []).append(value)
    return {k: (v[0] if len(v) == 1 else v) for k, v in out.items()}


def deep_parse(data: bytes, max_depth: int = 6) -> dict:
    """递归解析嵌套 protobuf -> ``{field: value/嵌套dict}``。"""
    def walk(buf, depth):
        out = {}
        for field, wt, value in PbReader(buf).fields():
            if wt == 2 and isinstance(value, (bytes, bytearray)):
                try:
                    text = value.decode("utf-8")
                    printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
                    ratio = printable / max(len(text), 1)
                except UnicodeDecodeError:
                    ratio = 0
                if ratio > 0.85 or depth >= max_depth:
                    out[field] = text if ratio > 0.85 else value.hex()
                else:
                    out[field] = walk(value, depth + 1)
            else:
                out[field] = value
        return out
    return walk(data, 0)


class ImCmd:
    """已实证的 frontier 命令号（oneof 字段号见 ``_ONEOF``）。"""

    SEND_MESSAGE = 100
    GET_MESSAGES_BY_USER = 200
    NEW_MSG_NOTIFY = 500
    NEW_P2P_MSG_NOTIFY = 504
    GET_MESSAGES_BY_CONVERSATION = 301
    CREATE_CONVERSATION_V2 = 609
    GET_CONVERSATION_INFO_LIST_V2 = 610
    GET_TICKET = 2005
    GET_USER_CONVERSATION_LIST = 2006
    GET_UNREAD_COUNT = 2030
    SEND_MESSAGE_P2P = 2031
    GET_STRANGER_CONVERSATION_LIST = 1000
    GET_STRANGER_MESSAGES = 1002

    #: cmd -> RequestBody oneof 字段号（cmd≠oneof 的例外逐个对表）。
    _ONEOF = {
        SEND_MESSAGE: 100,
        GET_MESSAGES_BY_CONVERSATION: 301,
        CREATE_CONVERSATION_V2: 609,
        GET_TICKET: 2005,
        GET_UNREAD_COUNT: 2030,
        GET_CONVERSATION_INFO_LIST_V2: 610,
        GET_STRANGER_CONVERSATION_LIST: 1000,
        GET_STRANGER_MESSAGES: 1001,
        GET_USER_CONVERSATION_LIST: 2006,
    }


#: 响应 body 内业务体的字段号（与 oneof 字段号一致，除 stranger 系）。
_RESPONSE_FIELD = {
    ImCmd.GET_STRANGER_MESSAGES: 1001,
}


class FrontierClient:
    """frontier WS 客户端：raw socket + 手写 pbbp2 帧，线程安全。"""

    def __init__(self, token: str, device_id: str, *, log: Callable[..., None] | None = None):
        if not isinstance(token, str) or not token:
            raise ValueError("token must be a non-empty string")
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id must be a non-empty string")
        self.token = token
        self.device_id = device_id
        self.access_key = compute_access_key(device_id)
        self.seq = 10001
        self.log = log or (lambda *a: None)
        self.sock: ssl.SSLSocket | None = None
        self._pending: dict[int, dict] = {}
        self._push_handlers: list[Callable[[dict, dict], None]] = []
        self._recv_thread: threading.Thread | None = None
        self._hb_thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._lock = threading.Lock()

    # -- 连接 --

    def connect(self, timeout: float = 10) -> None:
        """建立 WS 连接并启动收帧/心跳线程。失败抛 ConnectionError。"""
        if self.sock is not None:
            raise RuntimeError("client already connected")
        path = (
            f"/ws/v2?token={self.token}&aid={AID}&fpid={FP_ID}"
            f"&device_id={self.device_id}&access_key={self.access_key}"
            "&device_platform=web"
        )
        raw = socket.create_connection((FRONTIER_HOST, 443), timeout=timeout)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.sock = ctx.wrap_socket(raw, server_hostname=FRONTIER_HOST)
        key_b64 = base64.b64encode(os.urandom(16)).decode()
        # 握手头按浏览器原序全量发送（Network.webSocketWillSendHandshakeRequest
        # 实测抓包；服务端对 Origin/UA 等头敏感，缺头会被环境风控踢线）。
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\n"
                "Upgrade: websocket\r\n"
                f"Origin: {ORIGIN}\r\n"
                "Cache-Control: no-cache\r\n"
                "Accept-Language: zh-CN,zh;q=0.9\r\n"
                "Pragma: no-cache\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key_b64}\r\n"
                "Accept-Encoding: gzip, deflate, br, zstd\r\n"
                f"User-Agent: {USER_AGENT}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"Sec-WebSocket-Protocol: {WS_PROTOCOLS}\r\n"
                f"Host: {FRONTIER_HOST}\r\n"
                "Sec-WebSocket-Extensions: permessage-deflate; client_max_window_bits\r\n"
                "\r\n"
            ).encode()
        )
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("frontier handshake closed")
            response += chunk
        if b"101" not in response.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"frontier handshake refused: {response[:200]!r}")
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        self._hb_thread = threading.Thread(target=self._hb_loop, daemon=True)
        self._hb_thread.start()

    def close(self) -> None:
        self._closed.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self) -> "FrontierClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- 帧收发 --

    def _ws_send(self, data: bytes, opcode: int = 2) -> None:
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode])
        ln = len(data)
        if ln < 126:
            header.append(0x80 | ln)
        elif ln < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", ln)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", ln)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if self.sock is None:
            raise ConnectionError("frontier socket is closed")
        self.sock.sendall(bytes(header) + mask + masked)

    def _ws_recv(self, timeout: float = 3) -> tuple[int, bytes] | None:
        if self.sock is None:
            return None
        self.sock.settimeout(timeout)
        try:
            hdr = b""
            while len(hdr) < 2:
                chunk = self.sock.recv(2 - len(hdr))
                if not chunk:
                    return None
                hdr += chunk
            b1, b2 = hdr
            op = b1 & 0x0F
            ln = b2 & 0x7F
            if ln == 126:
                ext = b""
                while len(ext) < 2:
                    ext += self.sock.recv(2 - len(ext))
                ln = struct.unpack("!H", ext)[0]
            elif ln == 127:
                ext = b""
                while len(ext) < 8:
                    ext += self.sock.recv(8 - len(ext))
                ln = struct.unpack("!Q", ext)[0]
            data = b""
            while len(data) < ln:
                chunk = self.sock.recv(ln - len(data))
                if not chunk:
                    break
                data += chunk
            return op, data
        except socket.timeout:
            return None
        except (ConnectionError, OSError):
            return None

    def _recv_loop(self) -> None:
        while not self._closed.is_set():
            got = self._ws_recv(20)
            if got is None:
                if self._closed.is_set():
                    return
                continue
            op, data = got
            if op == 8:
                self.log("[ws] server close:", data[:40])
                self._fail_all_pending()
                return
            if op in (9, 10):
                if op == 9:
                    self._ws_send(data, opcode=10)
                continue
            if op == 1:
                if data != b"hi":
                    self.log("[ws] text:", data[:60])
                continue
            try:
                self._dispatch(data)
            except Exception as exc:  # noqa: BLE001
                self.log("[decode-error]", exc)

    def _hb_loop(self) -> None:
        while not self._closed.wait(HEARTBEAT_INTERVAL):
            try:
                self._ws_send(b"hi", opcode=1)
            except OSError:
                return

    def _fail_all_pending(self) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for waiter in pending:
            waiter["resp"] = (-1, {"4": "connection closed"})
            waiter["event"].set()

    def _dispatch(self, frame_bytes: bytes) -> None:
        frame = parse_pb(frame_bytes)
        payload = frame.get(8)
        if not isinstance(payload, (bytes, bytearray)):
            return
        resp = parse_pb(payload)
        seq = resp.get(2, 0)
        status = resp.get(3, -1)
        waiter = None
        if isinstance(seq, int) and seq:
            with self._lock:
                waiter = self._pending.pop(seq, None)
        if waiter is not None:
            waiter["resp"] = (status, resp)
            waiter["event"].set()
        else:
            for handler in list(self._push_handlers):
                handler(frame, resp)

    # -- 请求 --

    def request(self, cmd: int, body_field: int, body: bytes, *, timeout: float = 8) -> dict:
        """发送一条业务请求并等待同 sequence_id 的响应。

        非 0 status 抛 :class:`ImError`；返回完整 Response 解析 dict。
        """
        if cmd != ImCmd.SEND_MESSAGE and cmd not in ImCmd._ONEOF:
            raise ValueError(f"unknown frontier cmd: {cmd}")
        with self._lock:
            self.seq += 1
            seq = self.seq
            waiter = {"event": threading.Event(), "resp": None}
            self._pending[seq] = waiter
        request_body = pbm(body_field, body)
        req = (
            pbi(1, cmd)
            + pbi(2, seq)
            + pbs(3, SDK_VERSION)
            + pbs(4, self.token)
            + pbi(5, 3)
            + pbi_raw(6, 0)
            + pbs(7, BUILD_NUMBER)
            + pbm(8, request_body)
            + pbs(9, self.device_id)
            + pbs(11, "web")
            + pbi(18, 2)
            + pbs_raw(21, "")
            + pbs(22, "web_sdk")
        )
        frame = (
            pbi_raw(1, seq)
            + pbi_raw(2, int(time.time() * 1000))
            + pbi(3, 2)
            + pbi(4, 1)
            + pbs(7, "pb")
            + pbm(8, req)
        )
        self._ws_send(frame)
        if not waiter["event"].wait(timeout):
            with self._lock:
                self._pending.pop(seq, None)
            raise TimeoutError(f"frontier cmd={cmd} seq={seq} timeout")
        status, resp = waiter["resp"]
        if status != 0:
            desc = resp.get(4, "")
            raise ImError(cmd, status, desc if isinstance(desc, str) else str(desc))
        return resp

    def on_push(self, handler: Callable[[dict, dict], None]) -> None:
        """注册服务端推送回调 ``(frame, resp)``（NEW_MSG_NOTIFY 等）。"""
        if not callable(handler):
            raise TypeError("push handler must be callable")
        self._push_handlers.append(handler)

    # -- 业务方法 --

    @staticmethod
    def _body(resp: dict, field: int) -> dict:
        inner = resp.get(6)
        parsed = deep_parse(inner) if isinstance(inner, (bytes, bytearray)) else {}
        return parsed.get(field, parsed) if field in parsed else parsed

    def get_unread_count(self) -> dict:
        """GET_UNREAD_COUNT；返回 ``{'total': int, 'raw': ...}``。"""
        resp = self.request(ImCmd.GET_UNREAD_COUNT, ImCmd._ONEOF[ImCmd.GET_UNREAD_COUNT], pbi_raw(1, 1))
        out = self._body(resp, 2030)
        return {"total": out.get(1, 0), "raw": out}

    def get_ticket(self, *, ticket_type: int, conversation_type: int, to_id: int, short_id: int = 0) -> dict:
        """GET_TICKET；会话票据在 body 字段 1（160 字节）。"""
        body = pbi(1, ticket_type) + pbi(2, conversation_type) + pbi(4, to_id) + pbi(5, short_id)
        resp = self.request(ImCmd.GET_TICKET, ImCmd._ONEOF[ImCmd.GET_TICKET], body)
        inner = resp.get(6)
        return deep_parse(inner) if isinstance(inner, (bytes, bytearray)) else {}

    def send_message(
        self,
        *,
        conversation_id: str,
        content: str,
        ticket: str = "",
        short_id: int = 0,
        conversation_type: int = 1,
        message_type: int = 7,
    ) -> dict:
        """会话内发消息（cmd=100，mp 私信页同款路径）。

        ``content`` 是 IM 协议 JSON 字符串（如 ``'{"text":"hello"}'``）；
        ``message_type=7`` 是 LEGACY_MESSAGE_TYPE_TEXT（mp 实用值）。
        2026-11-20 live 实证：发送后 server_message_id 回读命中。

        会话级限频（m20）：对未回复的陌生人会话，首条之后的发送会被拒，
        响应 body 是 ``{3: fail_status, 4: client_id, 5: 109, 6: 提示文本}``
        ——``5:109`` 即 m20（``0x6d`` + varint 后续），此时改抛
        :class:`ImError`（status=109），成功则 body 含 server_message_id。
        """
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id must be non-empty")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be non-empty")
        body = (
            pbs(1, conversation_id)
            + pbi(2, conversation_type)
            + pbi(3, short_id)
            + pbs(4, content)
            + pbi(6, message_type)
            + pbs(7, ticket)
            + pbs(8, uuid.uuid4().hex)
        )
        resp = self.request(ImCmd.SEND_MESSAGE, ImCmd._ONEOF[ImCmd.SEND_MESSAGE], body)
        inner = resp.get(6)
        parsed = deep_parse(inner) if isinstance(inner, (bytes, bytearray)) else {}
        send_body = parsed.get(100, parsed)
        if isinstance(send_body, dict) and send_body.get(3) not in (None, 0) and send_body.get(5) == 109:
            raise ImError(ImCmd.SEND_MESSAGE, send_body.get(5), str(send_body.get(6, "send throttled")))
        return parsed

    def list_conversations(self, *, next_cursor: int = 0, limit: int = 20, inbox_type: int = 0) -> dict:
        """GET_USER_CONVERSATION_LIST（cmd=2006，2026-11-20 live 实证）。

        返回真实会话：conversation_id/short_id/type/ticket 等在
        ``conversation_info_list``（body 字段 1）。
        body 契约：``sort_type=0, cursor=int64, limit=int32``；
        ``limit`` 服务端上限 20（>20 返回空列表）。
        """
        if type(next_cursor) is not int or next_cursor < 0:
            raise ValueError("next_cursor must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit must be an integer from 1 to 20 (server cap)")
        body = pbi(1, 0) + pbi(2, next_cursor) + pbi(4, limit)
        resp = self.request(ImCmd.GET_USER_CONVERSATION_LIST, ImCmd._ONEOF[ImCmd.GET_USER_CONVERSATION_LIST], body)
        inner = resp.get(6)
        return deep_parse(inner) if isinstance(inner, (bytes, bytearray)) else {}

    def list_messages(self, *, conversation_id: str, conversation_type: int = 1, short_id: int = 0, limit: int = 20) -> dict:
        """GET_MESSAGES_BY_CONVERSATION（cmd=301，2026-11-20 live 实证）。

        返回消息历史：content（field 8 的 JSON 文本）、message_type、
        server_message_id 等在 body 的 message 列表里。
        """
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id must be non-empty")
        if type(short_id) is not int or short_id < 0:
            raise ValueError("short_id must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")
        body = (
            pbs(1, conversation_id)
            + pbi(2, conversation_type)
            + pbi(3, short_id)
            + pbi(4, 1)
            + pbi(6, limit)
        )
        resp = self.request(ImCmd.GET_MESSAGES_BY_CONVERSATION, ImCmd._ONEOF[ImCmd.GET_MESSAGES_BY_CONVERSATION], body)
        inner = resp.get(6)
        return deep_parse(inner) if isinstance(inner, (bytes, bytearray)) else {}

    def create_conversation(self, *, conversation_type: int = 1, participants: list[int], persistent: bool = True, idempotent_id: str = "") -> dict:
        """CREATE_CONVERSATION_V2（cmd=609，2026-11-20 live 实证）。

        body：``{conversation_type(f1 int32), participants(f2 repeated
        int64), persistent(f3 bool), idempotent_id(f4 str)}``。
        ONE_TO_ONE（type=1）需要**两个**参与者 uid（本端+对端，
        单参与者会建出隐藏的自会话）。返回 ConversationInfoV2
        （conversation_id/short_id/ticket）。
        """
        if conversation_type != 1:
            raise ValueError("only ONE_TO_ONE (type=1) is verified")
        if not isinstance(participants, list) or len(participants) != 2:
            raise ValueError("ONE_TO_ONE requires exactly 2 participant uids")
        if not all(isinstance(p, int) and p > 0 for p in participants):
            raise ValueError("participants must be positive int uids")
        if not idempotent_id:
            idempotent_id = uuid.uuid4().hex
        body = (
            pbi(1, conversation_type)
            + b"".join(pbi(2, p) for p in participants)
            + pbi(3, 1 if persistent else 0)
            + pbs(4, idempotent_id)
        )
        resp = self.request(ImCmd.CREATE_CONVERSATION_V2, ImCmd._ONEOF[ImCmd.CREATE_CONVERSATION_V2], body)
        inner = resp.get(6)
        return deep_parse(inner) if isinstance(inner, (bytes, bytearray)) else {}

    def list_stranger_conversations(self, *, cursor: int = 0, count: int = 20) -> dict:
        """GET_STRANGER_CONVERSATION_LIST。"""
        if type(count) is not int or not 1 <= count <= 100:
            raise ValueError("count must be an integer from 1 to 100")
        body = pbi(1, cursor) + pbi(2, count)
        resp = self.request(ImCmd.GET_STRANGER_CONVERSATION_LIST, ImCmd._ONEOF[ImCmd.GET_STRANGER_CONVERSATION_LIST], body)
        inner = resp.get(6)
        return deep_parse(inner) if isinstance(inner, (bytes, bytearray)) else {}

    def list_stranger_messages(self, short_id: int, *, server_message_id: int = 0, reset_unread: bool = False) -> dict:
        """GET_STRANGER_MESSAGES（cmd=1002，oneof 字段 1001）。"""
        if type(short_id) is not int or short_id < 0:
            raise ValueError("short_id must be a non-negative integer")
        body = pbi(1, short_id) + pbi(2, 1 if reset_unread else 0)
        resp = self.request(ImCmd.GET_STRANGER_MESSAGES, ImCmd._ONEOF[ImCmd.GET_STRANGER_MESSAGES], body)
        return self._body(resp, _RESPONSE_FIELD[ImCmd.GET_STRANGER_MESSAGES])


class TouTiaoImApi:
    """面向调用方的门面：凭据注入 + 一条 WS 连接复用多个命令。"""

    def __init__(self, credential: ImCredentialProvider, *, client_factory: Callable[..., FrontierClient] | None = None):
        if not isinstance(credential, ImCredentialProvider):
            raise TypeError("credential must be an ImCredentialProvider")
        self._credential = credential
        self._client_factory = client_factory or FrontierClient
        self._client: FrontierClient | None = None

    def _connect(self) -> FrontierClient:
        if self._client is not None:
            return self._client
        token, media_id = self._credential.credentials()
        self._client = self._client_factory(token, media_id)
        self._client.connect()
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "TouTiaoImApi":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- 只读 --

    def unread_count(self) -> dict:
        """未读私信数。"""
        return self._connect().get_unread_count()

    def conversations(self, *, limit: int = 20) -> dict:
        """私信会话列表。"""
        return self._connect().list_conversations(limit=limit)

    def messages(self, conversation_id: str, *, short_id: int = 0, conversation_type: int = 1, limit: int = 20) -> dict:
        """会话消息历史。"""
        return self._connect().list_messages(
            conversation_id=conversation_id,
            conversation_type=conversation_type,
            short_id=short_id,
            limit=limit,
        )

    def stranger_conversations(self, *, count: int = 20) -> dict:
        """陌生人私信会话列表。"""
        return self._connect().list_stranger_conversations(count=count)

    def stranger_messages(self, short_id: int, *, reset_unread: bool = False) -> dict:
        """陌生人会话消息。"""
        return self._connect().list_stranger_messages(short_id, reset_unread=reset_unread)

    # -- 发送 --

    def send_text(
        self,
        conversation_id: str,
        text: str,
        *,
        short_id: int = 0,
        to_id: int = 0,
        conversation_type: int = 1,
    ) -> dict:
        """给既有会话发文本（mp 私信页同款 cmd=100 + type=7 路径）。

        ``content`` 由本方法按 UI 层契约包成 ``{"text": ...}`` JSON。
        ``to_id`` 是对端用户 uid（会话列表 field 50 的 field 12），
        GET_TICKET 需要它（2026-11-20 live：to_id=0 报
        ``get ticket from auth error``）。
        """
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be non-empty")
        if short_id and not to_id:
            raise ValueError("to_id (peer uid) is required with short_id for the ticket")
        client = self._connect()
        ticket = ""
        if short_id:
            ticket_payload = client.get_ticket(
                ticket_type=1,
                conversation_type=conversation_type,
                to_id=to_id,
                short_id=short_id,
            )
            inner = ticket_payload.get(2005, ticket_payload)
            ticket_value = inner.get(1) if isinstance(inner, dict) else None
            if not ticket_value:
                raise ValueError("failed to obtain conversation ticket")
            ticket = str(ticket_value)
        content = json_dumps_text(text)
        return client.send_message(
            conversation_id=conversation_id,
            content=content,
            ticket=ticket,
            short_id=short_id,
            conversation_type=conversation_type,
        )


def json_dumps_text(text: str) -> str:
    """按 mp UI 层契约把纯文本包成 IM content JSON。"""
    import json

    return json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "APP_KEY",
    "AID",
    "FP_ID",
    "FRONTIER_HOST",
    "ImCmd",
    "ImCredentialProvider",
    "ImError",
    "FrontierClient",
    "TouTiaoImApi",
    "compute_access_key",
    "deep_parse",
    "json_dumps_text",
    "parse_pb",
    "pbm",
    "pbi",
    "pbi_raw",
    "pbs",
    "pbs_raw",
]
