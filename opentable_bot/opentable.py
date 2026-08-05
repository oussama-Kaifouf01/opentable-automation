from __future__ import annotations

import calendar
from datetime import date
from datetime import datetime
import json
import os
import re
from time import sleep
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError

from .config import AppConfig, ReservationConfig

CALENDAR_NEXT_X = 821
CALENDAR_PREVIOUS_X = 773
CALENDAR_ARROW_Y = 264
CALENDAR_GRID_X = 166
CALENDAR_GRID_Y = 334
CALENDAR_CELL_WIDTH = 95
CALENDAR_CELL_HEIGHT = 79
FINAL_CTA_DELAY_SECONDS = 5
DIAGNOSTIC_EVENT_LIMIT = 80
DIAGNOSTIC_TEXT_LIMIT = 900

_DIAGNOSTIC_STORES: dict[int, list[dict[str, Any]]] = {}
_DIAGNOSTIC_CONTEXT_IDS: set[int] = set()
_DIAGNOSTIC_PAGE_IDS: set[int] = set()


@dataclass(frozen=True)
class BookingResult:
    status: str
    url: str
    message: str


def login_interactively(context: BrowserContext, config: AppConfig) -> None:
    page = _page(context)
    page.goto(config.opentable.login_url, wait_until="domcontentloaded")
    _dismiss_cookie_banner(page)
    email = os.getenv("OPENTABLE_EMAIL", "").strip()
    password = os.getenv("OPENTABLE_PASSWORD", "").strip()

    if email and password:
        attempted = _attempt_login(page, email, password)
        if attempted:
            print("Submitted OpenTable login credentials from .env.")
            print("If OpenTable asks for CAPTCHA, SMS, or email verification, complete it now.")
        else:
            print("Could not find OpenTable login controls automatically.")
    else:
        print("OPENTABLE_EMAIL and OPENTABLE_PASSWORD are not set in .env.")

    print("After the account is visibly logged in, press Enter here to save the session.")
    input()
    page.goto(config.admin.reservations_url or config.admin.dashboard_url, wait_until="domcontentloaded")
    _wait_for_page_ready(page)
    if _looks_guestcenter_logged_out(page):
        save_artifacts(context, config.path.parent / "artifacts", "login-not-authenticated")
        raise RuntimeError(
            "GuestCenter still shows a login page. Complete login in the browser before pressing Enter."
        )
    context.storage_state(path=str(config.path.parent / "artifacts" / "storage-state.json"))


def check_reservations(context: BrowserContext, config: AppConfig) -> list[str]:
    page = _page(context)
    page.goto(config.opentable.reservations_url, wait_until="domcontentloaded")
    _dismiss_cookie_banner(page)
    _wait_for_page_ready(page)

    cards = _reservation_cards(page)
    if cards:
        return cards

    body_text = _visible_text(page)
    if _looks_logged_out(body_text):
        return ["Not logged in. Run `python run.py login` first."]
    if "reservation" not in body_text.lower():
        return [
            "Could not identify reservation cards on the page.",
            f"Opened: {page.url}",
        ]
    return _summarize_lines(body_text)


def admin_check_reservations(context: BrowserContext, config: AppConfig) -> list[str]:
    page = _page(context)
    page.goto(config.admin.reservations_url, wait_until="domcontentloaded")
    _dismiss_cookie_banner(page)
    _wait_for_page_ready(page)

    body_text = _visible_text(page)
    if _looks_guestcenter_logged_out(page):
        return ["Not logged in to GuestCenter. Run `python run.py login` first."]
    return _summarize_lines(body_text)


def admin_select_date(
    context: BrowserContext,
    config: AppConfig,
) -> BookingResult:
    reservation = config.reservation
    selectors = config.admin.selectors or {}
    page = _page(context)
    page.goto(config.admin.reservations_url or config.admin.dashboard_url, wait_until="domcontentloaded")
    _dismiss_cookie_banner(page)
    _wait_for_page_ready(page)
    if _looks_guestcenter_logged_out(page):
        raise RuntimeError(
            "GuestCenter is asking for login. Run `python run.py session`, then `login`, "
            "and press Enter only after the reservations page is visible."
        )

    _open_admin_reservation_modal(page, selectors)
    _set_admin_date(page, reservation.date, selectors)
    return BookingResult(
        status="ready",
        url=page.url,
        message=f"Opened GuestCenter make-reservation modal and selected {reservation.date}.",
    )


def admin_debug_datepicker(context: BrowserContext, config: AppConfig) -> list[str]:
    page = _page(context)
    selectors = config.admin.selectors or {}
    page.goto(config.admin.reservations_url or config.admin.dashboard_url, wait_until="domcontentloaded")
    _dismiss_cookie_banner(page)
    _wait_for_page_ready(page)
    if _looks_guestcenter_logged_out(page):
        return ["GuestCenter is asking for login."]

    _open_admin_reservation_modal(page, selectors)
    visible_month = _visible_calendar_month(page)
    header = page.locator("[class*='DatePicker__header']").first
    try:
        header_text = header.inner_text(timeout=2000).strip()
    except PlaywrightTimeoutError:
        header_text = "DatePicker header not found"

    right_count = page.locator("button:has(span.gc-icon.ic-right)").count()
    left_count = page.locator("button:has(span.gc-icon.ic-left)").count()
    button_count = page.locator("button").count()
    iframe_button_counts = _iframe_button_counts(page)
    svg_arrow_candidates = _calendar_svg_arrow_candidates(page)
    next_x, next_y = _calendar_arrow_point(page, "next")
    previous_x, previous_y = _calendar_arrow_point(page, "previous")
    anchor = _calendar_anchor_box(page)
    anchor_text = (
        f"x={anchor['x']:.0f}, y={anchor['y']:.0f}, w={anchor['width']:.0f}, h={anchor['height']:.0f}"
        if anchor
        else "not found"
    )
    return [
        f"Header: {header_text}",
        f"Visible month: {visible_month.strftime('%B %Y') if visible_month else 'not found'}",
        f"Right arrow buttons: {right_count}",
        f"Left arrow buttons: {left_count}",
        f"All buttons: {button_count}",
        f"Iframe button counts: {_format_iframe_button_counts(iframe_button_counts)}",
        "Recorded iframe arrows: previous=button nth(3), next=button nth(4)",
        f"SVG arrow candidates: {_format_svg_arrow_candidates(svg_arrow_candidates)}",
        f"Calendar anchor: {anchor_text}",
        f"Element at previous arrow: {_element_from_point_summary(page, previous_x, previous_y)}",
        f"Element at next arrow: {_element_from_point_summary(page, next_x, next_y)}",
        (
            "Coordinate fallback: enabled "
            f"(previous={CALENDAR_PREVIOUS_X},{CALENDAR_ARROW_Y}; "
            f"next={CALENDAR_NEXT_X},{CALENDAR_ARROW_Y}; "
            f"grid={CALENDAR_GRID_X},{CALENDAR_GRID_Y})"
        ),
    ]


def admin_book_reservation(
    context: BrowserContext,
    config: AppConfig,
    *,
    confirm: bool,
) -> BookingResult:
    _ensure_browser_diagnostics(context)
    try:
        return _admin_book_reservation_impl(context, config, confirm=confirm)
    except Exception as exc:
        diagnostics = save_failure_diagnostics(
            context,
            config.path.parent / "artifacts",
            f"admin-book-error-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            exc,
        )
        raise RuntimeError(format_failure_message(str(exc), diagnostics)) from exc


