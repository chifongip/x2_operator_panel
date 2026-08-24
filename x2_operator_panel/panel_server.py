"""Authenticated local HTTP and WebSocket server for the ROS panel node."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import signal
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.executors import MultiThreadedExecutor
from rclpy.signals import SignalHandlerOptions
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .auth import LoginAttemptLimiter, SessionStore
from .map_model import MapAsset, load_map_asset
from .ros_gateway import OperatorPanelNode, PanelCommandError


_MAX_REQUEST_BYTES = 64 * 1024


class RequestReadTimeout(TimeoutError):
    """The client did not provide its body within the configured limit."""


def _is_loopback_host(value: str | None) -> bool:
    if not value:
        return False
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server with a hard cap on concurrent request handlers."""

    request_queue_size = 32
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler: Any, max_workers: int) -> None:
        if max_workers < 1:
            raise ValueError("http_worker_limit must be positive")
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        super().__init__(server_address, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._worker_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


class AuditLog:
    def __init__(self) -> None:
        self._entries: deque[dict[str, str]] = deque(maxlen=250)
        self._lock = threading.Lock()

    def add(self, action: str, outcome: str, detail: str) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "outcome": outcome,
            "detail": detail[:500],
        }
        with self._lock:
            self._entries.appendleft(entry)

    def entries(self) -> list[dict[str, str]]:
        with self._lock:
            return list(self._entries)


class WebsocketHub:
    """Runs WebSocket status streaming on a dedicated asyncio loop."""

    def __init__(self, application: "PanelApplication") -> None:
        self._application = application
        self._clients: dict[Any, str] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._broadcast_lock = threading.Lock()
        self._latest_payload: str | None = None
        self._broadcast_scheduled = False

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="operator-panel-websocket", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)
        if self._error is not None:
            raise RuntimeError(f"WebSocket server failed to start: {self._error}") from self._error
        if not self._ready.is_set():
            raise RuntimeError("WebSocket server did not start within five seconds")

    def stop(self) -> None:
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def broadcast(self, snapshot: dict[str, Any]) -> None:
        if self._loop is None:
            return
        payload = json.dumps({"type": "status", "payload": snapshot}, separators=(",", ":"))
        with self._broadcast_lock:
            self._latest_payload = payload
            if self._broadcast_scheduled:
                return
            self._broadcast_scheduled = True
        try:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._drain_broadcasts())
            )
        except RuntimeError:
            with self._broadcast_lock:
                self._broadcast_scheduled = False
            return

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as error:
            self._error = error
            self._ready.set()
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        self._stop_event = asyncio.Event()
        async with serve(
            self._handle_client,
            self._application.bind_address,
            self._application.websocket_port,
            compression="deflate",
            max_size=64 * 1024,
            max_queue=1,
        ):
            self._ready.set()
            await self._stop_event.wait()

    async def _handle_client(self, connection: Any) -> None:
        headers = connection.request.headers
        token = self._application.authenticated_session_token(headers)
        if token is None or not self._application.websocket_origin_is_allowed(headers):
            await connection.close(code=1008, reason="Unauthorized panel session")
            return
        if len(self._clients) >= self._application.websocket_client_limit:
            await connection.close(code=1013, reason="Panel WebSocket limit reached")
            return
        self._clients[connection] = token
        try:
            await asyncio.wait_for(
                connection.send(
                    json.dumps(
                        {"type": "status", "payload": self._application.status()},
                        separators=(",", ":"),
                    )
                ),
                timeout=self._application.websocket_send_timeout_sec,
            )
            async for _ in connection:
                if not self._application.sessions.valid(token):
                    await connection.close(code=1008, reason="Panel session expired")
                    break
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        finally:
            self._clients.pop(connection, None)

    async def _drain_broadcasts(self) -> None:
        while True:
            with self._broadcast_lock:
                payload = self._latest_payload
                self._latest_payload = None
                if payload is None:
                    self._broadcast_scheduled = False
                    return
            await self._broadcast(payload)

    async def _broadcast(self, payload: str) -> None:
        if not self._clients:
            return

        async def send_one(connection: Any, token: str) -> bool:
            if not self._application.sessions.valid(token):
                try:
                    await connection.close(code=1008, reason="Panel session expired")
                except ConnectionClosed:
                    pass
                return False
            try:
                await asyncio.wait_for(
                    connection.send(payload),
                    timeout=self._application.websocket_send_timeout_sec,
                )
                return True
            except (ConnectionClosed, asyncio.TimeoutError):
                try:
                    await connection.close(code=1013, reason="Status client too slow")
                except ConnectionClosed:
                    pass
                return False

        clients = tuple(self._clients.items())
        results = await asyncio.gather(
            *(send_one(connection, token) for connection, token in clients),
            return_exceptions=True,
        )
        for (connection, _), result in zip(clients, results):
            if result is not True:
                self._clients.pop(connection, None)


