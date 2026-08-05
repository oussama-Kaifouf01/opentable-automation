from __future__ import annotations

import json
import re
from datetime import date
from datetime import timedelta
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BrowserConfig:
    engine: str = "auto"
    headless: bool = False
    profile_dir: str = ".opentable-profile"
    slow_mo_ms: int = 0
    timeout_ms: int = 30_000


@dataclass(frozen=True)
class GuestConfig:
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""


@dataclass(frozen=True)
class ReservationConfig:
    restaurant_url: str
    party_size: int
    date: str
    time: str
    guest: GuestConfig
    special_request: str = ""
    occasion: str = ""


@dataclass(frozen=True)
class OpenTableConfig:
    home_url: str = "https://www.opentable.com/"
    login_url: str = "https://www.opentable.com/"
    reservations_url: str = "https://www.opentable.com/profile/reservations"


@dataclass(frozen=True)
class AdminConfig:
    dashboard_url: str = "https://guestcenter.opentable.com/"
    reservations_url: str = "https://guestcenter.opentable.com/"
    selectors: dict[str, str] | None = None


@dataclass(frozen=True)
class AppConfig:
    browser: BrowserConfig
    opentable: OpenTableConfig
    admin: AdminConfig
    reservation: ReservationConfig
    path: Path


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required string config value: {key}")
    return value.strip()


def _require_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise ValueError(f"Missing required integer config value: {key}")
    return value


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    browser_raw = raw.get("browser", {})
    opentable_raw = raw.get("opentable", {})
    admin_raw = raw.get("admin", {})
    reservation_raw = raw.get("reservation", {})
    guest_raw = reservation_raw.get("guest", {})

    if not isinstance(browser_raw, dict):
        raise ValueError("browser must be an object")
    if not isinstance(opentable_raw, dict):
        raise ValueError("opentable must be an object")
    if not isinstance(admin_raw, dict):
        raise ValueError("admin must be an object")
    if not isinstance(reservation_raw, dict):
        raise ValueError("reservation must be an object")
    if not isinstance(guest_raw, dict):
        raise ValueError("reservation.guest must be an object")

    browser = BrowserConfig(
        engine=str(browser_raw.get("engine", "auto")).lower(),
        headless=bool(browser_raw.get("headless", False)),
        profile_dir=str(browser_raw.get("profile_dir", ".opentable-profile")),
        slow_mo_ms=int(browser_raw.get("slow_mo_ms", 0)),
        timeout_ms=int(browser_raw.get("timeout_ms", 30_000)),
    )
    opentable = OpenTableConfig(
        home_url=str(opentable_raw.get("home_url", "https://www.opentable.com/")),
        login_url=str(opentable_raw.get("login_url", "https://www.opentable.com/")),
        reservations_url=str(
            opentable_raw.get(
                "reservations_url",
                "https://www.opentable.com/profile/reservations",
            )
        ),
    )
    selectors = admin_raw.get("selectors", {})
    if not isinstance(selectors, dict):
        raise ValueError("admin.selectors must be an object")
    admin = AdminConfig(
        dashboard_url=str(
            admin_raw.get("dashboard_url", "https://guestcenter.opentable.com/")
        ),
        reservations_url=str(
            admin_raw.get("reservations_url", "https://guestcenter.opentable.com/")
        ),
        selectors={
            str(key): str(value)
            for key, value in selectors.items()
            if isinstance(value, str) and value.strip()
        },
    )
    reservation = ReservationConfig(
        restaurant_url=_require_str(reservation_raw, "restaurant_url"),
        party_size=_require_int(reservation_raw, "party_size"),
        date=_require_str(reservation_raw, "date"),
        time=_require_str(reservation_raw, "time"),
        guest=GuestConfig(
            first_name=str(guest_raw.get("first_name", "")),
            last_name=str(guest_raw.get("last_name", "")),
            email=str(guest_raw.get("email", "")),
            phone=str(guest_raw.get("phone", "")),
        ),
        special_request=str(reservation_raw.get("special_request", "")),
        occasion=str(reservation_raw.get("occasion", "")),
    )

    return AppConfig(
        browser=browser,
        opentable=opentable,
        admin=admin,
        reservation=reservation,
        path=config_path,
    )


def with_browser_overrides(
    config: AppConfig,
    *,
    engine: str | None = None,
    profile_dir: str | None = None,
) -> AppConfig:
    browser = config.browser
    if engine:
        browser = replace(browser, engine=engine.lower())
    if profile_dir:
        browser = replace(browser, profile_dir=profile_dir)
    return replace(config, browser=browser)


def with_reservation_overrides(
    config: AppConfig,
    *,
    date_value: str | None = None,
    time_value: str | None = None,
    party_size: int | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    special_request: str | None = None,
) -> AppConfig:
    reservation = config.reservation
    guest = reservation.guest

    if date_value:
        reservation = replace(reservation, date=normalize_booking_date(date_value))
    if time_value:
        reservation = replace(reservation, time=normalize_booking_time(time_value))
    if party_size is not None:
        reservation = replace(reservation, party_size=party_size)
    if special_request is not None:
        reservation = replace(reservation, special_request=special_request)

    if first_name is not None:
        guest = replace(guest, first_name=first_name)
    if last_name is not None:
        guest = replace(guest, last_name=last_name)
    if email is not None:
        guest = replace(guest, email=email)
    if phone is not None:
        guest = replace(guest, phone=phone)

    reservation = replace(reservation, guest=guest)
    return replace(config, reservation=reservation)


def normalize_booking_date(value: str) -> str:
    raw = value.strip().lower()
    today = date.today()

    if raw == "today":
        return today.isoformat()
    if raw == "tomorrow":
        return (today + timedelta(days=1)).isoformat()

    days_match = re.fullmatch(r"\+(\d+)d", raw)
    if days_match:
        return (today + timedelta(days=int(days_match.group(1)))).isoformat()

    next_month_match = re.fullmatch(r"next-month-(\d{1,2})", raw)
    if next_month_match:
        month = today.month + 1
        year = today.year
        if month == 13:
            month = 1
            year += 1
        return date(year, month, int(next_month_match.group(1))).isoformat()

    return date.fromisoformat(value).isoformat()


def normalize_booking_time(value: str) -> str:
    raw = value.strip().lower().replace(".", "")
    am_pm_match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", raw)
    if am_pm_match:
        hour = int(am_pm_match.group(1))
        minute = int(am_pm_match.group(2) or "0")
        suffix = am_pm_match.group(3)
        if suffix == "pm" and hour != 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    twenty_four_hour_match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if twenty_four_hour_match:
        hour = int(twenty_four_hour_match.group(1))
        minute = int(twenty_four_hour_match.group(2))
        if hour > 23 or minute > 59:
            raise ValueError(f"Invalid booking time: {value}")
        return f"{hour:02d}:{minute:02d}"

    raise ValueError("Time must look like 19:00, 7:00pm, or 7pm")


def resolve_profile_dir(config: AppConfig) -> Path:
    profile_dir = Path(config.browser.profile_dir)
    if profile_dir.is_absolute():
        return profile_dir
    return (config.path.parent / profile_dir).resolve()
