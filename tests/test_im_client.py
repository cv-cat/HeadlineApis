import threading
import unittest
from collections.abc import Callable
from typing import Any

from tou_tiao_im import (
    FrontierClient,
    ImCmd,
    ImCredentialProvider,
    ImError,
    TouTiaoImApi,
    compute_access_key,
    deep_parse,
    json_dumps_text,
    parse_pb,
    pbm,
    pbi,
    pbi_raw,
    pbs,
    pbs_raw,
)


class AccessKeyTests(unittest.TestCase):
    def test_matches_live_capture(self):
        # 2026-11-20 CDP 抓包全等实证（frontier_findings.md）。
        self.assertEqual(
            compute_access_key("1817388559263755"),
            "fd03ed499f3e940f188a4079c4b47b1d",
        )


class ConversationBodyTests(unittest.TestCase):
    def test_2006_body_contract(self):
        # 2026-11-20 live 实证：sort_type=0 (f1 省略), cursor=0 (f2 省略), limit=20 (f4)。
        body = pbi(1, 0) + pbi(2, 0) + pbi(4, 20)
        self.assertEqual(body, b" \x14")


class ProtobufCodecTests(unittest.TestCase):
    def test_varint_roundtrip_and_omission(self):
        self.assertEqual(pbi(1, 0), b"")          # proto3 0 省略
        self.assertEqual(pbi_raw(1, 0), b"\x08\x00")  # raw 保留 0
        self.assertEqual(pbi(1, 300), b"\x08\xac\x02")
        self.assertEqual(parse_pb(b"\x08\xac\x02"), {1: 300})

    def test_string_fields(self):
        self.assertEqual(pbs(4, ""), b"")
        self.assertEqual(pbs_raw(21, ""), b"\xaa\x01\x00")
        self.assertEqual(pbs(3, "0.7.0"), b"\x1a\x05" b"0.7.0")
        self.assertEqual(parse_pb(pbs(3, "0.7.0")), {3: "0.7.0"})

    def test_bytes_field_and_nested_parse(self):
        raw = pbm(8, b"\x08\xee\x0f")
        parsed = parse_pb(raw)
        self.assertEqual(parsed[8], b"\x08\xee\x0f")
        deep = deep_parse(raw)
        self.assertEqual(deep, {8: {1: 2030}})

    def test_deep_parse_handles_2022_old_conversation(self):
        # live 帧片段：Request{8: RequestBody{2030: {1: 1}}}
        outer = pbm(8, pbm(2030, pbi_raw(1, 1)))
        self.assertEqual(deep_parse(outer), {8: {2030: {1: 1}}})


class FrameConstructionTests(unittest.TestCase):
    def _client(self):
        client = FrontierClient.__new__(FrontierClient)
        client.token = "tok"
        client.device_id = "1817388559263755"
        client.access_key = compute_access_key(client.device_id)
        client.seq = 10001
        client.log = lambda *a: None
        client.sock = None
        client._pending = {}
        client._push_handlers = []
        client._recv_thread = None
        client._hb_thread = None
        client._closed = threading.Event()
        client._lock = threading.Lock()
        return client

    def test_request_frame_three_layers(self):
        client = self._client()
        sent: list[bytes] = []
        client.sock = types_simple_namespace(sendall=sent.append)

        real_wait = threading.Event.wait

        threading.Event.wait = lambda self, timeout=None: False
        try:
            with self.assertRaises(TimeoutError):
                client.request(ImCmd.GET_UNREAD_COUNT, 2030, pbi_raw(1, 1), timeout=0.001)
        finally:
            threading.Event.wait = real_wait
        # _ws_send 做客户端掩码；解开掩码还原明文帧。
        raw = sent[0]
        self.assertEqual(raw[0] & 0x0F, 2)  # binary opcode
        payload_len = raw[1] & 0x7F
        offset = 2
        if payload_len == 126:
            payload_len = int.from_bytes(raw[2:4], "big")
            offset = 4
        elif payload_len == 127:
            payload_len = int.from_bytes(raw[2:10], "big")
            offset = 10
        mask = raw[offset : offset + 4]
        payload = raw[offset + 4 : offset + 4 + payload_len]
        frame = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        parsed = parse_pb(frame)
        self.assertEqual(parsed[3], 2)                 # service
        self.assertEqual(parsed[4], 1)                 # method
        self.assertEqual(parsed[7], "pb")               # payload_type
        request = parse_pb(parsed[8])
        self.assertEqual(request[1], 2030)             # cmd
        self.assertEqual(request[2], 10002)            # sequence_id
        self.assertEqual(request[3], "0.7.0")          # sdk_version
        self.assertEqual(request[4], "tok")            # token
        self.assertEqual(request[9], "1817388559263755")  # device_id
        self.assertEqual(request[11], "web")           # device_platform
        # RequestBody oneof: {2030: {1: 1}}
        body_field = request.get(8)
        self.assertIsInstance(body_field, (bytes, bytearray))
        body_parsed = deep_parse(body_field)
        self.assertEqual(body_parsed, {2030: {1: 1}})

    def test_request_rejects_unknown_cmd(self):
        client = self._client()
        with self.assertRaises(ValueError):
            client.request(99999, 99999, b"")