class PanelApplication:
    def __init__(self, node: OperatorPanelNode) -> None:
        self.node = node
        self.bind_address = node.bind_address
        if not _is_loopback_host(self.bind_address):
            raise ValueError(
                "bind_address must be loopback; expose the panel through an "
                "authenticated HTTPS reverse proxy or SSH tunnel"
            )
        self.http_port = node.http_port
        self.websocket_port = node.websocket_port
        self.websocket_client_limit = node.websocket_client_limit
        self.websocket_send_timeout_sec = node.websocket_send_timeout_sec
        if (
            self.websocket_client_limit < 1
            or self.websocket_send_timeout_sec <= 0.0
            or node.http_request_timeout_sec <= 0.0
        ):
            raise ValueError("HTTP and WebSocket limits must be positive")
        self.static_root = (
            os.path.join(get_package_share_directory("x2_operator_panel"), "static")
        )
        if not os.path.isdir(self.static_root):
            raise ValueError(f"Installed static asset directory is missing: {self.static_root}")
        self.map_asset: MapAsset = load_map_asset(node.map_yaml)
        self.sessions = SessionStore(
            os.environ.get("X2_OPERATOR_PANEL_PASSWORD_HASH"), node.session_ttl_sec
        )
        self.audit = AuditLog()
        self.login_limiter = LoginAttemptLimiter(
            per_source_limit=node.login_per_source_limit,
            global_limit=node.login_global_limit,
            window_sec=node.login_window_sec,
        )
        self.websocket_hub = WebsocketHub(self)
        self.http_server: BoundedThreadingHTTPServer | None = None
        self.http_thread: threading.Thread | None = None
        self._allowed_origins, self.secure_cookies = self._build_allowed_origins(
            node.allowed_origin
        )
        self.websocket_url = node.websocket_url.strip()
        if self.websocket_url:
            parsed_websocket = urlsplit(self.websocket_url)
            expected_scheme = "wss" if self.secure_cookies else "ws"
            if parsed_websocket.scheme != expected_scheme or not parsed_websocket.netloc:
                raise ValueError(f"websocket_url must use {expected_scheme}:// with a host")
        self.node.set_status_sink(self.websocket_hub.broadcast)
        self.node.set_audit_sink(self.audit.add)

    def start(self) -> None:
        if not self.sessions.configured:
            self.node.get_logger().error(
                "X2_OPERATOR_PANEL_PASSWORD_HASH is unset; command APIs will remain locked"
            )
        handler = _make_request_handler(self)
        self.http_server = BoundedThreadingHTTPServer(
            (self.bind_address, self.http_port), handler, self.node.http_worker_limit
        )
        try:
            self.websocket_hub.start()
        except Exception:
            self.http_server.server_close()
            self.http_server = None
            raise
        self.http_thread = threading.Thread(
            target=self.http_server.serve_forever,
            name="operator-panel-http",
            daemon=True,
        )
        self.http_thread.start()
        self.node.get_logger().info(
            f"Operator panel listening at http://{self.bind_address}:{self.http_port}"
        )

    def stop(self) -> None:
        self.stop_accepting()
        self.websocket_hub.stop()

    def stop_accepting(self) -> None:
        if self.http_server is not None:
            self.http_server.shutdown()
            self.http_server.server_close()
            self.http_server = None
        if self.http_thread is not None:
            self.http_thread.join(timeout=5.0)
            self.http_thread = None

    def status(self) -> dict[str, Any]:
        status = self.node.snapshot()
        status["authentication_configured"] = self.sessions.configured
        status["audit"] = self.audit.entries()
        return status

    def map_metadata(self) -> dict[str, Any]:
        return self.map_asset.metadata()

    def login(self, password: str) -> str | None:
        session = self.sessions.login(password)
        if session is not None:
            self.audit.add("login", "accepted", "Operator session started")
            return session.token
        self.audit.add("login", "rejected", "Invalid password or panel not configured")
        return None

    def authenticated_session_token(self, headers: Any) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("Cookie", ""))
        except (TypeError, ValueError):
            return None
        morsel = cookie.get("x2_operator_session")
        token = morsel.value if morsel is not None else None
        return token if self.sessions.valid(token) else None

    def request_is_authenticated(self, headers: Any) -> bool:
        return self.authenticated_session_token(headers) is not None

    def websocket_origin_is_allowed(self, headers: Any) -> bool:
        origin = headers.get("Origin")
        if origin is None:
            return False
        return origin in self._allowed_origins

    def unsafe_request_has_same_origin(self, headers: Any) -> bool:
        origin = headers.get("Origin")
        host = headers.get("Host")
        if not origin or not host:
            return False
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        return origin in self._allowed_origins and parsed.netloc == host

    def _build_allowed_origins(self, configured: str) -> tuple[set[str], bool]:
        origins = (
            {origin.strip() for origin in configured.split(",") if origin.strip()}
            if configured.strip()
            else {
                f"http://localhost:{self.http_port}",
                f"http://127.0.0.1:{self.http_port}",
            }
        )
        schemes: set[str] = set()
        for origin in origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"Invalid allowed_origin: {origin}")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError("allowed_origin entries must not contain paths or queries")
            if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
                raise ValueError("Non-loopback allowed_origin entries must use HTTPS")
            schemes.add(parsed.scheme)
        if len(schemes) != 1:
            raise ValueError("Do not mix HTTP and HTTPS allowed origins")
        return origins, schemes == {"https"}