def _admin_book_reservation_impl(
    context: BrowserContext,
    config: AppConfig,
    *,
    confirm: bool,
) -> BookingResult:
    reservation = config.reservation
    selectors = config.admin.selectors or {}
    page = _page(context)
    page.goto(config.admin.reservations_url or config.admin.dashboard_url, wait_until="domcontentloaded")
    _dismiss_cookie_banner(page)
    _wait_for_page_ready(page)
    if _looks_guestcenter_logged_out(page):
        raise RuntimeError(
            "GuestCenter is asking for login. Run `python run.py login`, finish login manually, "
            "then press Enter only after the reservations page is visible."
        )

    _admin_progress("opening reservation flow")
    _open_admin_reservation_modal(page, selectors)
    _admin_progress("setting date")
    _set_admin_date(page, reservation.date, selectors)
    full_name = f"{reservation.guest.first_name} {reservation.guest.last_name}".strip()
    _admin_progress("setting guests")
    _set_admin_party_size(page, reservation.party_size, selectors)
    _admin_progress("setting time")
    _set_admin_time(page, reservation.time, selectors)
    _admin_progress("setting guest")
    _set_admin_guest(page, reservation, selectors)
    _admin_progress("setting notes")
    _fill_configured_or_patterns(
        page,
        selectors.get("notes"),
        [r"notes?", r"special request", r"internal note"],
        reservation.special_request,
        required=False,
    )

    if not confirm:
        return BookingResult(
            status="ready",
            url=page.url,
            message=(
                "Filled the GuestCenter reservation form and stopped before save. "
                "Review the browser, then rerun with `admin-book --confirm`."
            ),
        )

    if _is_dinner_time(reservation.time):
        _admin_progress("enabling credit card link switch for dinner")
        _set_admin_credit_card_link_checked(page, checked=True, required=True)
    else:
        _admin_progress("disabling credit card link switch for lunch")
        _set_admin_credit_card_link_checked(page, checked=False, required=False)
    _admin_progress(f"waiting {FINAL_CTA_DELAY_SECONDS}s before Make reservation")
    sleep(FINAL_CTA_DELAY_SECONDS)
    _admin_progress("clicking Make reservation")
    _click_admin_make_reservation(page, selectors.get("save_button"))
    _wait_for_page_ready(page)
    return BookingResult(
        status="submitted",
        url=page.url,
        message="Clicked the GuestCenter final save/create button.",
    )


def _admin_progress(message: str) -> None:
    print(f"[admin-book] {message}", flush=True)


def book_reservation(
    context: BrowserContext,
    config: AppConfig,
    *,
    confirm: bool,
) -> BookingResult:
    reservation = config.reservation
    page = _page(context)
    page.goto(reservation.restaurant_url, wait_until="domcontentloaded")
    _dismiss_cookie_banner(page)
    _wait_for_page_ready(page)

    _set_party_size(page, reservation.party_size)
    _set_date(page, reservation.date)
    _set_time(page, reservation.time)
    _click_find_table(page)
    _select_requested_time(page, reservation.time)

    _fill_guest_details(page, reservation)

    if not confirm:
        return BookingResult(
            status="ready",
            url=page.url,
            message=(
                "Stopped before final confirmation. Review the browser, then run with "
                "`--confirm` if everything is correct."
            ),
        )

    _click_final_confirm(page)
    _wait_for_page_ready(page)
    return BookingResult(
        status="submitted",
        url=page.url,
        message="Clicked the final booking button. Check the browser/account for confirmation.",
    )


def save_artifacts(context: BrowserContext, base_dir: Path, name: str) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    page = _page(context)
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-") or "opentable"
    page.screenshot(path=str(base_dir / f"{safe_name}.png"), full_page=True)
    (base_dir / f"{safe_name}.html").write_text(page.content(), encoding="utf-8")


def save_failure_diagnostics(
    context: BrowserContext,
    base_dir: Path,
    name: str,
    exc: Exception,
) -> dict[str, Any]:
    base_dir.mkdir(parents=True, exist_ok=True)
    page = _page(context)
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-") or "opentable-error"
    screenshot_path = base_dir / f"{safe_name}.png"
    html_path = base_dir / f"{safe_name}.html"
    text_path = base_dir / f"{safe_name}.txt"
    json_path = base_dir / f"{safe_name}.diagnostics.json"

    title = ""
    visible_text = ""
    try:
        title = page.title(timeout=2000)
    except Exception:
        title = ""
    try:
        visible_text = _visible_text(page)
    except Exception:
        visible_text = ""
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception:
        screenshot_path = None
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception:
        html_path = None

    text_excerpt = _diagnostic_text_excerpt(visible_text)
    try:
        text_path.write_text(text_excerpt, encoding="utf-8")
    except Exception:
        text_path = None

    events = list(_DIAGNOSTIC_STORES.get(id(context), []))[-DIAGNOSTIC_EVENT_LIMIT:]
    diagnostics: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "error": str(exc),
        "url": page.url,
        "title": title,
        "visibleTextExcerpt": text_excerpt,
        "recentBrowserEvents": events,
        "artifacts": {
            "screenshot": str(screenshot_path) if screenshot_path else None,
            "html": str(html_path) if html_path else None,
            "text": str(text_path) if text_path else None,
            "diagnostics": str(json_path),
        },
    }
    json_path.write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False), encoding="utf-8")
    return diagnostics


def format_failure_message(error: str, diagnostics: dict[str, Any]) -> str:
    parts = [error]
    url = diagnostics.get("url")
    title = diagnostics.get("title")
    if title:
        parts.append(f"Page title: {title}")
    if url:
        parts.append(f"Page URL: {url}")

    events = diagnostics.get("recentBrowserEvents") or []
    important_events = [
        event for event in events
        if event.get("level") in {"error", "requestfailed", "pageerror", "http"}
    ][-5:]
    if important_events:
        event_lines = []
        for event in important_events:
            message = event.get("message") or event.get("url") or event.get("text") or ""
            event_lines.append(f"{event.get('level')}: {_compact_text(str(message))[:180]}")
        parts.append("Recent browser issues: " + " | ".join(event_lines))

    text_excerpt = diagnostics.get("visibleTextExcerpt")
    if text_excerpt:
        parts.append(f"Visible page text: {_compact_text(str(text_excerpt))[:DIAGNOSTIC_TEXT_LIMIT]}")

    artifacts = diagnostics.get("artifacts") or {}
    diagnostics_path = artifacts.get("diagnostics")
    screenshot_path = artifacts.get("screenshot")
    if diagnostics_path:
        parts.append(f"Diagnostics: {diagnostics_path}")
    if screenshot_path:
        parts.append(f"Screenshot: {screenshot_path}")
    return "\n".join(parts)


def _ensure_browser_diagnostics(context: BrowserContext) -> None:
    context_id = id(context)
    _DIAGNOSTIC_STORES.setdefault(context_id, [])
    if context_id not in _DIAGNOSTIC_CONTEXT_IDS:
        _DIAGNOSTIC_CONTEXT_IDS.add(context_id)
        try:
            context.on("page", lambda page: _attach_page_diagnostics(context, page))
        except Exception:
            pass
    for page in context.pages:
        _attach_page_diagnostics(context, page)


