from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from queue import Empty
from queue import Queue
from time import sleep
from threading import Event
from threading import Lock
from threading import Thread
from typing import Any
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request
from urllib.request import urlopen
from uuid import uuid4

from playwright.sync_api import BrowserContext

from .config import AppConfig
from .config import with_reservation_overrides
from . import opentable


@dataclass
class ServiceState:
    context: BrowserContext
    config: AppConfig
    artifacts_dir: Path
    jobs: dict[str, dict[str, Any]]
    lock: Lock
    automation_lock: Lock
    queue: Queue[str]
    stop_event: Event
    cancel_event: Event
    current_job_id: str | None


def run_service(
    context: BrowserContext,
    config: AppConfig,
    artifacts_dir: Path,
    *,
    host: str,
    port: int,
) -> int:
    artifacts_dir.mkdir(exist_ok=True)
    state = ServiceState(
        context=context,
        config=config,
        artifacts_dir=artifacts_dir,
        jobs={},
        lock=Lock(),
        automation_lock=Lock(),
        queue=Queue(),
        stop_event=Event(),
        cancel_event=Event(),
        current_job_id=None,
    )

    server = _build_server(host, port, state)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"OpenTable service listening on http://{host}:{port}", flush=True)
    print(
        "POST /admin-book to enqueue a job. POST /cancel to cancel. POST /reload to reload automation code.",
        flush=True,
    )

    try:
        _process_jobs(state)
    finally:
        server.shutdown()
        server.server_close()
    return 0


def run_poller(
    context: BrowserContext,
    config: AppConfig,
    artifacts_dir: Path,
    *,
    jobs_url: str,
    status_url: str | None,
    interval_seconds: float,
    once: bool,
) -> int:
    artifacts_dir.mkdir(exist_ok=True)
    print(f"Polling n8n queue: {jobs_url}", flush=True)
    if status_url:
        print(f"Posting job status to: {status_url}", flush=True)

    while True:
        try:
            jobs = _fetch_poll_jobs(jobs_url)
            if not jobs:
                if once:
                    print("[poll] no job returned", flush=True)
                    return 0
                sleep(interval_seconds)
                continue
            for payload in jobs:
                job_id = str(payload.get("id") or payload.get("job_id") or uuid4().hex)
                _post_status(status_url, job_id, "running", payload=payload)
                print(f"[poll] running job {job_id}", flush=True)
                try:
                    job_config = _config_from_payload(config, payload)
                    confirm = bool(payload.get("confirm", False))
                    result = opentable.admin_book_reservation(context, job_config, confirm=confirm)
                    opentable.save_artifacts(context, artifacts_dir, f"poll-{job_id}")
                    _post_status(
                        status_url,
                        job_id,
                        "completed",
                        payload=payload,
                        result={
                            "status": result.status,
                            "message": result.message,
                            "url": result.url,
                        },
                    )
                    print(f"[poll] completed job {job_id}", flush=True)
                except Exception as exc:
                    try:
                        opentable.save_artifacts(context, artifacts_dir, f"poll-{job_id}-error")
                    except Exception:
                        pass
                    diagnostics = getattr(exc, "diagnostics", None)
                    _post_status(
                        status_url,
                        job_id,
                        "failed",
                        payload=payload,
                        result={"diagnostics": diagnostics} if diagnostics else None,
                        error=str(exc),
                    )
                    print(f"[poll] failed job {job_id}: {exc}", flush=True)
            if once:
                return 0
        except KeyboardInterrupt:
            print("Stopping poller.", flush=True)
            return 0
        except Exception as exc:
            print(f"[poll] error: {exc}", flush=True)
            if once:
                return 1
            sleep(interval_seconds)