def _make_request_handler(application: PanelApplication) -> type[BaseHTTPRequestHandler]:
    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "X2OperatorPanel/0.1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(application.node.http_request_timeout_sec)

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/":
                self._serve_index()
                return
            if path in {"/assets/app.js", "/assets/style.css", "/assets/config.js"}:
                self._serve_static(path)
                return
            if path.startswith("/api/"):
                if not application.request_is_authenticated(self.headers):
                    self._json_error(HTTPStatus.UNAUTHORIZED, "Authentication required")
                    return
                if path == "/api/status":
                    self._json(HTTPStatus.OK, application.status())
                elif path == "/api/map":
                    self._json(HTTPStatus.OK, application.map_metadata())
                elif path == "/api/map/image":
                    self._bytes(HTTPStatus.OK, application.map_asset.png, "image/png")
                elif path == "/api/presets":
                    self._json(HTTPStatus.OK, {"presets": application.node.presets()})
                else:
                    self._json_error(HTTPStatus.NOT_FOUND, "Unknown API endpoint")
                return
            self._json_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/api/login":
                allowed, retry_after = application.login_limiter.consume(
                    str(self.client_address[0])
                )
                if not allowed:
                    application.audit.add(
                        "login", "throttled", f"Source {self.client_address[0]}"
                    )
                    self._json_error(
                        HTTPStatus.TOO_MANY_REQUESTS,
                        "Too many login attempts; retry later",
                        {"Retry-After": str(retry_after)},
                    )
                    return
            try:
                payload = self._read_json()
            except RequestReadTimeout:
                self._json_error(HTTPStatus.REQUEST_TIMEOUT, "Request body timed out")
                return
            except PanelCommandError as error:
                self._json_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            if path == "/api/login":
                password = payload.get("password")
                if not isinstance(password, str):
                    self._json_error(HTTPStatus.BAD_REQUEST, "Password is required")
                    return
                token = application.login(password)
                if token is None:
                    self._json_error(HTTPStatus.UNAUTHORIZED, "Invalid operator credentials")
                    return
                cookie = (
                    f"x2_operator_session={token}; HttpOnly; SameSite=Strict; "
                    f"Path=/; Max-Age={int(application.node.session_ttl_sec)}"
                )
                if application.secure_cookies:
                    cookie += "; Secure"
                self._bytes(
                    HTTPStatus.OK,
                    b'{"ok":true}',
                    "application/json; charset=utf-8",
                    {"Set-Cookie": cookie},
                )
                return
            if not application.request_is_authenticated(self.headers):
                self._json_error(HTTPStatus.UNAUTHORIZED, "Authentication required")
                return
            if not application.unsafe_request_has_same_origin(self.headers):
                self._json_error(HTTPStatus.FORBIDDEN, "Invalid request origin")
                return
            try:
                if path == "/api/unlock/execution":
                    if payload.get("confirmed") is not True:
                        raise PanelCommandError("Execution unlock requires confirmation")
                    response = application.node.enable_execution_unlock()
                elif path == "/api/actions":
                    response = application.node.request("submit", payload)
                elif path == "/api/cancel":
                    response = application.node.request("cancel_active", {})
                elif path == "/api/recover-state":
                    response = application.node.request("recover_state", payload)
                else:
                    self._json_error(HTTPStatus.NOT_FOUND, "Unknown API endpoint")
                    return
            except PanelCommandError as error:
                self._json_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            except TimeoutError:
                self._json_error(HTTPStatus.GATEWAY_TIMEOUT, "ROS panel command timed out")
                return
            self._json(HTTPStatus.ACCEPTED, response)

        def _read_json(self) -> dict[str, Any]:
            content_length = self.headers.get("Content-Length")
            if content_length is None:
                raise PanelCommandError("Request body is required")
            try:
                size = int(content_length)
            except ValueError as error:
                raise PanelCommandError("Invalid Content-Length") from error
            if size < 0 or size > _MAX_REQUEST_BYTES:
                raise PanelCommandError("Request body is too large")
            try:
                body = self.rfile.read(size)
            except (socket.timeout, TimeoutError, OSError) as error:
                raise RequestReadTimeout from error
            if len(body) != size:
                raise PanelCommandError("Request body ended before Content-Length")
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise PanelCommandError("Request body must be JSON") from error
            if not isinstance(value, dict):
                raise PanelCommandError("Request body must be a JSON object")
            return value

        def _serve_index(self) -> None:
            with open(
                os.path.join(application.static_root, "index.html"),
                encoding="utf-8",
            ) as index_file:
                content = index_file.read()
            self._bytes(HTTPStatus.OK, content.encode("utf-8"), "text/html; charset=utf-8")

        def _serve_static(self, path: str) -> None:
            name = path.removeprefix("/assets/")
            if name == "config.js":
                config = {
                    "websocketPort": application.websocket_port,
                    "websocketUrl": application.websocket_url,
                }
                body = f"window.X2_PANEL_CONFIG={json.dumps(config)};\n".encode("utf-8")
                self._bytes(
                    HTTPStatus.OK, body, "application/javascript; charset=utf-8"
                )
                return
            static_file = os.path.join(application.static_root, name)
            content_types = {
                "app.js": "application/javascript; charset=utf-8",
                "style.css": "text/css; charset=utf-8",
            }
            if name not in content_types or not os.path.isfile(static_file):
                self._json_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            with open(static_file, "rb") as asset_file:
                self._bytes(HTTPStatus.OK, asset_file.read(), content_types[name])

        def _json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
            self._bytes(
                status,
                json.dumps(value, separators=(",", ":")).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _json_error(
            self,
            status: HTTPStatus,
            message: str,
            headers: dict[str, str] | None = None,
        ) -> None:
            self._bytes(
                status,
                json.dumps({"error": message}, separators=(",", ":")).encode("utf-8"),
                "application/json; charset=utf-8",
                headers,
            )

        def _bytes(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str,
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self' ws: wss:; "
                "img-src 'self'; style-src 'self'; script-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass

        def log_message(self, format: str, *args: Any) -> None:
            application.node.get_logger().debug(format % args)

    return RequestHandler


def main() -> None:
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node: OperatorPanelNode | None = None
    application: PanelApplication | None = None
    executor = MultiThreadedExecutor(num_threads=2)

    def stop_on_sigterm(_: int, __: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_on_sigterm)
    try:
        node = OperatorPanelNode()
        executor.add_node(node)
        application = PanelApplication(node)
        application.start()
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if application is not None:
            application.stop_accepting()
        if node is not None:
            canceled = node.begin_shutdown()
            if canceled["operation_ids"]:
                node.get_logger().warn(
                    "Canceling active goals before operator panel shutdown"
                )
            deadline = time.monotonic() + node.shutdown_cancel_grace_sec
            while (
                rclpy.ok()
                and node.has_active_action_operations()
                and time.monotonic() < deadline
            ):
                executor.spin_once(timeout_sec=0.1)
            if node.has_active_action_operations():
                node.get_logger().error(
                    "Shutdown cancellation was not confirmed; verify robot state before restarting"
                )
        if application is not None:
            application.stop()
        executor.shutdown(timeout_sec=2.0)
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