def _attach_page_diagnostics(context: BrowserContext, page: Page) -> None:
    page_id = id(page)
    if page_id in _DIAGNOSTIC_PAGE_IDS:
        return
    _DIAGNOSTIC_PAGE_IDS.add(page_id)

    try:
        page.on(
            "console",
            lambda msg: _record_browser_event(
                context,
                {
                    "level": msg.type,
                    "message": msg.text,
                    "location": msg.location,
                    "url": page.url,
                },
            ) if msg.type in {"error", "warning"} else None,
        )
    except Exception:
        pass
    try:
        page.on(
            "pageerror",
            lambda error: _record_browser_event(
                context,
                {"level": "pageerror", "message": str(error), "url": page.url},
            ),
        )
    except Exception:
        pass
    try:
        page.on(
            "requestfailed",
            lambda request: _record_browser_event(
                context,
                {
                    "level": "requestfailed",
                    "method": request.method,
                    "url": request.url,
                    "message": request.failure or "",
                },
            ),
        )
    except Exception:
        pass
    try:
        page.on("response", lambda response: _record_http_response_event(context, response))
    except Exception:
        pass


def _record_http_response_event(context: BrowserContext, response) -> None:
    try:
        status = int(response.status)
        url = str(response.url)
    except Exception:
        return
    if status < 400:
        return
    if "opentable" not in url.lower() and "guestcenter" not in url.lower():
        return
    _record_browser_event(
        context,
        {
            "level": "http",
            "status": status,
            "url": url,
            "message": f"HTTP {status}",
        },
    )


def _record_browser_event(context: BrowserContext, event: dict[str, Any]) -> None:
    store = _DIAGNOSTIC_STORES.setdefault(id(context), [])
    store.append({"timestamp": datetime.now().isoformat(timespec="seconds"), **event})
    if len(store) > DIAGNOSTIC_EVENT_LIMIT:
        del store[:len(store) - DIAGNOSTIC_EVENT_LIMIT]


def _diagnostic_text_excerpt(text: str) -> str:
    lines = _summarize_lines(text)
    return "\n".join(lines[:20])[:DIAGNOSTIC_TEXT_LIMIT]


def _page(context: BrowserContext) -> Page:
    if context.pages:
        return context.pages[0]
    return context.new_page()


def _dismiss_cookie_banner(page: Page) -> None:
    for name in ("Accept all", "Accept", "I agree", "Got it", "Allow all"):
        try:
            page.get_by_role("button", name=re.compile(name, re.I)).click(timeout=1500)
            return
        except PlaywrightTimeoutError:
            continue


def _attempt_login(page: Page, email: str, password: str) -> bool:
    _open_sign_in_form(page)

    email_filled = _fill_first_matching(
        page,
        [
            ("label", r"email|username|user name"),
            ("placeholder", r"email|username|user name"),
            ("selector", "input[type='email']"),
            ("selector", "input[name*='email' i]"),
            ("selector", "input[name*='user' i]"),
            ("selector", "input[id*='email' i]"),
            ("selector", "input[id*='user' i]"),
            ("selector", "input[autocomplete='username']"),
        ],
        email,
    )

    _click_continue_if_present(page)

    password_filled = _fill_first_matching(
        page,
        [
            ("label", r"password"),
            ("placeholder", r"password"),
            ("selector", "input[type='password']"),
            ("selector", "input[name*='password' i]"),
        ],
        password,
    )

    if not email_filled or not password_filled:
        return False

    for pattern in (r"sign in", r"log in", r"continue", r"submit"):
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).click(timeout=3000)
            _wait_for_page_ready(page)
            return True
        except PlaywrightTimeoutError:
            continue
    page.keyboard.press("Enter")
    _wait_for_page_ready(page)
    return True


def _open_sign_in_form(page: Page) -> None:
    for pattern in (r"sign in", r"log in", r"profile", r"account"):
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).click(timeout=2500)
            _wait_for_page_ready(page)
            return
        except PlaywrightTimeoutError:
            try:
                page.get_by_role("link", name=re.compile(pattern, re.I)).click(timeout=2500)
                _wait_for_page_ready(page)
                return
            except PlaywrightTimeoutError:
                continue


def _click_continue_if_present(page: Page) -> None:
    for pattern in (r"continue", r"next"):
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).click(timeout=1500)
            _wait_for_page_ready(page)
            return
        except PlaywrightTimeoutError:
            continue


def _fill_first_matching(page: Page, locators: list[tuple[str, str]], value: str) -> bool:
    for kind, pattern in locators:
        try:
            if kind == "label":
                page.get_by_label(re.compile(pattern, re.I)).fill(value, timeout=2000)
            elif kind == "placeholder":
                page.get_by_placeholder(re.compile(pattern, re.I)).fill(value, timeout=2000)
            else:
                page.locator(pattern).first.fill(value, timeout=2000)
            return True
        except PlaywrightTimeoutError:
            continue
    return False


def _set_admin_date(page: Page, date_value: str, selectors: dict[str, str]) -> None:
    target = date.fromisoformat(date_value)
    _go_to_admin_step(page, "date", selectors.get("date_tab"))

    date_selector = selectors.get("date")
    if date_selector:
        locator = page.locator(date_selector).first
        try:
            _set_locator_value(locator, date_value)
            return
        except PlaywrightTimeoutError:
            try:
                locator.click(timeout=2000)
                return
            except PlaywrightTimeoutError:
                pass

    current = _visible_calendar_month(page)
    if current:
        delta = (target.year - current.year) * 12 + target.month - current.month
    else:
        today = date.today()
        delta = (target.year - today.year) * 12 + target.month - today.month

    if delta > 0:
        for _ in range(delta):
            _click_calendar_arrow(page, selectors.get("next_month_button"), "next")
            sleep(0.1)
    elif delta < 0:
        for _ in range(abs(delta)):
            _click_calendar_arrow(page, selectors.get("previous_month_button"), "previous")
            sleep(0.1)

    _click_calendar_day(page, target)


def _visible_calendar_month(page: Page) -> date | None:
    header = page.locator("[class*='DatePicker__header']").first
    try:
        text = header.inner_text(timeout=2000)
    except PlaywrightTimeoutError:
        text = _visible_text(page)
    month = _parse_month_header(text)
    if month:
        return month

    text = _visible_text(page)
    return _parse_month_header(text)


def _parse_month_header(text: str) -> date | None:
    month_names = "|".join(calendar.month_name[1:])
    match = re.search(rf"\b({month_names})\s+(\d{{4}})\b", text, re.I)
    if not match:
        return None
    month = next(
        index
        for index, name in enumerate(calendar.month_name)
        if name.lower() == match.group(1).lower()
    )
    return date(int(match.group(2)), month, 1)


