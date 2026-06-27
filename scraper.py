"""
ICAI SPMT Batch Monitor - Playwright Scraper

Uses Playwright (headless Chromium) to:
1. Load the public slot details page
2. Select State → City → Test Centre via dropdowns
3. Read the calendar to find GREEN-highlighted available dates
4. Detect when new dates become available

The SPMT portal uses a JavaScript calendar/datepicker where:
  🟢 Green = Slots available
  🔴 Red   = Fully booked
  ⬜ Grey  = No slots / Holiday
"""

import os
import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Page,
    Browser,
    BrowserContext,
    TimeoutError as PlaywrightTimeout,
)

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

SLOT_DETAILS_URL = "https://spmt.icai.org/ICAI/LoginAction_showSlotDetails.action"
CENTRE_DETAILS_URL = "https://spmt.icai.org/ICAI/LoginAction_showCentreDetails.action"

PAGE_TIMEOUT = 30_000       # 30s for page loads
ELEMENT_TIMEOUT = 15_000    # 15s to wait for elements
AJAX_WAIT = 2_000           # 2s for AJAX to complete after interactions
DROPDOWN_WAIT = 8_000       # 8s for dependent dropdown to populate


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class DropdownOption:
    """A dropdown option with value and display text."""
    value: str
    text: str

    def to_dict(self) -> dict:
        return {"value": self.value, "text": self.text}

    @classmethod
    def from_dict(cls, d: dict) -> "DropdownOption":
        return cls(value=d["value"], text=d["text"])


@dataclass
class SlotDate:
    """An available slot date from the calendar."""
    date: str             # e.g., "2025-07-15" or "15"
    day_of_week: str = ""
    month: str = ""
    year: str = ""
    status: str = ""      # "available", "booked", "unavailable"
    css_class: str = ""   # Original CSS classes for debugging
    time_slots: list = field(default_factory=list)  # Available time slots if any

    def full_date(self) -> str:
        """Return a full date string."""
        if self.year and self.month:
            return f"{self.date} {self.month} {self.year}"
        return self.date

    def unique_key(self) -> str:
        return f"{self.full_date()}|{self.status}|{'|'.join(self.time_slots)}"


@dataclass
class SlotInfo:
    """Complete slot information for a test centre."""
    state: str
    city: str
    test_centre: str
    available_dates: list[SlotDate] = field(default_factory=list)
    booked_dates: list[SlotDate] = field(default_factory=list)
    calendar_month: str = ""  # Currently displayed month/year
    raw_text: str = ""        # Any additional text on the page

    def has_availability(self) -> bool:
        return len(self.available_dates) > 0


# ─── Scraper Class ────────────────────────────────────────────────────────────

