from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import AppConfig
from .config import resolve_profile_dir
from .service import _fetch_poll_jobs
from .service import _http_json


def run_health_check(
    config: AppConfig,
    *,
    daemon_url: str,
    jobs_url: str | None,
    status_url: str | None,
) -> int:
    checks: list[tuple[str, bool, str]] = []
    profile_dir = resolve_profile_dir(config)

    checks.append(("config", config.path.exists(), str(config.path)))
    checks.append(("browser engine", config.browser.engine in {"auto", "camoufox", "playwright"}, config.browser.engine))
    checks.append(("profile dir", profile_dir.exists(), str(profile_dir)))
    checks.append(("profile cookies", (profile_dir / "cookies.sqlite").exists(), str(profile_dir / "cookies.sqlite")))
    checks.append(("profile fingerprint", (profile_dir / "camoufox-fingerprint.json").exists(), str(profile_dir / "camoufox-fingerprint.json")))

    lock_path = profile_dir / "parent.lock"
    checks.append(
        (
            "profile lock",
            True,
            "present, profile likely open" if lock_path.exists() else "not present",
        )
    )

    checks.append(("playwright import", _module_exists("playwright"), "python package"))
    checks.append(("camoufox import", _module_exists("camoufox"), "python package"))

    artifacts_dir = config.path.parent / "artifacts"
    checks.append(("artifacts writable", _can_write_to_dir(artifacts_dir), str(artifacts_dir)))

    daemon_ok, daemon_message = _check_daemon(daemon_url)
    checks.append(("daemon health", daemon_ok, daemon_message))

    if jobs_url:
        jobs_ok, jobs_message = _check_jobs_url(jobs_url)
        checks.append(("n8n jobs url", jobs_ok, jobs_message))
    else:
        checks.append(("n8n jobs url", True, "not configured for this check"))

    if status_url:
        checks.append(("n8n status url", _valid_http_url(status_url), status_url))
    else:
        checks.append(("n8n status url", True, "not configured for this check"))

    failed = False
    for name, ok, message in checks:
        marker = "OK" if ok else "FAIL"
        print(f"[{marker}] {name}: {message}")
        if not ok:
            failed = True

    return 1 if failed else 0


def _module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _can_write_to_dir(path: Path) -> bool:
    try:
        path.mkdir(exist_ok=True)
        test_path = path / ".health-check.tmp"
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _check_daemon(daemon_url: str) -> tuple[bool, str]:
    if not _valid_http_url(daemon_url):
        return False, f"invalid URL: {daemon_url}"
    try:
        payload = _http_json("GET", f"{daemon_url.rstrip('/')}/health")
    except Exception as exc:
        return False, str(exc)
    if not isinstance(payload, dict) or not payload.get("ok"):
        return False, f"unexpected response: {payload!r}"
    return True, f"{daemon_url.rstrip('/')}/health queue_size={payload.get('queue_size')}"


def _check_jobs_url(jobs_url: str) -> tuple[bool, str]:
    if not _valid_http_url(jobs_url):
        return False, f"invalid URL: {jobs_url}"
    try:
        jobs = _fetch_poll_jobs(jobs_url)
    except Exception as exc:
        return False, str(exc)
    if not jobs:
        return True, "reachable, no queued job returned"
    return True, f"reachable, returned {len(jobs)} job(s): {_job_summary(jobs[0])}"


def _job_summary(job: dict[str, Any]) -> str:
    job_id = job.get("id") or job.get("job_id") or "no-id"
    date = job.get("date") or "no-date"
    time = job.get("time") or "no-time"
    party_size = job.get("party_size") or job.get("partySize") or job.get("guests") or "no-party-size"
    return f"id={job_id}, date={date}, time={time}, party_size={party_size}"


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
