"""Runs the app on a real uvicorn server in a background thread.

Shared by verify_background_tasks.py and demo_session.py.

Why this exists — the trap at the centre of this exercise:

    TestClient CANNOT be used to prove that a response returns before its
    background tasks finish, because it waits for them.

    A Starlette Response runs `await self.background()` at the end of its
    `__call__`, after the body has been sent. Over a real socket that is
    invisible to the client: the bytes are already gone, and the connection
    closing a moment later changes nothing the client can see. TestClient
    has no socket. It awaits the ASGI application directly, so
    `client.post(...)` does not return until the whole coroutine — background
    tasks included — has completed.

    Time a POST through TestClient and you measure 2 seconds, conclude the
    background task is blocking the response, and go looking for a bug that
    is not there. Section [2] of verify_background_tasks.py measures it both
    ways to make that concrete.
"""

import contextlib
import socket
import threading
import time

import uvicorn


def _free_port() -> int:
    """Asks the OS for an unused port.

    Binding to port 0 and reading back what was assigned avoids a hardcoded
    8000 colliding with a dev server the grader already has running.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def live_server(app, startup_timeout: float = 15.0):
    """Yields the base URL of a running server, and shuts it down on exit."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # server.started flips inside the event loop once the socket is accepting.
    # Polling it beats sleeping a fixed interval, which is either too short on
    # a loaded machine or wasted time on a fast one.
    deadline = time.monotonic() + startup_timeout
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("the server thread died during startup")
        if time.monotonic() > deadline:
            raise RuntimeError(f"server did not start within {startup_timeout}s")
        time.sleep(0.02)

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=startup_timeout)
