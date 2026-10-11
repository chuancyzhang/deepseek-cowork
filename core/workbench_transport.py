"""One-shot desktop requests carried by the authenticated daemon stream.

Only live, claimable requests execute. Journal replay and reconnect never replay
an action, and tool arguments cannot choose the target conversation.
"""
import threading
import time
import uuid
import logging
from .workbench import WorkbenchRequest, WorkbenchError, workbench_bridge
from .execution_authorization import current_authorization

_lock = threading.RLock()
_pending = {}
log = logging.getLogger(__name__)


def dispatch(send, action, arguments, context, timeout):
    auth = current_authorization(context)
    request = WorkbenchRequest(auth.session_id, auth.run_id, action, dict(arguments),
                               dict(context), time.monotonic() + timeout)
    with _lock:
        _pending[request.id] = request
    try:
        send({"type": "workbench_request", "data": {"request_id": request.id}})
        while not request.event.wait(0.1):
            request.check()
        return request.result
    except Exception as exc:
        request.fail(exc)
        return request.result
    finally:
        with _lock:
            _pending.pop(request.id, None)


def handle_host_request(data):
    with _lock:
        request = _pending.get(data.get("request_id"))
        if (request is None or request.session_id != data.get("session_id")
                or request.run_id != data.get("run_id") or request.event.is_set()):
            return {"status": "unavailable"}
        try:
            request.check()
        except Exception as exc:
            request.fail(exc)
            return {"status": "unavailable"}
        action = data.get("action")
        if action == "workbench_claim":
            if request.started:
                return {"status": "already_claimed"}
            request.started = True
            request.host_token = uuid.uuid4().hex
            return {"status": "ok", "action": request.action, "arguments": request.arguments,
                    "token": request.host_token, "policy": current_authorization(request.context).runtime_snapshot(),
                    "model_supports_vision": bool(request.context.get("model_supports_vision")),
                    "model_api_protocol": str(request.context.get("model_api_protocol") or ""),
                    "remaining": max(0, request.deadline - time.monotonic())}
        if data.get("token") != getattr(request, "host_token", None):
            return {"status": "unavailable"}
        if action == "workbench_result":
            request.finish(data.get("result") or {"ok": False, "status": "failed"})
        return {"status": "ok"}


def run_desktop_request(client, request_id, session_id, run_id):
    """Runs off the GUI/stream threads; the bridge dispatches onto the GUI thread."""
    from .execution_authorization import authorization_from_host_snapshot
    payload = dict(request_id=request_id, session_id=session_id, run_id=run_id)
    try:
        claim = client._request(dict(payload, action="workbench_claim"), timeout=5)
    except Exception:
        log.exception("workbench.claim_error session=%s run=%s request=%s", session_id, run_id, request_id)
        return
    if not isinstance(claim, dict) or claim.get("status") != "ok":
        return
    payload["token"] = claim["token"]
    authorization = authorization_from_host_snapshot(claim["policy"], session_id=session_id, run_id=run_id)
    stopped = threading.Event()
    def monitor():
        while not stopped.wait(0.2):
            try:
                active = client._request(dict(payload, action="workbench_active"), timeout=2)
                if not isinstance(active, dict) or active.get("status") != "ok":
                    authorization.cancel()
                    return
            except Exception:
                authorization.cancel()
                return
    watcher = threading.Thread(target=monitor, name="workbench-cancel", daemon=True)
    watcher.start()
    try:
        result = workbench_bridge().call(claim["action"], claim["arguments"],
            {"execution_authorization": authorization,
             "model_supports_vision": claim.get("model_supports_vision", False),
             "model_api_protocol": claim.get("model_api_protocol", "")}, timeout=min(30, claim["remaining"]))
        client._request(dict(payload, action="workbench_result", result=result), timeout=5)
    except Exception:
        log.exception("workbench.transport_error session=%s run=%s request=%s", session_id, run_id, request_id)
    finally:
        stopped.set()