def run_poll_client(
    *,
    jobs_url: str,
    daemon_url: str,
    status_url: str | None,
    status_method: str,
    interval_seconds: float,
    once: bool,
) -> int:
    daemon_url = daemon_url.rstrip("/")
    status_method = status_method.upper()
    print(f"Polling n8n queue: {jobs_url}", flush=True)
    print(f"Forwarding jobs to browser daemon: {daemon_url}", flush=True)
    if status_url:
        print(f"Sending job status with {status_method} to: {status_url}", flush=True)

    while True:
        try:
            jobs = _fetch_poll_jobs(jobs_url)
            if not jobs:
                if once:
                    print("[poll-client] no job returned", flush=True)
                    return 0
                sleep(interval_seconds)
                continue

            for payload in jobs:
                job_id = str(payload.get("id") or payload.get("job_id") or uuid4().hex)
                try:
                    _post_status(status_url, job_id, "running", method=status_method, payload=payload)
                    response = _http_json("POST", f"{daemon_url}/admin-book", payload)
                    daemon_job_id = str(
                        response.get("id", job_id) if isinstance(response, dict) else job_id
                    )
                    print(f"[poll-client] queued job {job_id} in daemon as {daemon_job_id}", flush=True)
                    daemon_job = _wait_for_daemon_job(daemon_url, daemon_job_id)
                    final_status = str(daemon_job.get("status", "failed"))
                    if final_status == "completed":
                        _post_status(
                            status_url,
                            job_id,
                            "completed",
                            method=status_method,
                            payload=payload,
                            result=daemon_job.get("result") if isinstance(daemon_job.get("result"), dict) else daemon_job,
                        )
                    elif final_status == "cancelled":
                        _post_status(
                            status_url,
                            job_id,
                            "cancelled",
                            method=status_method,
                            payload=payload,
                            error=str(daemon_job.get("error") or "booking cancelled"),
                            result=daemon_job,
                        )
                    else:
                        _post_status(
                            status_url,
                            job_id,
                            "failed",
                            method=status_method,
                            payload=payload,
                            error=str(daemon_job.get("error") or "daemon job failed"),
                            result=daemon_job,
                        )
                    print(f"[poll-client] daemon finished job {job_id}: {final_status}", flush=True)
                except Exception as exc:
                    _post_status(status_url, job_id, "failed", method=status_method, payload=payload, error=str(exc))
                    print(f"[poll-client] failed to queue job {job_id}: {exc}", flush=True)
            if once:
                return 0
        except KeyboardInterrupt:
            print("Stopping poll client.", flush=True)
            return 0
        except Exception as exc:
            print(f"[poll-client] error: {exc}", flush=True)
            if once:
                return 1
            sleep(interval_seconds)