def _click_calendar_arrow(page: Page, selector: str | None, direction: str) -> None:
    if selector:
        if _click_first_enabled(page.locator(selector), timeout=5000):
            return

    if _click_calendar_iframe_arrow(page, direction):
        return

    icon_class = "ic-right" if direction == "next" else "ic-left"
    candidates = [
        page.locator(f"button:has(span.gc-icon.{icon_class})"),
        page.locator(
            f"xpath=//button[.//span[contains(concat(' ', normalize-space(@class), ' '), ' gc-icon ') "
            f"and contains(concat(' ', normalize-space(@class), ' '), ' {icon_class} ')]]"
        ),
        page.locator(
            f"xpath=//*[contains(concat(' ', normalize-space(@class), ' '), ' DatePicker__header')]/following::button"
            f"[.//span[contains(concat(' ', normalize-space(@class), ' '), ' {icon_class} ')]][1]"
        ),
    ]
    for candidate in candidates:
        if _click_first_enabled(candidate, timeout=2000):
            return

    names = [r"next month", r"next", r">"] if direction == "next" else [r"previous month", r"previous", r"prev", r"<"]
    for name in names:
        try:
            page.get_by_role("button", name=re.compile(name, re.I)).click(timeout=2000)
            return
        except PlaywrightTimeoutError:
            continue
    if _click_calendar_arrow_from_point(page, direction):
        return
    if _click_calendar_svg_arrow(page, direction):
        return
    if _click_calendar_arrow_by_coordinates(page, direction):
        return
    raise RuntimeError(f"Could not find {direction} month calendar arrow.")


def _click_calendar_iframe_arrow(page: Page, direction: str) -> bool:
    button_index = 4 if direction == "next" else 3
    try:
        page.frame_locator("iframe").get_by_role("button").nth(button_index).click(timeout=3000)
        return True
    except Exception:
        pass

    for frame in _admin_iframes(page):
        try:
            frame.get_by_role("button").nth(button_index).click(timeout=3000)
            return True
        except Exception:
            continue
    return False


def _iframe_button_counts(page: Page) -> list[dict[str, str | int]]:
    counts: list[dict[str, str | int]] = []
    for index, frame in enumerate(_admin_iframes(page), start=1):
        try:
            count = frame.get_by_role("button").count()
        except Exception:
            count = -1
        counts.append({"index": index, "buttons": count, "url": frame.url})
    return counts


def _format_iframe_button_counts(counts: list[dict[str, str | int]]) -> str:
    if not counts:
        return "0 iframes"
    return "; ".join(
        f"frame {item['index']}: {item['buttons']} buttons"
        for item in counts
    )


def _admin_iframes(page: Page):
    return [frame for frame in page.frames if frame != page.main_frame]


def _click_calendar_day(page: Page, target: date) -> None:
    day = str(target.day)
    cells = page.locator("[class*='DatePicker__DayCell']")
    try:
        count = min(cells.count(), 80)
        for index in range(count):
            cell = cells.nth(index)
            number = cell.locator("[class*='DatePicker__number']").first
            try:
                if number.inner_text(timeout=500).strip() != day:
                    continue
            except PlaywrightTimeoutError:
                continue
            if _element_looks_disabled(cell):
                continue
            cell.click(timeout=2000)
            return
    except PlaywrightTimeoutError:
        pass

    candidates = [page.get_by_text(re.compile(rf"^{day}$"))]
    for candidate in candidates:
        try:
            elements = candidate
            count = min(elements.count(), 10)
            for index in range(count):
                element = elements.nth(index)
                if _element_looks_disabled(element):
                    continue
                element.click(timeout=2000)
                return
        except PlaywrightTimeoutError:
            continue
    if _click_calendar_day_by_coordinates(page, target):
        return
    raise RuntimeError(f"Could not click calendar day {target.isoformat()}.")


def _set_admin_party_size(page: Page, party_size: int, selectors: dict[str, str]) -> None:
    if not _go_to_admin_step(page, "party size", selectors.get("party_size_tab")):
        _go_to_admin_step(page, "guests", None)
    value = str(party_size)

    selector = selectors.get("party_size")
    if selector:
        if _set_admin_field_by_selector(page, selector, value):
            return

    for frame in _admin_iframes(page):
        if _set_party_size_in_scope(frame, party_size):
            return

    if _set_party_size_in_scope(page, party_size):
        return

    if _fill_configured_or_patterns(
        page,
        selectors.get("party_size"),
        [r"party size", r"covers", r"guests?", r"people"],
        value,
        required=False,
    ):
        return

    raise RuntimeError(f"Could not set GuestCenter party size to {party_size}.")


def _set_admin_time(page: Page, time_value: str, selectors: dict[str, str]) -> None:
    _go_to_admin_step(page, "time", selectors.get("time_tab"))

    selector = selectors.get("time")
    if selector:
        if _set_admin_field_by_selector(page, selector, time_value):
            return

    labels = _time_labels(time_value)
    primary_period = "Dinner" if _is_dinner_time(time_value) else "Lunch"
    fallback_period = "Lunch" if primary_period == "Dinner" else "Dinner"
    for service_period in (primary_period, fallback_period):
        for scope in [*_admin_iframes(page), page]:
            if _select_admin_service_period(scope, service_period):
                break
        if _click_admin_time_labels(page, labels):
            return

    if _fill_configured_or_patterns(
        page,
        selectors.get("time"),
        [r"time"],
        time_value,
        required=False,
    ):
        return

    raise RuntimeError(f"Could not set GuestCenter time to {time_value}.")


def _set_admin_guest(page: Page, reservation: ReservationConfig, selectors: dict[str, str]) -> None:
    _go_to_admin_step(page, "guest", selectors.get("guest_tab"))
    full_name = f"{reservation.guest.first_name} {reservation.guest.last_name}".strip()
    search_value = full_name or reservation.guest.phone

    if search_value:
        for scope in [*_admin_iframes(page), page]:
            if _search_or_create_admin_guest(scope, search_value, reservation):
                return

    _fill_admin_guest_fields(page, reservation, selectors)


def _search_or_create_admin_guest(scope, search_value: str, reservation: ReservationConfig) -> bool:
    search = scope.get_by_placeholder("Search by full phone number")
    try:
        search.fill(search_value, timeout=1000)
    except PlaywrightTimeoutError:
        return False
    try:
        search.press("Enter", timeout=1000)
    except PlaywrightTimeoutError:
        pass
    sleep(0.1)

    add_to_guestbook = scope.locator("a").filter(has_text="Add to guestbook").first
    try:
        add_to_guestbook.click(timeout=1000)
        sleep(0.1)
        _fill_guest_fields_in_scope(scope, reservation)
        return True
    except PlaywrightTimeoutError:
        return False


def _fill_admin_guest_fields(page: Page, reservation: ReservationConfig, selectors: dict[str, str]) -> None:
    full_name = f"{reservation.guest.first_name} {reservation.guest.last_name}".strip()
    if full_name:
        _fill_configured_or_patterns(
            page,
            selectors.get("guest_name"),
            [r"guest name", r"name"],
            full_name,
            required=False,
        )
    _fill_configured_or_patterns(
        page,
        selectors.get("first_name"),
        [r"first name"],
        reservation.guest.first_name,
        required=False,
    )
    _fill_configured_or_patterns(
        page,
        selectors.get("last_name"),
        [r"last name", r"surname"],
        reservation.guest.last_name,
        required=False,
    )
    _fill_configured_or_patterns(
        page,
        selectors.get("phone"),
        [r"phone", r"mobile"],
        reservation.guest.phone,
        required=False,
    )


