from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, sync_playwright

from .config import AppConfig, resolve_profile_dir


class BrowserLaunchError(RuntimeError):
    pass


@contextmanager
def open_browser(config: AppConfig) -> Iterator[BrowserContext]:
    profile_dir = resolve_profile_dir(config)
    profile_dir.mkdir(parents=True, exist_ok=True)

    engine = config.browser.engine.lower()
    if engine not in {"auto", "camoufox", "playwright"}:
        raise BrowserLaunchError(
            "browser.engine must be one of: auto, camoufox, playwright"
        )

    if engine in {"auto", "camoufox"}:
        try:
            with _open_camoufox(config, profile_dir) as context:
                yield context
                return
        except ImportError:
            if engine == "camoufox":
                raise BrowserLaunchError(
                    "Camoufox is not installed. Run: pip install -r requirements.txt"
                )
        except TypeError as exc:
            if engine == "camoufox":
                raise BrowserLaunchError(f"Camoufox rejected launch options: {exc}") from exc

    with _open_playwright(config, profile_dir) as context:
        yield context


@contextmanager
def _open_camoufox(config: AppConfig, profile_dir: Path) -> Iterator[BrowserContext]:
    from camoufox.sync_api import Camoufox

    kwargs = {
        "headless": config.browser.headless,
        "slow_mo": config.browser.slow_mo_ms,
        "persistent_context": True,
        "user_data_dir": str(profile_dir),
        "fingerprint": _stable_camoufox_fingerprint(profile_dir),
    }

    with Camoufox(**kwargs) as context:
        context.set_default_timeout(config.browser.timeout_ms)
        yield context


@contextmanager
def _open_playwright(config: AppConfig, profile_dir: Path) -> Iterator[BrowserContext]:
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=config.browser.headless,
            slow_mo=config.browser.slow_mo_ms,
            viewport={"width": 1365, "height": 900},
        )
        context.set_default_timeout(config.browser.timeout_ms)
        try:
            yield context
        finally:
            context.close()


def _stable_camoufox_fingerprint(profile_dir: Path):
    from browserforge.fingerprints import Fingerprint
    from browserforge.fingerprints import NavigatorFingerprint
    from browserforge.fingerprints import ScreenFingerprint
    from browserforge.fingerprints import VideoCard
    from camoufox.fingerprints import generate_fingerprint

    fingerprint_path = profile_dir / "camoufox-fingerprint.json"
    if fingerprint_path.exists():
        data = json.loads(fingerprint_path.read_text(encoding="utf-8"))
        return _fingerprint_from_dict(
            data,
            Fingerprint=Fingerprint,
            NavigatorFingerprint=NavigatorFingerprint,
            ScreenFingerprint=ScreenFingerprint,
            VideoCard=VideoCard,
        )

    fingerprint = generate_fingerprint(window=(1365, 900), os="windows")
    fingerprint_path.write_text(fingerprint.dumps(), encoding="utf-8")
    return fingerprint


def _fingerprint_from_dict(
    data,
    *,
    Fingerprint,
    NavigatorFingerprint,
    ScreenFingerprint,
    VideoCard,
):
    video_card = data.get("videoCard")
    screen = dict(data["screen"])
    screen.pop("screenY", None)
    return Fingerprint(
        screen=ScreenFingerprint(**screen),
        navigator=NavigatorFingerprint(**data["navigator"]),
        headers=data["headers"],
        videoCodecs=data["videoCodecs"],
        audioCodecs=data["audioCodecs"],
        pluginsData=data["pluginsData"],
        battery=data.get("battery"),
        videoCard=VideoCard(**video_card) if video_card else None,
        multimediaDevices=data["multimediaDevices"],
        fonts=data["fonts"],
        mockWebRTC=data.get("mockWebRTC"),
        slim=data.get("slim"),
    )