class ImErrorTests(unittest.TestCase):
    def test_message_and_fields(self):
        err = ImError(2031, 503, "invalid request")
        self.assertEqual(err.cmd, 2031)
        self.assertEqual(err.status, 503)
        self.assertEqual(str(err), "frontier cmd=2031 status=503: invalid request")


class CredentialProviderTests(unittest.TestCase):
    def test_valid_credentials(self):
        provider = ImCredentialProvider(lambda: ("tok", "123"))
        self.assertEqual(provider.credentials(), ("tok", "123"))

    def test_rejects_empty_token_or_media_id(self):
        provider = ImCredentialProvider(lambda: ("", "123"))
        with self.assertRaises(ValueError):
            provider.credentials()
        provider = ImCredentialProvider(lambda: ("tok", " "))
        with self.assertRaises(ValueError):
            provider.credentials()

    def test_rejects_non_callable(self):
        with self.assertRaises(TypeError):
            ImCredentialProvider("not-callable")


class TouTiaoImApiTests(unittest.TestCase):
    def _api(self, client):
        class _Factory:
            def __call__(self, token, media_id, **kwargs):
                self.created = (token, media_id)
                return client

        factory = _Factory()
        api = TouTiaoImApi(ImCredentialProvider(lambda: ("tok", "123")), client_factory=factory)
        return api, factory

    def test_send_text_wraps_content_json(self):
        sent: list[tuple[int, int, bytes]] = []

        class _Client:
            def __init__(self):
                self.calls: list[Any] = []

            def connect(self):
                self.calls.append("connect")

            def close(self):
                self.calls.append("close")

            def get_ticket(self, **kwargs):
                self.calls.append(("ticket", kwargs))
                return {2005: {1: "TICKET123"}}

            def send_message(self, **kwargs):
                sent.append((kwargs["conversation_id"], kwargs["content"], kwargs["ticket"]))
                return {"100": {"1": "ok"}}

        client = _Client()
        api, factory = self._api(client)
        result = api.send_text(
            "0:1:3615613886:1495795874410859",
            "你好",
            short_id=7059270313183379719,
            to_id=3615613886,
        )
        self.assertEqual(result, {"100": {"1": "ok"}})
        self.assertEqual(
            sent,
            [
                (
                    "0:1:3615613886:1495795874410859",
                    '{"text":"你好"}',
                    "TICKET123",
                )
            ],
        )
        self.assertEqual(factory.created, ("tok", "123"))
        api.close()

    def test_send_text_with_short_id_requires_to_id(self):
        client = object()
        api, _ = self._api(client)
        with self.assertRaises(ValueError):
            api.send_text("conv", "hi", short_id=123)

    def test_send_text_without_short_id_skips_ticket(self):
        class _Client:
            def connect(self):
                pass

            def close(self):
                pass

            def get_ticket(self, **kwargs):
                raise AssertionError("ticket should not be requested")

            def send_message(self, **kwargs):
                return {"100": {}}

        client = _Client()
        api, _ = self._api(client)
        result = api.send_text("conv", "hi")
        self.assertEqual(result, {"100": {}})

    def test_send_message_m20_throttle_raises(self):
        # 2026-11-20 live：对未回复陌生人会话第二条起 body={3:3,4:client_id,5:109,6:提示}
        client = FrameConstructionTests._client(self)
        # 响应：Response{6: {100: {3:3, 4:cid, 5:109, 6:msg}}}
        m20_body = pbm(100, pbi_raw(3, 3) + pbs(4, "cid1") + pbi(5, 109) + pbs(6, "你已经发过消息啦"))

        original_request = FrontierClient.request

        def fake_request(self, cmd, body_field, body, *, timeout=8):
            if cmd == ImCmd.GET_TICKET:
                return {6: pbm(2005, pbs(1, "TICKET"))}
            return {1: cmd, 3: 0, 6: m20_body}

        FrontierClient.request = fake_request
        try:
            with self.assertRaises(ImError) as ctx:
                client.send_message(conversation_id="conv", content='{"text":"hi"}', ticket="T", short_id=7)
            self.assertEqual(ctx.exception.status, 109)
        finally:
            FrontierClient.request = original_request

    def test_send_text_rejects_empty_text(self):
        client = object()
        api, _ = self._api(client)
        with self.assertRaises(ValueError):
            api.send_text("conv", "  ")

    def test_requires_credential_provider(self):
        with self.assertRaises(TypeError):
            TouTiaoImApi("not-a-provider")