def _build_server(host: str, port: int, state: ServiceState) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                _send_json(self, 200, {"ok": True, "queue_size": state.queue.qsize()})
                return

            if self.path.startswith("/jobs/"):
                job_id = self.path.removeprefix("/jobs/").split("?", 1)[0].strip("/")
                with state.lock:
                    job = state.jobs.get(job_id)
                if not job:
                    _send_json(self, 404, {"error": "job not found"})
                    return
                _send_json(self, 200, job)
                return

            _send_json(self, 404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path == "/cancel":
                cancelled = _cancel_jobs(state)
                _send_json(self, 200, {"ok": True, **cancelled})
                return

            if self.path == "/reload":
                if not state.automation_lock.acquire(blocking=False):
                    _send_json(
                        self,
                        409,
                        {"error": "automation is running; reload after the current job finishes"},
                    )
                    return
                try:
                    importlib.reload(opentable)
                    _send_json(self, 200, {"ok": True, "message": "automation code reloaded"})
                    print("[service] reloaded automation code", flush=True)
                except Exception as exc:
                    _send_json(self, 500, {"error": f"reload failed: {exc}"})
                finally:
                    state.automation_lock.release()
                return

            if self.path != "/admin-book":
                _send_json(self, 404, {"error": "not found"})
                return

            try:
                payload = _read_json(self)
                job = _new_job(payload)
            except ValueError as exc:
                _send_json(self, 400, {"error": str(exc)})
                return

            with state.lock:
                state.jobs[job["id"]] = job
                state.cancel_event.clear()
            state.queue.put(job["id"])
            _send_json(self, 202, {"id": job["id"], "status": job["status"]})

        def log_message(self, format: str, *args) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def _process_jobs(state: ServiceState) -> None:
    while not state.stop_event.is_set():
        try:
            job_id = state.queue.get(timeout=0.5)
        except Empty:
            continue

        with state.lock:
            job = state.jobs[job_id]
            if job.get("status") == "cancelled":
                state.queue.task_done()
                continue
            state.current_job_id = job_id
            job["status"] = "running"
            job["started_at"] = _now()
        print(f"[service] running job {job_id}", flush=True)

        try:
            with state.automation_lock:
                payload = job["payload"]
                job_config = _config_from_payload(state.config, payload)
                confirm = bool(payload.get("confirm", False))
                result = opentable.admin_book_reservation(
                    state.context,
                    job_config,
                    confirm=confirm,
                    cancel_event=state.cancel_event,
                )
                opentable.save_artifacts(state.context, state.artifacts_dir, f"service-{job_id}")
            with state.lock:
                job["status"] = "completed"
                job["completed_at"] = _now()
                job["result"] = {
                    "status": result.status,
                    "message": result.message,
                    "url": result.url,
                }
            print(f"[service] completed job {job_id}", flush=True)
        except Exception as exc:
            is_cancelled = isinstance(exc, opentable.BookingCancelledError)
            try:
                if not is_cancelled:
                    opentable.save_artifacts(state.context, state.artifacts_dir, f"service-{job_id}-error")
            except Exception:
                pass
            with state.lock:
                job["status"] = "cancelled" if is_cancelled else "failed"
                job["completed_at"] = _now()
                job["error"] = str(exc)
                if not is_cancelled:
                    diagnostics = getattr(exc, "diagnostics", None)
                    if diagnostics:
                        job["diagnostics"] = diagnostics
                        job["result"] = {
                            "payload": job.get("payload"),
                            "diagnostics": diagnostics,
                        }
            print(f"[service] {'cancelled' if is_cancelled else 'failed'} job {job_id}: {exc}", flush=True)
        finally:
            with state.lock:
                if state.current_job_id == job_id:
                    state.current_job_id = None
                state.cancel_event.clear()
            state.queue.task_done()


def _cancel_jobs(state: ServiceState) -> dict[str, Any]:
    with state.lock:
        current_job_id = state.current_job_id
        queued_ids: list[str] = []
        for job_id, job in state.jobs.items():
            if job.get("status") == "queued":
                job["status"] = "cancelled"
                job["completed_at"] = _now()
                job["error"] = "Booking cancelled by operator before it started."
                queued_ids.append(job_id)
        if current_job_id:
            state.cancel_event.set()
            current_job = state.jobs.get(current_job_id)
            if current_job:
                current_job["cancel_requested_at"] = _now()

    print(
        "[service] cancel requested"
        + (f" for running job {current_job_id}" if current_job_id else "")
        + (f"; cancelled queued jobs: {', '.join(queued_ids)}" if queued_ids else ""),
        flush=True,
    )
    return {
        "current_job_id": current_job_id,
        "queued_cancelled": queued_ids,
        "message": (
            "Cancel requested for the running job. The browser stays open."
            if current_job_id
            else "No running job. Queued jobs were cancelled."
        ),
    }


def _config_from_payload(config: AppConfig, payload: dict[str, Any]) -> AppConfig:
    return with_reservation_overrides(
        config,
        date_value=_optional_str(payload, "date"),
        time_value=_optional_str(payload, "time"),
        party_size=_optional_int(payload, "party_size", "partySize", "guests"),
        first_name=_optional_str(payload, "first_name", "firstName"),
        last_name=_optional_str(payload, "last_name", "lastName"),
        email=_optional_str(payload, "email"),
        phone=_optional_str(payload, "phone"),
        special_request=_optional_str(payload, "special_request", "specialRequest", "notes"),
    )


def _fetch_poll_jobs(jobs_url: str) -> list[dict[str, Any]]:
    payload = _http_json("GET", jobs_url)
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    if payload.get("job") is None and "job" in payload:
        return []
    if isinstance(payload.get("job"), dict):
        return [payload["job"]]
    if isinstance(payload.get("jobs"), list):
        return [item for item in payload["jobs"] if isinstance(item, dict)]
    if payload.get("empty") or payload.get("status") in {"empty", "idle", "none"}:
        return []
    if payload.get("date") and payload.get("time"):
        return [payload]
    return []


def _post_status(
    status_url: str | None,
    job_id: str,
    status: str,
    *,
    method: str = "POST",
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    if not status_url:
        return
    body: dict[str, Any] = {
        "id": job_id,
        "status": status,
        "timestamp": _now(),
    }
    if payload is not None:
        body["payload"] = payload
    if result is not None:
        body["result"] = result
    if error is not None:
        body["error"] = error
    try:
        _http_json(method, status_url, body)
    except Exception as exc:
        print(f"[poll] could not post status for {job_id}: {exc}", flush=True)


def _wait_for_daemon_job(daemon_url: str, job_id: str) -> dict[str, Any]:
    while True:
        payload = _http_json("GET", f"{daemon_url}/jobs/{job_id}")
        if isinstance(payload, dict) and payload.get("status") in {"completed", "failed"}:
            return payload
        sleep(1)


def _http_json(method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read()
    except HTTPError as exc:
        if exc.code == 204:
            return None
        raise RuntimeError(f"HTTP {exc.code} from {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _new_job(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("date", "time"):
        if not str(payload.get(key, "")).strip():
            raise ValueError(f"Missing required field: {key}")
    if _optional_int(payload, "party_size", "partySize", "guests") is None:
        raise ValueError("Missing required field: party_size")

    return {
        "id": str(payload.get("id") or payload.get("job_id") or uuid4().hex),
        "status": "queued",
        "created_at": _now(),
        "payload": payload,
    }


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length <= 0:
        raise ValueError("Request body must be JSON")
    raw = handler.rfile.read(content_length)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _optional_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _optional_int(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
    return None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