def _fill_guest_fields_in_scope(scope, reservation: ReservationConfig) -> None:
    fields = [
        (r"first name", reservation.guest.first_name),
        (r"last name|surname", reservation.guest.last_name),
        (r"phone|mobile", reservation.guest.phone),
    ]
    for label_pattern, value in fields:
        if not value:
            continue
        _fill_field_in_scope(scope, label_pattern, value)


def _fill_field_in_scope(scope, label_pattern: str, value: str) -> bool:
    pattern = re.compile(label_pattern, re.I)
    for getter in (
        lambda: scope.get_by_label(pattern),
        lambda: scope.get_by_placeholder(pattern),
    ):
        try:
            if _fill_first_editable(getter(), value):
                return True
        except Exception:
            continue
    token = _simple_selector_token(label_pattern)
    for selector in (
        f"input[name*='{token}' i]",
        f"input[id*='{token}' i]",
        f"textarea[name*='{token}' i]",
        f"textarea[id*='{token}' i]",
    ):
        try:
            if _fill_first_editable(scope.locator(selector), value):
                return True
        except Exception:
            continue
    return False


def _fill_first_editable(locator, value: str) -> bool:
    try:
        count = min(locator.count(), 20)
    except Exception:
        count = 1

    for index in range(count):
        item = locator.nth(index)
        try:
            if not item.evaluate(
                """element => {
                    const tag = element.tagName.toLowerCase();
                    const type = String(element.getAttribute('type') || '').toLowerCase();
                    return (
                        !element.disabled &&
                        (tag === 'textarea' || (tag === 'input' && !['checkbox', 'radio', 'button', 'submit'].includes(type)))
                    );
                }""",
                timeout=1000,
            ):
                continue
            item.fill(value, timeout=1500)
            return True
        except Exception:
            continue
    return False


def _click_admin_time_labels(page: Page, labels: list[str]) -> bool:
    for scope in [*_admin_iframes(page), page]:
        for label in labels:
            if _click_admin_availability_slot(scope, label):
                return True
            if _click_time_label_in_scope(scope, label):
                return True
            pattern = re.compile(rf"\b{re.escape(label)}\b", re.I)
            try:
                scope.get_by_text(pattern).click(timeout=700)
                return True
            except Exception:
                continue
    return False


def _click_admin_availability_slot(scope, label: str) -> bool:
    pattern = re.compile(rf"^{re.escape(label)}$", re.I)
    value = scope.locator("[class*='AvailabilitySlot__slot__value']").filter(
        has_text=pattern
    )
    try:
        value.first.wait_for(state="visible", timeout=2500)
    except Exception:
        return False

    rows = scope.locator("li[class*='AvailabilitySlot__slot___']").filter(
        has=value
    )
    return _click_first_matching(rows, timeout=2000)


def _select_admin_service_period(scope, period: str) -> bool:
    pattern = re.compile(rf"^{re.escape(period)}$", re.I)
    candidates = (
        scope.locator("[class*='AvailabilityShift__shifts__item']").filter(has_text=pattern),
        scope.locator("a").filter(has_text=pattern),
        scope.get_by_text(pattern),
    )
    for locator in candidates:
        try:
            count = min(locator.count(), 10)
        except Exception:
            continue
        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible(timeout=500):
                    continue
                item.click(timeout=1500)
                scope.locator(
                    "[class*='AvailabilityShift__shifts__item_active']"
                ).filter(has_text=pattern).wait_for(state="visible", timeout=2000)
                return True
            except Exception:
                continue
    return False


def _click_exact_text_in_scope(scope, text: str) -> bool:
    pattern = re.compile(rf"^{re.escape(text)}$", re.I)
    for getter in (
        lambda: scope.get_by_role("button", name=pattern),
        lambda: scope.get_by_role("option", name=pattern),
        lambda: scope.get_by_text(pattern),
    ):
        try:
            getter().click(timeout=700)
            return True
        except PlaywrightTimeoutError:
            continue
    return False


def _click_time_label_in_scope(scope, text: str) -> bool:
    pattern = re.compile(rf"^{re.escape(text)}$", re.I)
    for getter in (
        lambda: scope.get_by_role("button", name=pattern),
        lambda: scope.get_by_role("option", name=pattern),
    ):
        try:
            locator = getter()
            count = locator.count()
            if count > 2:
                locator.nth(2).click(timeout=700)
                return True
            if count:
                locator.first.click(timeout=700)
                return True
        except Exception:
            continue

    try:
        locator = scope.get_by_text(pattern)
        count = locator.count()
        if count > 2:
            locator.nth(2).click(timeout=700)
            return True
        for index in range(count):
            try:
                locator.nth(index).click(timeout=700)
                return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def _time_minutes(time_value: str) -> int:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", time_value.strip())
    if not match:
        raise ValueError(f"Time must be normalized as HH:MM, got {time_value!r}")
    return int(match.group(1)) * 60 + int(match.group(2))


def _is_dinner_time(time_value: str) -> bool:
    return _time_minutes(time_value) > (17 * 60)


def _time_labels(time_value: str) -> list[str]:
    display_time = _format_time_for_ui(time_value)
    labels = [display_time, display_time.lower(), time_value]
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", time_value.strip())
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        suffix = "AM" if hour < 12 else "PM"
        hour_12 = hour % 12 or 12
        if minute == 0:
            labels.extend(
                [
                    f"{hour_12} {suffix}",
                    f"{hour_12}{suffix}",
                    f"{hour_12} {suffix.lower()}",
                    f"{hour_12}{suffix.lower()}",
                ]
            )
    return list(dict.fromkeys(labels))


def _set_admin_field_by_selector(page: Page, selector: str, value: str) -> bool:
    scopes = [page, *_admin_iframes(page)]
    for scope in scopes:
        try:
            _set_locator_value(scope.locator(selector).first, value)
            return True
        except Exception:
            continue
    return False


def _set_party_size_in_scope(scope, party_size: int) -> bool:
    value = str(party_size)
    labels = [
        f"{party_size} guests",
        f"{party_size} guest",
        f"{party_size} people",
        f"{party_size} person",
        value,
        f"party size {party_size}",
    ]

    selectors = [
        "select[name*='party' i]",
        "select[id*='party' i]",
        "select[data-testid*='party' i]",
        "select[name*='guest' i]",
        "select[id*='guest' i]",
        "select[data-testid*='guest' i]",
        "input[name*='party' i]",
        "input[id*='party' i]",
        "input[data-testid*='party' i]",
        "input[name*='guest' i]",
        "input[id*='guest' i]",
        "input[data-testid*='guest' i]",
    ]
    for selector in selectors:
        locator = scope.locator(selector).first
        try:
            _set_locator_value(locator, value)
            return True
        except Exception:
            continue

    for label in labels:
        pattern = re.compile(rf"^{re.escape(label)}$", re.I)
        if _click_first_matching(scope.get_by_role("button", name=pattern), timeout=700):
            return True
        if _click_first_matching(scope.get_by_role("option", name=pattern), timeout=700):
            return True
        if _click_first_matching(scope.get_by_text(pattern), timeout=700):
            return True

    for label_pattern in (r"party size", r"covers", r"guests?", r"people"):
        try:
            scope.get_by_label(re.compile(label_pattern, re.I)).fill(value, timeout=700)
            return True
        except PlaywrightTimeoutError:
            pass
        try:
            scope.get_by_placeholder(re.compile(label_pattern, re.I)).fill(value, timeout=700)
            return True
        except PlaywrightTimeoutError:
            continue

    return False


