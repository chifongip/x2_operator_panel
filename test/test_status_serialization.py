import asyncio
from http import HTTPStatus
import json
from math import inf, isnan, nan
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from x2_operator_panel.panel_server import WebsocketHub, _make_request_handler


def strict_json(payload):
    def reject_constant(value):
        raise ValueError(f"Non-JSON numeric constant: {value}")

    return json.loads(payload, parse_constant=reject_constant)


def canceled_status():
    return {
        "operations": [{
            "id": "dock", "kind": "fine_align", "status": "CANCELED",
            "feedback": {"current_error": {"x": nan, "y": inf, "yaw": -inf}},
            "result": {"final_error": {"x": nan, "y": nan, "yaw": nan}},
        }],
        "task_admission": {"blocked": False},
        "finite_values": [0.0, 1.25, True, "unchanged"],
    }


class StatusSerializationTest(unittest.TestCase):
    def test_http_status_preserves_cancellation_and_unavailable_errors(self):
        handler = object.__new__(_make_request_handler(SimpleNamespace()))
        handler._bytes = Mock()
        snapshot = canceled_status()

        handler._json(HTTPStatus.OK, snapshot)

        status, payload, content_type = handler._bytes.call_args.args
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(content_type, "application/json; charset=utf-8")
        parsed = strict_json(payload)
        self.assertEqual(parsed["operations"][0]["status"], "CANCELED")
        self.assertFalse(parsed["task_admission"]["blocked"])
        self.assertEqual(parsed["operations"][0]["result"]["final_error"],
                         {"x": None, "y": None, "yaw": None})
        self.assertEqual(parsed["operations"][0]["feedback"]["current_error"],
                         {"x": None, "y": None, "yaw": None})
        self.assertEqual(parsed["finite_values"], snapshot["finite_values"])
        self.assertTrue(isnan(snapshot["operations"][0]["result"]["final_error"]["x"]))

    def test_websocket_delta_clears_active_task_and_deduplicates_unavailable_errors(self):
        hub = WebsocketHub(SimpleNamespace())
        hub._loop = Mock()
        hub._clients = {object(): "session"}
        active = {"operations": [{"id": "dock", "status": "ACTIVE"}],
                  "task_admission": {"blocked": True}}
        hub.broadcast(active)
        self.assertEqual(strict_json(hub._latest_payload)["type"], "status")
        hub._broadcast_scheduled = False
        hub._latest_payload = None

        hub.broadcast(canceled_status())

        message = strict_json(hub._latest_payload)
        self.assertEqual(message["type"], "status_delta")
        update = message["payload"]["set"]
        self.assertEqual(update["operations"][0]["status"], "CANCELED")
        self.assertFalse(update["task_admission"]["blocked"])
        self.assertIsNone(update["operations"][0]["result"]["final_error"]["x"])
        hub._latest_payload = None
        hub._broadcast_scheduled = False
        hub._loop.call_soon_threadsafe.reset_mock()
        hub.broadcast(canceled_status())
        self.assertIsNone(hub._latest_payload)
        hub._loop.call_soon_threadsafe.assert_not_called()

    def test_websocket_reconnect_receives_valid_canceled_history(self):
        class Connection:
            request = SimpleNamespace(headers={})
            remote_address = ("127.0.0.1", 1234)
            send = AsyncMock()

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        application = SimpleNamespace(
            source_address_is_allowed=lambda address: True,
            authenticated_session_token=lambda headers: "session",
            websocket_origin_is_allowed=lambda headers: True,
            websocket_client_limit=1, websocket_send_timeout_sec=1.0,
            status=canceled_status,
        )
        connection = Connection()
        asyncio.run(WebsocketHub(application)._handle_client(connection))
        message = strict_json(connection.send.call_args.args[0])
        self.assertEqual(message["type"], "status")
        self.assertEqual(message["payload"]["operations"][0]["status"], "CANCELED")
        self.assertIsNone(message["payload"]["operations"][0]["result"]["final_error"]["x"])
