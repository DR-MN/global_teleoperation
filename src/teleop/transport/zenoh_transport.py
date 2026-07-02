"""Zenoh transport: production control-plane transport per the spec.

Zenoh natively traverses NAT and supports peer/client/router modes, so leader
and follower on different networks connect through a cloud Zenoh router
(``zenoh_endpoint``). Payloads are JSON-encoded on the wire.

This import is lazy: the module imports cleanly even when ``zenoh`` is not
installed (e.g. on the Windows dev box running the in-process slice). The error
only surfaces if you actually select the zenoh transport.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from .base import Transport, Handler

log = logging.getLogger(__name__)


class ZenohTransport(Transport):
    def __init__(self, endpoint: Optional[str] = None, session_id: str = "default") -> None:
        try:
            import zenoh  # type: ignore
        except ImportError as e:  # pragma: no cover - depends on optional dep
            raise RuntimeError(
                "zenoh is not installed. Install with `pip install eclipse-zenoh` "
                "or use transport='inproc' for the hardware-free slice."
            ) from e

        self._zenoh = zenoh
        self._prefix = f"teleop/{session_id}"
        conf = zenoh.Config()
        if endpoint:
            # Connect to a cloud router so NAT'd peers can rendezvous.
            conf.insert_json5("connect/endpoints", json.dumps([endpoint]))
        self._session = zenoh.open(conf)
        self._subscribers: list = []  # keep handles alive
        log.info("Zenoh session open (prefix=%s, endpoint=%s)", self._prefix, endpoint)

    def _full(self, key: str) -> str:
        return f"{self._prefix}/{key}"

    def publish(self, key: str, payload: Dict[str, Any]) -> None:
        try:
            self._session.put(self._full(key), json.dumps(payload).encode("utf-8"))
        except Exception:  # never crash the control loop on a transient fault
            log.exception("Zenoh publish failed on %s", key)

    def subscribe(self, key: str, handler: Handler) -> None:
        def _on_sample(sample: Any) -> None:
            try:
                payload = sample.payload
                # zenoh 1.x exposes bytes via ZBytes.to_bytes(); older builds
                # support bytes(payload). Handle both so an API bump can't break us.
                raw = payload.to_bytes() if hasattr(payload, "to_bytes") else bytes(payload)
                handler(json.loads(raw.decode("utf-8")))
            except Exception:
                log.exception("Zenoh subscriber error on %s", key)

        sub = self._session.declare_subscriber(self._full(key), _on_sample)
        self._subscribers.append(sub)

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            log.exception("Zenoh close failed")


def make_transport(config: "Any", role: str = "viewer",
                   peer_id: str = "peer") -> Transport:  # noqa: ANN401
    """Factory: build the configured transport from a SystemConfig.

    ``role``/``peer_id`` are only used by the WebSocket transport (to identify
    the peer to the signaling server). ``ws_url`` is read from
    ``config.zenoh_endpoint`` (reused as the generic endpoint field) or, for ws,
    from ``config.ws_url`` if present.
    """
    from .inproc import InProcTransport
    kind = getattr(config, "transport", "inproc")
    if kind == "zenoh":
        return ZenohTransport(config.zenoh_endpoint, config.session_id)
    if kind == "ws":
        from .ws_transport import WebSocketTransport
        url = getattr(config, "ws_url", None) or config.zenoh_endpoint
        return WebSocketTransport(url, config.session_id, peer_id, role=role)
    return InProcTransport()