def _click_first_matching(locator, *, timeout: int) -> bool:
    try:
        count = min(locator.count(), 20)
    except Exception:
        count = 1

    for index in range(count):
        try:
            item = locator.nth(index)
            if _element_looks_disabled(item):
                continue
            item.click(timeout=timeout)
            return True
        except Exception:
            continue
    return False


def _calendar_anchor_box(page: Page) -> dict[str, float] | None:
    anchors = [
        page.get_by_text(re.compile(r"^Select a date$", re.I)),
        page.get_by_text(re.compile(r"\bSelect a date\b", re.I)),
    ]
    for anchor in anchors:
        try:
            count = min(anchor.count(), 3)
            for index in range(count):
                box = anchor.nth(index).bounding_box(timeout=1000)
                if box:
                    return box
        except PlaywrightTimeoutError:
            continue
    return None


def _calendar_svg_arrow_candidates(page: Page) -> list[dict[str, float]]:
    candidates: list[dict[str, float]] = []
    for frame in page.frames:
        candidates.extend(_calendar_svg_arrow_candidates_in_frame(frame))
    candidates.sort(key=lambda candidate: candidate["x"])
    return candidates


def _calendar_svg_arrow_candidates_in_frame(frame) -> list[dict[str, float]]:
    try:
        return frame.evaluate(
            """() => {
                const collectButtons = root => {
                    const buttons = Array.from(root.querySelectorAll('button'));
                    for (const element of root.querySelectorAll('*')) {
                        if (element.shadowRoot) {
                            buttons.push(...collectButtons(element.shadowRoot));
                        }
                    }
                    return buttons;
                };

                const visible = element => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return (
                        rect.width >= 16 &&
                        rect.width <= 64 &&
                        rect.height >= 16 &&
                        rect.height <= 64 &&
                        rect.x >= 500 &&
                        rect.y >= 180 &&
                        rect.y <= 360 &&
                        style.visibility !== 'hidden' &&
                        style.display !== 'none' &&
                        style.opacity !== '0'
                    );
                };

                return collectButtons(document)
                    .filter(button => {
                        const ariaDisabled = button.getAttribute('aria-disabled') === 'true';
                        return button.querySelector('svg') && !button.disabled && !ariaDisabled && visible(button);
                    })
                    .map(button => {
                        const rect = button.getBoundingClientRect();
                        return {
                            x: rect.x + rect.width / 2,
                            y: rect.y + rect.height / 2,
                            left: rect.x,
                            top: rect.y,
                            width: rect.width,
                            height: rect.height
                        };
                    })
                    .sort((a, b) => a.x - b.x);
            }"""
        )
    except Exception:
        return []


def _format_svg_arrow_candidates(candidates: list[dict[str, float]]) -> str:
    if not candidates:
        return "0"
    points = ", ".join(f"({candidate['x']:.0f},{candidate['y']:.0f})" for candidate in candidates[:6])
    suffix = "" if len(candidates) <= 6 else f", +{len(candidates) - 6} more"
    return f"{len(candidates)} {points}{suffix}"


def _click_calendar_svg_arrow(page: Page, direction: str) -> bool:
    candidates = _calendar_svg_arrow_candidates(page)
    if not candidates:
        return False
    candidate = candidates[-1] if direction == "next" else candidates[0]
    page.mouse.click(candidate["x"], candidate["y"])
    return True


def _calendar_arrow_point(page: Page, direction: str) -> tuple[float, float]:
    box = _calendar_anchor_box(page)
    if box:
        x_offset = 655 if direction == "next" else 607
        return box["x"] + x_offset, box["y"] + 48
    x = CALENDAR_NEXT_X if direction == "next" else CALENDAR_PREVIOUS_X
    return float(x), float(CALENDAR_ARROW_Y)


def _element_from_point_summary(page: Page, x: float, y: float) -> str:
    try:
        return str(
            page.evaluate(
                """([x, y]) => {
                    const element = document.elementFromPoint(x, y);
                    if (!element) return 'none';
                    const parts = [];
                    let current = element;
                    for (let i = 0; current && i < 5; i += 1) {
                        const tag = current.tagName ? current.tagName.toLowerCase() : String(current.nodeName);
                        const id = current.id ? `#${current.id}` : '';
                        const classes = current.className && typeof current.className === 'string'
                            ? `.${current.className.trim().replace(/\\s+/g, '.')}`
                            : '';
                        parts.push(`${tag}${id}${classes}`);
                        current = current.parentElement;
                    }
                    return parts.join(' <- ');
                }""",
                [x, y],
            )
        )
    except Exception as exc:
        return f"error: {exc}"


def _click_calendar_arrow_from_point(page: Page, direction: str) -> bool:
    x, y = _calendar_arrow_point(page, direction)
    try:
        clicked = bool(
            page.evaluate(
                """([x, y]) => {
                    const element = document.elementFromPoint(x, y);
                    const button = element && element.closest ? element.closest('button') : null;
                    if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') {
                        return false;
                    }
                    button.click();
                    return true;
                }""",
                [x, y],
            )
        )
        if clicked:
            return True
    except Exception:
        return False
    return False


def _click_calendar_arrow_by_coordinates(page: Page, direction: str) -> bool:
    x, y = _calendar_arrow_point(page, direction)
    page.mouse.click(x, y)
    return True


def _click_calendar_day_by_coordinates(page: Page, target: date) -> bool:
    box = _calendar_anchor_box(page)
    if box:
        grid_x = box["x"]
        grid_y = box["y"] + 118
    else:
        grid_x = CALENDAR_GRID_X
        grid_y = CALENDAR_GRID_Y

    first_day = date(target.year, target.month, 1)
    first_weekday_sunday_first = (first_day.weekday() + 1) % 7
    index = first_weekday_sunday_first + target.day - 1
    row = index // 7
    col = index % 7
    x = grid_x + (col * CALENDAR_CELL_WIDTH) + (CALENDAR_CELL_WIDTH / 2)
    y = grid_y + (row * CALENDAR_CELL_HEIGHT) + (CALENDAR_CELL_HEIGHT / 2)
    page.mouse.click(x, y)
    return True


def _open_admin_reservation_modal(page: Page, selectors: dict[str, str]) -> None:
    if _admin_reservation_modal_is_open(page):
        return

    scopes = [page, *_admin_iframes(page)]
    selector = selectors.get("new_reservation_button")
    if selector:
        for scope in scopes:
            if _click_first_enabled(scope.locator(selector), timeout=1000, force=True):
                _wait_for_page_ready(page)
                sleep(0.1)
                return

    for scope in scopes:
        if _click_first_enabled(
            scope.locator('[data-testid="addReservationActionContainer"]'),
            timeout=1000,
            force=True,
        ):
            _wait_for_page_ready(page)
            sleep(0.1)
            return

    patterns = [
        r"make a reservation",
        r"new reservation",
        r"create reservation",
        r"add reservation",
        r"book reservation",
        r"reserve",
        r"new booking",
    ]
    for scope in scopes:
        if _click_admin_action_by_patterns(scope, patterns):
            _wait_for_page_ready(page)
            sleep(0.1)
            return

    if _admin_reservation_modal_is_open(page):
        return
    raise RuntimeError(f"Could not find clickable control matching: {', '.join(patterns)}")