class SPMTScraper:
    """
    Scrapes the ICAI SPMT portal using Playwright (headless browser).
    Reads the calendar datepicker to detect green (available) dates.
    """

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None

    async def _ensure_browser(self) -> Browser:
        """Start Playwright and launch browser if needed."""
        if self._browser is None or not self._browser.is_connected():
            self._playwright = await async_playwright().start()
            
            executable_path = os.getenv("CHROMIUM_EXECUTABLE_PATH")
            launch_args = {
                "headless": True,
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-background-networking",
                ],
            }
            if executable_path:
                launch_args["executable_path"] = executable_path
                logger.info(f"Using custom Chromium path: {executable_path}")

            self._browser = await self._playwright.chromium.launch(**launch_args)
            logger.info("Browser launched")
        return self._browser

    async def _new_page(self) -> Page:
        """Create a new page with realistic settings."""
        browser = await self._ensure_browser()
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        page.set_default_timeout(ELEMENT_TIMEOUT)
        return page

    async def close(self):
        """Clean up browser resources."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def _load_slot_page(self, page: Page) -> bool:
        """Navigate to the slot details page and wait for it to load."""
        try:
            logger.info(f"Loading: {SLOT_DETAILS_URL}")
            await page.goto(SLOT_DETAILS_URL, wait_until="networkidle", timeout=PAGE_TIMEOUT)

            # Wait for at least one <select> dropdown to appear
            await page.wait_for_selector("select", timeout=ELEMENT_TIMEOUT)
            await page.wait_for_timeout(AJAX_WAIT)

            logger.info("Slot details page loaded successfully")
            return True

        except PlaywrightTimeout:
            logger.error("Timeout loading SPMT page — portal may be under maintenance")
            return False
        except Exception as e:
            logger.error(f"Error loading page: {e}")
            return False

    async def _get_select_options(self, page: Page, selector: str) -> list[DropdownOption]:
        """Read all options from a <select> dropdown."""
        options = []
        try:
            # Wait for the select to exist
            await page.wait_for_selector(selector, timeout=ELEMENT_TIMEOUT)

            # Extract options via JS to avoid stale element issues
            raw = await page.evaluate(f"""() => {{
                const sel = document.querySelector('{selector}');
                if (!sel) return [];
                return Array.from(sel.options).map(o => ({{
                    value: o.value,
                    text: o.textContent.trim()
                }}));
            }}""")

            for item in raw:
                v = item["value"].strip()
                t = item["text"].strip()
                # Skip placeholder options
                if v and v not in ("", "-1", "0") and t.lower() not in (
                    "select", "--select--", "-- select --", "select state",
                    "select city", "select centre", "select test centre",
                    "select country", ""
                ):
                    options.append(DropdownOption(value=v, text=t))

        except Exception as e:
            logger.error(f"Error reading select '{selector}': {e}")

        return options

    async def _discover_selects(self, page: Page) -> dict[str, str]:
        """Discover all <select> elements and classify them by role."""
        selects_info = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('select')).map(s => ({
                id: s.id,
                name: s.name,
                optionCount: s.options.length,
                firstOptionText: s.options.length > 0 ? s.options[0].textContent.trim() : ''
            }));
        }""")

        logger.info(f"Found {len(selects_info)} <select> elements:")
        for s in selects_info:
            logger.info(f"  id='{s['id']}' name='{s['name']}' options={s['optionCount']} "
                        f"first='{s['firstOptionText']}'")

        mapping = {}
        for s in selects_info:
            ident = s["id"] or s["name"]
            il = ident.lower()
            first = s["firstOptionText"].lower()

            if "country" in il or "country" in first:
                mapping["country"] = f"#{s['id']}" if s["id"] else f"[name='{s['name']}']"
            elif "state" in il or "state" in first:
                mapping["state"] = f"#{s['id']}" if s["id"] else f"[name='{s['name']}']"
            elif "city" in il or "city" in first:
                mapping["city"] = f"#{s['id']}" if s["id"] else f"[name='{s['name']}']"
            elif "centre" in il or "center" in il or "test" in il or "centre" in first:
                mapping["centre"] = f"#{s['id']}" if s["id"] else f"[name='{s['name']}']"

        # Fallback: assign by position if we couldn't identify by name
        unassigned = [s for s in selects_info
                      if (f"#{s['id']}" not in mapping.values() and
                          f"[name='{s['name']}']" not in mapping.values())]

        roles_needed = [r for r in ["country", "state", "city", "centre"] if r not in mapping]
        for role, s in zip(roles_needed, unassigned):
            mapping[role] = f"#{s['id']}" if s["id"] else f"[name='{s['name']}']"

        logger.info(f"Dropdown mapping: {mapping}")
        return mapping

    async def _select_option(self, page: Page, selector: str, value: str):
        """Select an option in a dropdown and wait for AJAX."""
        await page.select_option(selector, value)
        await page.wait_for_timeout(AJAX_WAIT)
        # Wait for any network requests triggered by the selection
        try:
            await page.wait_for_load_state("networkidle", timeout=DROPDOWN_WAIT)
        except PlaywrightTimeout:
            pass  # Some selections don't trigger network calls

    async def _read_calendar(self, page: Page) -> tuple[list[SlotDate], list[SlotDate], str]:
        """
        Read the calendar/datepicker on the page.
        Returns: (available_dates, booked_dates, calendar_month_label)

        The calendar typically uses CSS classes or inline styles to indicate:
        - Green / available / active → slots available
        - Red / booked / full → fully booked
        - Disabled / grey → no slots
        """
        available = []
        booked = []
        month_label = ""

        try:
            # Wait for the calendar to appear
            # Common selectors for datepicker/calendar widgets
            calendar_selectors = [
                ".ui-datepicker",           # jQuery UI Datepicker
                ".datepicker",              # Bootstrap Datepicker
                ".calendar",                # Generic calendar
                ".fc",                      # FullCalendar
                "[class*='calendar']",      # Any element with 'calendar' in class
                "[class*='datepicker']",    # Any element with 'datepicker' in class
                "table.table-bordered",     # Bootstrap-style table calendar
                ".hasDatepicker",           # jQuery UI marker
            ]

            calendar_found = False
            for sel in calendar_selectors:
                try:
                    el = await page.wait_for_selector(sel, timeout=5000)
                    if el:
                        calendar_found = True
                        logger.info(f"Calendar found with selector: {sel}")
                        break
                except PlaywrightTimeout:
                    continue

            if not calendar_found:
                logger.warning("No calendar widget found on page")

            # Extract calendar data using JavaScript for reliability
            calendar_data = await page.evaluate("""() => {
                const result = {
                    monthLabel: '',
                    dates: [],
                    calendarHTML: '',
                    allClasses: new Set()
                };

                // === jQuery UI Datepicker ===
                const uiCalendar = document.querySelector('.ui-datepicker, .hasDatepicker, [id*="datepicker"]');
                if (uiCalendar) {
                    // Get month/year label
                    const titleEl = uiCalendar.querySelector('.ui-datepicker-title, .ui-datepicker-header');
                    if (titleEl) result.monthLabel = titleEl.textContent.trim();

                    // Get all date cells
                    const cells = uiCalendar.querySelectorAll('td');
                    cells.forEach(td => {
                        const a = td.querySelector('a') || td.querySelector('span');
                        if (!a) return;
                        const dateNum = a.textContent.trim();
                        if (!dateNum || isNaN(dateNum)) return;

                        const classes = td.className + ' ' + a.className;
                        const style = td.getAttribute('style') || '';
                        const aStyle = a.getAttribute('style') || '';
                        const allStyles = style + ' ' + aStyle;

                        result.dates.push({
                            date: dateNum,
                            classes: classes,
                            style: allStyles,
                            title: td.getAttribute('title') || a.getAttribute('title') || '',
                            isDisabled: td.classList.contains('ui-datepicker-unselectable') ||
                                       td.classList.contains('ui-state-disabled'),
                        });
                    });
                }

                // === Bootstrap / Custom Calendar ===
                if (result.dates.length === 0) {
                    // Try finding any table that looks like a calendar
                    const tables = document.querySelectorAll('table');
                    tables.forEach(table => {
                        const headerText = table.querySelector('th, caption');
                        if (headerText) {
                            const text = headerText.textContent.trim();
                            // Check if it looks like a month/year header
                            if (/\\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)\\b/i.test(text)) {
                                result.monthLabel = text;
                            }
                        }

                        const cells = table.querySelectorAll('td');
                        cells.forEach(td => {
                            const text = td.textContent.trim();
                            if (text && !isNaN(text) && parseInt(text) >= 1 && parseInt(text) <= 31) {
                                const classes = td.className;
                                const style = td.getAttribute('style') || '';
                                const bgColor = window.getComputedStyle(td).backgroundColor;
                                const color = window.getComputedStyle(td).color;
                                const innerEl = td.querySelector('a, span, div');
                                const innerBg = innerEl ?
                                    window.getComputedStyle(innerEl).backgroundColor : '';

                                result.dates.push({
                                    date: text,
                                    classes: classes + (innerEl ? ' ' + innerEl.className : ''),
                                    style: style,
                                    bgColor: bgColor,
                                    innerBg: innerBg,
                                    color: color,
                                    title: td.getAttribute('title') || '',
                                    isDisabled: td.classList.contains('disabled') ||
                                               td.classList.contains('unavailable'),
                                });
                            }
                        });
                    });
                }

                // === Generic: Look for any colored day elements ===
                if (result.dates.length === 0) {
                    const allElements = document.querySelectorAll('[class*="day"], [class*="date"], [class*="slot"]');
                    allElements.forEach(el => {
                        const text = el.textContent.trim();
                        if (text && !isNaN(text) && parseInt(text) >= 1 && parseInt(text) <= 31) {
                            const bgColor = window.getComputedStyle(el).backgroundColor;
                            result.dates.push({
                                date: text,
                                classes: el.className,
                                style: el.getAttribute('style') || '',
                                bgColor: bgColor,
                                title: el.getAttribute('title') || '',
                                isDisabled: false,
                            });
                        }
                    });
                }

                // Collect all unique CSS classes for debugging
                result.dates.forEach(d => {
                    d.classes.split(/\\s+/).forEach(c => {
                        if (c) result.allClasses.add(c);
                    });
                });
                result.allClasses = [...result.allClasses];

                return result;
            }""")

            month_label = calendar_data.get("monthLabel", "")
            all_classes = calendar_data.get("allClasses", [])
            logger.info(f"Calendar month: {month_label}")
            logger.info(f"Found {len(calendar_data.get('dates', []))} date cells")
            logger.info(f"CSS classes found: {all_classes}")

            # Classify each date as available, booked, or unavailable
            for d in calendar_data.get("dates", []):
                date_num = d["date"]
                classes = d.get("classes", "").lower()
                style = d.get("style", "").lower()
                bg_color = d.get("bgColor", "").lower()
                inner_bg = d.get("innerBg", "").lower()
                title = d.get("title", "")
                is_disabled = d.get("isDisabled", False)

                slot = SlotDate(
                    date=date_num,
                    month=month_label,
                    css_class=d.get("classes", ""),
                )

                # Determine status from CSS classes and colors
                status = self._classify_date(classes, style, bg_color, inner_bg, title, is_disabled)
                slot.status = status

                if status == "available":
                    available.append(slot)
                elif status == "booked":
                    booked.append(slot)

            logger.info(f"Available dates: {[d.date for d in available]}")
            logger.info(f"Booked dates: {[d.date for d in booked]}")

        except Exception as e:
            logger.error(f"Error reading calendar: {e}")

        return available, booked, month_label

    def _classify_date(self, classes: str, style: str, bg_color: str,
                        inner_bg: str, title: str, is_disabled: bool) -> str:
        """
        Classify a calendar date cell as available, booked, or unavailable.
        Uses CSS classes, inline styles, computed background colors, and title text.
        """
        all_text = f"{classes} {style} {bg_color} {inner_bg} {title}".lower()

        # ── Green indicators (AVAILABLE) ──
        green_indicators = [
            "available", "active", "green", "open", "free",
            "success", "highlight", "selectable", "enabled",
            "bg-success", "text-success", "slot-available",
        ]
        green_colors = [
            "rgb(0, 128, 0)", "rgb(0, 255, 0)", "rgb(40, 167, 69)",   # Bootstrap green
            "rgb(76, 175, 80)", "rgb(102, 187, 106)",                   # Material green
            "rgb(34, 139, 34)", "rgb(50, 205, 50)",                     # Forest/lime green
            "#00ff00", "#008000", "#28a745", "#4caf50", "#66bb6a",
            "#22c55e", "#16a34a", "#15803d",                            # Tailwind greens
        ]

        # ── Red indicators (BOOKED) ──
        red_indicators = [
            "booked", "full", "red", "occupied", "sold",
            "danger", "slot-booked", "not-available", "closed",
        ]
        red_colors = [
            "rgb(255, 0, 0)", "rgb(220, 53, 69)", "rgb(244, 67, 54)",
            "rgb(211, 47, 47)", "#ff0000", "#dc3545", "#f44336",
        ]

        # ── Disabled indicators ──
        disabled_indicators = [
            "disabled", "unavailable", "grey", "gray",
            "ui-datepicker-unselectable", "ui-state-disabled",
            "muted", "empty", "other-month",
        ]

        if is_disabled:
            return "unavailable"

        # Check for green (available)
        for indicator in green_indicators:
            if indicator in all_text:
                return "available"
        for color in green_colors:
            if color in bg_color or color in inner_bg or color in style:
                return "available"

        # Check for red (booked)
        for indicator in red_indicators:
            if indicator in all_text:
                return "booked"
        for color in red_colors:
            if color in bg_color or color in inner_bg or color in style:
                return "booked"

        # Check for disabled
        for indicator in disabled_indicators:
            if indicator in classes:
                return "unavailable"

        # If clickable (has <a> tag behavior), might be available
        if "ui-state-default" in classes and "ui-state-disabled" not in classes:
            return "available"

        return "unknown"

    # ─── Public API ───────────────────────────────────────────────────────────

    async def get_states(self) -> list[DropdownOption]:
        """Fetch available states from the SPMT portal."""
        page = await self._new_page()
        try:
            if not await self._load_slot_page(page):
                return []

            mapping = await self._discover_selects(page)

            # Some portals have a "Country" dropdown first — select India if present
            if "country" in mapping:
                country_opts = await self._get_select_options(page, mapping["country"])
                india = next((o for o in country_opts
                             if "india" in o.text.lower()), None)
                if india:
                    await self._select_option(page, mapping["country"], india.value)

            if "state" not in mapping:
                logger.error("No state dropdown found")
                return []

            states = await self._get_select_options(page, mapping["state"])
            logger.info(f"Found {len(states)} states")
            return states

        finally:
            await page.context.close()

    async def get_cities(self, state_value: str) -> list[DropdownOption]:
        """Fetch cities for the given state."""
        page = await self._new_page()
        try:
            if not await self._load_slot_page(page):
                return []

            mapping = await self._discover_selects(page)

            # Handle Country dropdown
            if "country" in mapping:
                country_opts = await self._get_select_options(page, mapping["country"])
                india = next((o for o in country_opts if "india" in o.text.lower()), None)
                if india:
                    await self._select_option(page, mapping["country"], india.value)

            if "state" not in mapping:
                return []

            # Select state
            await self._select_option(page, mapping["state"], state_value)

            # Wait for city dropdown to populate
            if "city" not in mapping:
                # Re-discover after state selection (new dropdowns may appear)
                mapping = await self._discover_selects(page)

            if "city" not in mapping:
                logger.error("No city dropdown found after state selection")
                return []

            # Wait for the city dropdown to get populated
            await page.wait_for_timeout(AJAX_WAIT)
            cities = await self._get_select_options(page, mapping["city"])
            logger.info(f"Found {len(cities)} cities")
            return cities

        finally:
            await page.context.close()

    async def get_test_centres(self, state_value: str, city_value: str) -> list[DropdownOption]:
        """Fetch test centres for the given state and city."""
        page = await self._new_page()
        try:
            if not await self._load_slot_page(page):
                return []

            mapping = await self._discover_selects(page)

            if "country" in mapping:
                country_opts = await self._get_select_options(page, mapping["country"])
                india = next((o for o in country_opts if "india" in o.text.lower()), None)
                if india:
                    await self._select_option(page, mapping["country"], india.value)

            if "state" not in mapping:
                return []

            await self._select_option(page, mapping["state"], state_value)
            await page.wait_for_timeout(AJAX_WAIT)

            # Re-discover in case new elements appeared
            mapping = await self._discover_selects(page)

            if "city" not in mapping:
                return []

            await self._select_option(page, mapping["city"], city_value)
            await page.wait_for_timeout(AJAX_WAIT)

            mapping = await self._discover_selects(page)

            if "centre" not in mapping:
                logger.error("No test centre dropdown found")
                return []

            centres = await self._get_select_options(page, mapping["centre"])
            logger.info(f"Found {len(centres)} test centres")
            return centres

        finally:
            await page.context.close()

    async def check_slots(self, state_value: str, city_value: str, centre_value: str,
                          state_text: str = "", city_text: str = "",
                          centre_text: str = "") -> SlotInfo:
        """
        Check slot availability at the specified test centre.
        Reads the calendar and returns green (available) and red (booked) dates.
        """
        result = SlotInfo(state=state_text, city=city_text, test_centre=centre_text)
        page = await self._new_page()

        try:
            if not await self._load_slot_page(page):
                return result

            mapping = await self._discover_selects(page)

            # Handle Country
            if "country" in mapping:
                country_opts = await self._get_select_options(page, mapping["country"])
                india = next((o for o in country_opts if "india" in o.text.lower()), None)
                if india:
                    await self._select_option(page, mapping["country"], india.value)

            # Select State
            if "state" in mapping:
                logger.info(f"Selecting state: {state_text}")
                await self._select_option(page, mapping["state"], state_value)
                await page.wait_for_timeout(AJAX_WAIT)
                mapping = await self._discover_selects(page)

            # Select City
            if "city" in mapping:
                logger.info(f"Selecting city: {city_text}")
                await self._select_option(page, mapping["city"], city_value)
                await page.wait_for_timeout(AJAX_WAIT)
                mapping = await self._discover_selects(page)

            # Select Test Centre
            if "centre" in mapping:
                logger.info(f"Selecting centre: {centre_text}")
                await self._select_option(page, mapping["centre"], centre_value)
                await page.wait_for_timeout(AJAX_WAIT)

            # Look for and click any "Search" / "Show" button if present
            for btn_text in ["Search", "Show", "Submit", "Get", "View", "Go"]:
                try:
                    btn = page.locator(
                        f"input[type='submit'][value*='{btn_text}' i], "
                        f"button:has-text('{btn_text}'), "
                        f"input[type='button'][value*='{btn_text}' i]"
                    ).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        await page.wait_for_timeout(3000)
                        break
                except Exception:
                    continue

            # Wait for calendar to load
            await page.wait_for_timeout(3000)

            # Read the calendar
            available, booked, month_label = await self._read_calendar(page)

            result.available_dates = available
            result.booked_dates = booked
            result.calendar_month = month_label

            # Also try to read any text/table results on the page
            try:
                body_text = await page.inner_text("body")
                # Check for "no slots" messages
                no_slot_phrases = ["no slot", "no batch", "not available", "no record",
                                   "no data", "currently no", "no seats"]
                for phrase in no_slot_phrases:
                    if phrase in body_text.lower():
                        result.raw_text = f"Portal message: slots not available"
                        break
            except Exception:
                pass

            logger.info(f"Slot check complete: {len(available)} available, "
                        f"{len(booked)} booked dates")
            return result

        except Exception as e:
            logger.error(f"Error checking slots: {e}")
            return result
        finally:
            await page.context.close()

    async def health_check(self) -> bool:
        """Check if the SPMT portal is accessible."""
        page = await self._new_page()
        try:
            await page.goto(SLOT_DETAILS_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            return True
        except Exception:
            return False
        finally:
            await page.context.close()