class JsonTextContractTests(unittest.TestCase):
    def test_content_json_shape(self):
        self.assertEqual(json_dumps_text("test"), '{"text":"test"}')

    def test_content_json_preserves_unicode(self):
        self.assertEqual(json_dumps_text("橘子"), '{"text":"橘子"}')


def types_simple_namespace(**kwargs):
    import types

    return types.SimpleNamespace(**kwargs)


if __name__ == "__main__":
    unittest.main()


class HandshakeHeaderTests(unittest.TestCase):
    """握手头按浏览器原序全量（webSocketWillSendHandshakeRequest 实测）。"""

    def _capture_handshake(self):
        client = FrameConstructionTests._client(self)
        sent: list[bytes] = []

        import ssl as _ssl

        class _FakeSock:
            def sendall(self, data):
                sent.append(bytes(data))

            def recv(self, n):
                return b"HTTP/1.1 101 Switching Protocols\r\n\r\n"

        real_conn = FrontierClient.connect
        import socket as _socket

        def fake_connect(self, timeout=10):
            self.sock = _FakeSock()
            import base64 as _b64
            import os as _os

            key_b64 = _b64.b64encode(_os.urandom(16)).decode()
            from tou_tiao_im import ORIGIN, USER_AGENT, WS_PROTOCOLS, FRONTIER_HOST

            self.sock.sendall(
                (
                    f"GET /ws/v2?token={self.token} HTTP/1.1\r\n"
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
            resp = b""
            while b"\r\n\r\n" not in resp:
                resp += self.sock.recv(4096)
            self._recv_thread = threading.Thread(target=lambda: None, daemon=True)
            self._hb_thread = threading.Thread(target=lambda: None, daemon=True)

        FrontierClient.connect = fake_connect
        try:
            client.connect()
        finally:
            FrontierClient.connect = real_conn
        return sent[0].decode("utf-8", "replace")

    def test_handshake_has_full_browser_headers(self):
        raw = self._capture_handshake()
        for header in (
            "Origin: https://mp.toutiao.com",
            "Cache-Control: no-cache",
            "Accept-Language: zh-CN,zh;q=0.9",
            "Pragma: no-cache",
            "Accept-Encoding: gzip, deflate, br, zstd",
            "Sec-WebSocket-Extensions: permessage-deflate; client_max_window_bits",
            "Sec-WebSocket-Protocol: binary, base64, pbbp2",
        ):
            self.assertIn(header, raw, f"missing header: {header}")


class CreateConversationTests(unittest.TestCase):
    def test_body_contract_two_participants(self):
        client = FrameConstructionTests._client(self)
        sent: list[bytes] = []
        client.sock = types_simple_namespace(sendall=sent.append)

        original_request = FrontierClient.request

        def fake_request(self, cmd, body_field, body, *, timeout=8):
            self._last_body = body
            return {1: cmd, 3: 0, 6: pbm(609, b"\x0a\x30conv")}

        FrontierClient.request = fake_request
        try:
            client.create_conversation(conversation_type=1, participants=[3615613886, 1495795874410859])
        finally:
            FrontierClient.request = original_request
        body = client._last_body
        parsed = parse_pb(body)
        # f1=type(1), f2=participants repeated, f3=persistent(1), f4=idempotent_id
        self.assertEqual(parsed[1], 1)
        self.assertEqual(parsed[2], [3615613886, 1495795874410859])
        self.assertEqual(parsed[3], 1)
        self.assertIn(4, parsed)

    def test_rejects_wrong_participant_count(self):
        client = FrameConstructionTests._client(self)
        with self.assertRaises(ValueError):
            client.create_conversation(participants=[1])
        with self.assertRaises(ValueError):
            client.create_conversation(participants=[1, 2, 3])

    def test_rejects_non_one_to_one(self):
        client = FrameConstructionTests._client(self)
        with self.assertRaises(ValueError):
            client.create_conversation(conversation_type=2, participants=[1, 2])