def _admin_reservation_modal_is_open(page: Page) -> bool:
    for scope in [page, *_admin_iframes(page)]:
        for locator in (
            scope.get_by_test_id("save-booking-flow-button"),
            scope.get_by_text(re.compile(r"^Make reservation$", re.I)),
            scope.get_by_placeholder("Search by full phone number"),
        ):
            try:
                if locator.first.is_visible(timeout=500):
                    return True
            except Exception:
                continue
    return False


def _click_admin_action_by_patterns(scope, patterns: list[str]) -> bool:
    for pattern in patterns:
        compiled = re.compile(pattern, re.I)
        for getter in (
            lambda compiled=compiled: scope.get_by_role("button", name=compiled),
            lambda compiled=compiled: scope.get_by_role("link", name=compiled),
            lambda compiled=compiled: scope.get_by_text(compiled),
        ):
            try:
                getter().first.click(timeout=600)
                return True
            except Exception:
                continue
    return False


def _click_admin_make_reservation(page: Page, selector: str | None) -> None:
    scopes = [*_admin_iframes(page), page]
    if selector:
        for scope in scopes:
            if _click_first_enabled(scope.locator(selector), timeout=1000):
                return

    for scope in scopes:
        if _click_first_enabled(scope.get_by_test_id("save-booking-flow-button"), timeout=1000):
            return

    labels = [
        r"^Make reservation$",
        r"^Save$",
        r"^Create$",
        r"^Book$",
        r"^Confirm$",
    ]
    for scope in scopes:
        for label in labels:
            pattern = re.compile(label, re.I)
            for getter in (
                lambda scope=scope, pattern=pattern: scope.get_by_role("button", name=pattern),
                lambda scope=scope, pattern=pattern: scope.get_by_text(pattern),
            ):
                try:
                    getter().first.click(timeout=800)
                    return
                except Exception:
                    continue

    raise RuntimeError("Could not find GuestCenter Make reservation button.")


def _set_admin_credit_card_link_checked(page: Page, *, checked: bool, required: bool) -> None:
    scopes = [*_admin_iframes(page), page]
    for scope in scopes:
        if _set_checkbox_checked(
            scope.get_by_test_id("switch-checkbox-directDinerPaymentOptInToggle"),
            scope.get_by_test_id("switch-label-directDinerPaymentOptInToggle"),
            checked=checked,
        ):
            return
        if _set_checkbox_checked(
            scope.locator("#directDinerPaymentOptInToggle"),
            scope.locator("label[for='directDinerPaymentOptInToggle']"),
            checked=checked,
        ):
            return
        label = scope.locator("label").filter(
            has_text=re.compile(r"send credit card link to secure reservation", re.I)
        )
        if _set_checkbox_checked(label.locator("input[type='checkbox']"), label, checked=checked):
            return

    if required:
        raise RuntimeError("Could not check 'Send credit card link to secure reservation'.")


def _set_checkbox_checked(checkbox_locator, click_locator=None, *, checked: bool) -> bool:
    try:
        count = min(checkbox_locator.count(), 5)
    except Exception:
        count = 1

    for index in range(count):
        checkbox = checkbox_locator.nth(index)
        try:
            checkbox.wait_for(state="attached", timeout=600)
            if checkbox.is_checked(timeout=500) == checked:
                return True
        except Exception:
            continue

        targets = []
        if click_locator is not None:
            targets.append(click_locator.nth(index) if count > 1 else click_locator.first)
        targets.append(checkbox)

        for target in targets:
            try:
                target.click(timeout=800)
                sleep(0.1)
                if checkbox.is_checked(timeout=500) == checked:
                    return True
            except Exception:
                continue
            try:
                target.click(timeout=800, force=True)
                sleep(0.1)
                if checkbox.is_checked(timeout=500) == checked:
                    return True
            except Exception:
                continue

    return False


def _click_first_enabled(locator, *, timeout: int, force: bool = False) -> bool:
    try:
        count = min(locator.count(), 20)
    except Exception:
        count = 1

    for index in range(count):
        item = locator.nth(index)
        try:
            if not force and _element_looks_disabled(item):
                continue
            item.click(timeout=timeout, force=force)
            return True
        except PlaywrightTimeoutError:
            continue
    return False


def _element_looks_disabled(locator) -> bool:
    try:
        return bool(
            locator.evaluate(
                """element => {
                    const aria = element.getAttribute('aria-disabled') === 'true';
                    const disabled = Boolean(element.disabled);
                    const klass = String(element.className || '').toLowerCase();
                    return aria || disabled || klass.includes('disabled');
                }""",
                timeout=1000,
            )
        )
    except Exception:
        return False


def _go_to_admin_step(page: Page, label: str, selector: str | None) -> bool:
    scopes = [*_admin_iframes(page), page]
    exact_label = re.compile(rf"^{re.escape(label)}$", re.I)

    if selector:
        for scope in scopes:
            try:
                scope.locator(selector).first.click(timeout=800)
                return True
            except Exception:
                pass

    for scope in scopes:
        try:
            scope.get_by_role("tab", name=exact_label).first.click(timeout=800)
            return True
        except Exception:
            pass
        try:
            scope.get_by_role("button", name=exact_label).first.click(timeout=800)
            return True
        except Exception:
            pass
        try:
            scope.get_by_text(exact_label).first.click(timeout=800)
            return True
        except Exception:
            pass
    return False



def _click_configured_or_patterns(
    page: Page,
    selector: str | None,
    patterns: list[str],
) -> None:
    if selector:
        try:
            page.locator(selector).first.click(timeout=5000)
            _wait_for_page_ready(page)
            return
        except PlaywrightTimeoutError:
            pass

    for pattern in patterns:
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).click(timeout=5000)
            _wait_for_page_ready(page)
            return
        except PlaywrightTimeoutError:
            try:
                page.get_by_role("link", name=re.compile(pattern, re.I)).click(timeout=3000)
                _wait_for_page_ready(page)
                return
            except PlaywrightTimeoutError:
                continue
    raise RuntimeError(f"Could not find clickable control matching: {', '.join(patterns)}")


def _fill_configured_or_patterns(
    page: Page,
    selector: str | None,
    label_patterns: list[str],
    value: str,
    *,
    required: bool = True,
) -> bool:
    if not value and not required:
        return False

    if selector:
        locator = page.locator(selector).first
        try:
            _set_locator_value(locator, value)
            return True
        except PlaywrightTimeoutError:
            if required:
                raise RuntimeError(f"Could not fill configured selector: {selector}")

    for pattern in label_patterns:
        if _fill_by_label_with_result(page, pattern, value):
            return True

    lowered = "|".join(label_patterns)
    for selector_pattern in (
        f"input[name*='{_simple_selector_token(lowered)}' i]",
        f"input[id*='{_simple_selector_token(lowered)}' i]",
        f"textarea[name*='{_simple_selector_token(lowered)}' i]",
        f"textarea[id*='{_simple_selector_token(lowered)}' i]",
    ):
        try:
            _set_locator_value(page.locator(selector_pattern).first, value)
            return True
        except PlaywrightTimeoutError:
            continue

    if required:
        raise RuntimeError(f"Could not fill required field matching: {', '.join(label_patterns)}")
    return False


def _set_locator_value(locator, value: str) -> None:
    tag_name = locator.evaluate("element => element.tagName.toLowerCase()", timeout=2000)
    if tag_name == "select":
        try:
            locator.select_option(label=value, timeout=2000)
            return
        except Exception:
            locator.select_option(value=value, timeout=2000)
            return
    locator.fill(value, timeout=3000)
    try:
        locator.press("Enter", timeout=1000)
    except PlaywrightTimeoutError:
        pass


def _fill_by_label_with_result(page: Page, label_pattern: str, value: str) -> bool:
    try:
        page.get_by_label(re.compile(label_pattern, re.I)).fill(value, timeout=1500)
        return True
    except PlaywrightTimeoutError:
        pass
    try:
        page.get_by_placeholder(re.compile(label_pattern, re.I)).fill(value, timeout=1500)
        return True
    except PlaywrightTimeoutError:
        return False


def _simple_selector_token(pattern: str) -> str:
    match = re.search(r"[a-zA-Z]+", pattern)
    return match.group(0) if match else pattern


def _wait_for_page_ready(page: Page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=500)
    except PlaywrightTimeoutError:
        pass


def _set_party_size(page: Page, party_size: int) -> None:
    label = f"{party_size} people"
    fallback_label = f"{party_size} person"
    _select_or_click(
        page,
        selectors=[
            "select[name*='party']",
            "select[id*='party']",
            "select[data-testid*='party']",
        ],
        option_values=[str(party_size), label, fallback_label],
        button_patterns=[
            r"party size",
            r"guests?",
            r"people",
            r"person",
        ],
        choice_patterns=[
            rf"^{party_size}\s+people$",
            rf"^{party_size}\s+person$",
            rf"^{party_size}$",
        ],
    )


def _set_date(page: Page, date_value: str) -> None:
    for selector in (
        "input[type='date']",
        "input[name*='date']",
        "input[id*='date']",
        "input[data-testid*='date']",
    ):
        locator = page.locator(selector).first
        if locator.count():
            locator.fill(date_value)
            locator.press("Enter")
            return
    _click_by_patterns(page, [r"date"])
    page.keyboard.type(date_value)
    page.keyboard.press("Enter")


def _set_time(page: Page, time_value: str) -> None:
    display_time = _format_time_for_ui(time_value)
    _select_or_click(
        page,
        selectors=[
            "select[name*='time']",
            "select[id*='time']",
            "select[data-testid*='time']",
        ],
        option_values=[time_value, display_time],
        button_patterns=[r"time"],
        choice_patterns=[re.escape(display_time), re.escape(time_value)],
    )


def _click_find_table(page: Page) -> None:
    for pattern in (r"find a table", r"search", r"reserve", r"book"):
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).click(timeout=3000)
            _wait_for_page_ready(page)
            return
        except PlaywrightTimeoutError:
            continue


def _select_requested_time(page: Page, time_value: str) -> None:
    display_time = _format_time_for_ui(time_value)
    for pattern in (re.escape(display_time), re.escape(time_value)):
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).first.click(timeout=5000)
            _wait_for_page_ready(page)
            return
        except PlaywrightTimeoutError:
            continue
    try:
        page.get_by_text(re.compile(re.escape(display_time), re.I)).first.click(timeout=5000)
        _wait_for_page_ready(page)
    except PlaywrightTimeoutError:
        pass


def _fill_guest_details(page: Page, reservation: ReservationConfig) -> None:
    fields = {
        r"first name": reservation.guest.first_name,
        r"last name": reservation.guest.last_name,
        r"email": reservation.guest.email,
        r"phone|mobile": reservation.guest.phone,
        r"special request|note": reservation.special_request,
        r"occasion": reservation.occasion,
    }
    for pattern, value in fields.items():
        if value:
            _fill_by_label(page, pattern, value)


def _click_final_confirm(page: Page) -> None:
    for pattern in (
        r"complete reservation",
        r"confirm reservation",
        r"reserve now",
        r"book now",
    ):
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).click(timeout=5000)
            return
        except PlaywrightTimeoutError:
            continue
    raise RuntimeError("Could not find a final confirmation button.")


def _select_or_click(
    page: Page,
    *,
    selectors: list[str],
    option_values: list[str],
    button_patterns: list[str],
    choice_patterns: list[str],
) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        if locator.count():
            for value in option_values:
                try:
                    locator.select_option(label=value, timeout=1000)
                    return
                except Exception:
                    try:
                        locator.select_option(value=value, timeout=1000)
                        return
                    except Exception:
                        continue
    _click_by_patterns(page, button_patterns)
    for pattern in choice_patterns:
        try:
            page.get_by_role("option", name=re.compile(pattern, re.I)).click(timeout=3000)
            return
        except PlaywrightTimeoutError:
            try:
                page.get_by_text(re.compile(pattern, re.I)).first.click(timeout=3000)
                return
            except PlaywrightTimeoutError:
                continue


def _click_by_patterns(page: Page, patterns: list[str]) -> None:
    for pattern in patterns:
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).click(timeout=3000)
            return
        except PlaywrightTimeoutError:
            continue
    raise RuntimeError(f"Could not find control matching: {', '.join(patterns)}")


def _fill_by_label(page: Page, label_pattern: str, value: str) -> None:
    try:
        page.get_by_label(re.compile(label_pattern, re.I)).fill(value, timeout=1500)
        return
    except PlaywrightTimeoutError:
        pass
    try:
        page.get_by_placeholder(re.compile(label_pattern, re.I)).fill(value, timeout=1500)
    except PlaywrightTimeoutError:
        pass


def _reservation_cards(page: Page) -> list[str]:
    selectors = [
        "[data-testid*='reservation']",
        "[class*='reservation']",
        "article",
        "main li",
    ]
    cards: list[str] = []
    for selector in selectors:
        locators = page.locator(selector)
        count = min(locators.count(), 20)
        for index in range(count):
            text = locators.nth(index).inner_text(timeout=1000).strip()
            if "reservation" in text.lower() or re.search(r"\b\d{1,2}:\d{2}\b", text):
                cards.append(_compact_text(text))
        if cards:
            return cards
    return []


def _visible_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5000)
    except PlaywrightTimeoutError:
        return ""


def _looks_logged_out(text: str) -> bool:
    lowered = text.lower()
    return "sign in" in lowered or "log in" in lowered


def _looks_guestcenter_logged_out(page: Page) -> bool:
    url = page.url.lower()
    text = _visible_text(page).lower()

    if "guestcenter.opentable.com" not in url:
        return _looks_logged_out(text)
    if "/login" in url or "login" in url:
        return True

    login_markers = (
        "forgot password",
        "password",
        "email address",
        "sign in",
        "log in",
    )
    app_markers = (
        "front-of-house",
        "make a reservation",
        "reservation waitlist",
        "search by name or phone",
    )
    return any(marker in text for marker in login_markers) and not any(
        marker in text for marker in app_markers
    )


def _summarize_lines(text: str) -> list[str]:
    lines = [_compact_text(line) for line in text.splitlines()]
    return [line for line in lines if line][:40]


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _format_time_for_ui(time_value: str) -> str:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", time_value.strip())
    if not match:
        return time_value
    hour = int(match.group(1))
    minute = match.group(2)
    suffix = "AM" if hour < 12 else "PM"
    hour_12 = hour % 12 or 12
    return f"{hour_12}:{minute} {suffix}"
