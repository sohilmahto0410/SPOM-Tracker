"""
ICAI SPMT Batch Monitor - Playwright Scraper

Uses Playwright (headless Chromium) to:
1. Load the public slot details page
2. Select Country → State → City → Test Centre
3. Click "Search" to load the calendar
4. Read the calendar to find GREEN-highlighted available dates
5. Detect when new dates become available

The SPMT portal calendar structure (from actual inspection):
  - Two-month inline calendar (current + next month)
  - 🟢 Green background on date cell = Available slots
  - 🔴 Red background on date cell = Fully booked
  - No highlight = No slots / Holiday / Past date
  - "Search" button MUST be clicked after selecting dropdowns
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

PAGE_TIMEOUT = 45_000       # 45s for page loads
ELEMENT_TIMEOUT = 15_000    # 15s to wait for elements
AJAX_WAIT = 3_000           # 3s for AJAX to complete after interactions
DROPDOWN_WAIT = 8_000       # 8s for dependent dropdown to populate
CALENDAR_WAIT = 5_000       # 5s for calendar to render after Search


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
    """An available/booked date from the SPMT calendar."""
    date: str             # Day number, e.g. "4"
    month: str = ""       # e.g. "July 2026"
    status: str = ""      # "available" or "booked"
    bg_color: str = ""    # Actual background color detected
    css_class: str = ""   # CSS classes for debugging

    def full_date(self) -> str:
        if self.month:
            return f"{self.date} {self.month}"
        return self.date

    def unique_key(self) -> str:
        return f"{self.full_date()}|{self.status}"


@dataclass
class SlotInfo:
    """Complete slot information for a test centre."""
    state: str
    city: str
    test_centre: str
    available_dates: list[SlotDate] = field(default_factory=list)
    booked_dates: list[SlotDate] = field(default_factory=list)
    calendar_months: list[str] = field(default_factory=list)
    raw_text: str = ""

    def has_availability(self) -> bool:
        return len(self.available_dates) > 0


# ─── Scraper Class ────────────────────────────────────────────────────────────

class SPMTScraper:
    """
    Scrapes the ICAI SPMT portal using Playwright (headless browser).
    Reads the inline calendar to detect green (available) and red (booked) dates.
    """

    def __init__(self, chromium_path: str = ""):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._chromium_path = chromium_path or os.getenv("CHROMIUM_EXECUTABLE_PATH", "")

    async def _ensure_browser(self) -> Browser:
        if self._browser is None or not self._browser.is_connected():
            self._playwright = await async_playwright().start()

            launch_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--single-process",
            ]

            launch_kwargs = {
                "headless": True,
                "args": launch_args,
            }

            # Use custom Chromium binary if provided
            if self._chromium_path:
                launch_kwargs["executable_path"] = self._chromium_path
                logger.info(f"Using custom Chromium: {self._chromium_path}")

            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            logger.info("Browser launched")
        return self._browser

    async def _new_page(self) -> Page:
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
            await page.wait_for_selector("select", timeout=ELEMENT_TIMEOUT)
            await page.wait_for_timeout(AJAX_WAIT)
            logger.info("Page loaded successfully")
            return True
        except PlaywrightTimeout:
            logger.error("Timeout loading SPMT page — portal may be under maintenance")
            return False
        except Exception as e:
            logger.error(f"Error loading page: {e}")
            return False

    async def _get_select_options(self, page: Page, selector: str) -> list[DropdownOption]:
        """Read all meaningful options from a <select> dropdown."""
        options = []
        try:
            await page.wait_for_selector(selector, timeout=ELEMENT_TIMEOUT)
            raw = await page.evaluate(f"""() => {{
                const sel = document.querySelector('{selector}');
                if (!sel) return [];
                return Array.from(sel.options).map(o => ({{
                    value: o.value, text: o.textContent.trim()
                }}));
            }}""")
            skip_texts = {"select", "--select--", "-- select --", "select state",
                          "select city", "select centre", "select test centre",
                          "select country", ""}
            for item in raw:
                v = item["value"].strip()
                t = item["text"].strip()
                if v and v not in ("", "-1", "0") and t.lower() not in skip_texts:
                    options.append(DropdownOption(value=v, text=t))
        except Exception as e:
            logger.error(f"Error reading select '{selector}': {e}")
        return options

    async def _discover_selects(self, page: Page) -> dict[str, str]:
        """Discover all <select> elements and classify them."""
        selects_info = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('select')).map(s => ({
                id: s.id, name: s.name,
                optionCount: s.options.length,
                firstOptionText: s.options.length > 0 ? s.options[0].textContent.trim() : ''
            }));
        }""")

        logger.info(f"Found {len(selects_info)} <select> elements")

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

        # Fallback: assign by position
        unassigned = [s for s in selects_info
                      if (f"#{s['id']}" not in mapping.values() and
                          f"[name='{s['name']}']" not in mapping.values())]
        roles_needed = [r for r in ["country", "state", "city", "centre"] if r not in mapping]
        for role, s in zip(roles_needed, unassigned):
            mapping[role] = f"#{s['id']}" if s["id"] else f"[name='{s['name']}']"

        logger.info(f"Dropdown mapping: {mapping}")
        return mapping

    async def _select_option(self, page: Page, selector: str, value: str):
        """Select an option and wait for dependent AJAX."""
        await page.select_option(selector, value)
        await page.wait_for_timeout(AJAX_WAIT)
        try:
            await page.wait_for_load_state("networkidle", timeout=DROPDOWN_WAIT)
        except PlaywrightTimeout:
            pass

    async def _click_search(self, page: Page) -> bool:
        """Click the Search button to load the calendar."""
        # Try multiple selectors for the Search button
        search_selectors = [
            "input[value='Search']",
            "input[value='search']",
            "button:has-text('Search')",
            "input[type='submit'][value*='Search' i]",
            "input[type='button'][value*='Search' i]",
            "a:has-text('Search')",
            ".btn:has-text('Search')",
        ]

        for selector in search_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    logger.info(f"Clicked Search button: {selector}")
                    await page.wait_for_timeout(CALENDAR_WAIT)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10_000)
                    except PlaywrightTimeout:
                        pass
                    return True
            except Exception:
                continue

        # Fallback: try clicking any submit-type button
        try:
            btn = page.locator("input[type='submit'], button[type='submit']").first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                logger.info("Clicked fallback submit button")
                await page.wait_for_timeout(CALENDAR_WAIT)
                return True
        except Exception:
            pass

        logger.warning("Could not find Search button")
        return False

    async def _read_calendar(self, page: Page) -> tuple[list[SlotDate], list[SlotDate], list[str]]:
        """
        Read the SPMT two-month inline calendar.

        This calendar shows two months side by side. Each date cell (td) that
        has slots uses a colored background:
          - GREEN background → available
          - RED background → fully booked

        We detect colors by reading the computed backgroundColor of each td
        and any inner <a> element, then checking if it's in the green or red
        color range via RGB values.
        """
        available = []
        booked = []
        month_labels = []

        try:
            # Extract ALL relevant data from the calendar using a single JS evaluation
            calendar_data = await page.evaluate("""() => {
                const result = {
                    months: [],
                    cells: [],
                    debugInfo: ''
                };

                // Grab the full page HTML snippet around the calendar for debugging
                const examDateSection = document.querySelector('[class*="calendar"], [id*="calendar"], [class*="datepicker"], [id*="datepicker"]');

                // Find all table elements on the page
                const allTables = document.querySelectorAll('table');

                // Look for calendar-like tables (contain day headers like Su Mo Tu etc.)
                const calendarTables = [];
                allTables.forEach(table => {
                    const text = table.textContent;
                    if (text.includes('Su') && text.includes('Mo') && text.includes('Tu')) {
                        calendarTables.push(table);
                    }
                });

                result.debugInfo = `Found ${allTables.length} tables, ${calendarTables.length} calendar tables`;

                // If no calendar tables found, look for any table with month names
                if (calendarTables.length === 0) {
                    allTables.forEach(table => {
                        const months = ['January','February','March','April','May','June',
                                       'July','August','September','October','November','December'];
                        const text = table.textContent;
                        if (months.some(m => text.includes(m))) {
                            calendarTables.push(table);
                        }
                    });
                }

                // Process each calendar table
                calendarTables.forEach(table => {
                    // Find month/year headers
                    const ths = table.querySelectorAll('th, td.month-header, .ui-datepicker-title');
                    ths.forEach(th => {
                        const text = th.textContent.trim();
                        const months = ['January','February','March','April','May','June',
                                       'July','August','September','October','November','December'];
                        if (months.some(m => text.includes(m))) {
                            result.months.push(text);
                        }
                    });

                    // Process every td cell in this table
                    const tds = table.querySelectorAll('td');
                    tds.forEach(td => {
                        // Get the text — should be a day number (1-31)
                        let dayText = td.textContent.trim();
                        const innerA = td.querySelector('a');
                        const innerSpan = td.querySelector('span');
                        const innerEl = innerA || innerSpan;

                        if (innerEl) {
                            dayText = innerEl.textContent.trim();
                        }

                        // Skip non-numeric cells (headers, empty, month names)
                        if (!dayText || isNaN(dayText) || parseInt(dayText) < 1 || parseInt(dayText) > 31) {
                            return;
                        }

                        // Get background colors from BOTH the td and any inner element
                        const tdBg = window.getComputedStyle(td).backgroundColor;
                        const innerBg = innerEl ? window.getComputedStyle(innerEl).backgroundColor : '';
                        const tdStyle = td.getAttribute('style') || '';
                        const innerStyle = innerEl ? (innerEl.getAttribute('style') || '') : '';
                        const tdClass = td.className || '';
                        const innerClass = innerEl ? (innerEl.className || '') : '';

                        result.cells.push({
                            day: dayText,
                            tdBg: tdBg,
                            innerBg: innerBg,
                            tdStyle: tdStyle,
                            innerStyle: innerStyle,
                            tdClass: tdClass,
                            innerClass: innerClass,
                            title: td.getAttribute('title') || (innerEl ? innerEl.getAttribute('title') || '' : ''),
                        });
                    });
                });

                // If still no cells found, do a brute force scan of ALL tds
                if (result.cells.length === 0) {
                    result.debugInfo += ' | Brute force scan';
                    allTables.forEach(table => {
                        table.querySelectorAll('td').forEach(td => {
                            const innerA = td.querySelector('a');
                            const innerSpan = td.querySelector('span');
                            const innerEl = innerA || innerSpan;
                            let dayText = innerEl ? innerEl.textContent.trim() : td.textContent.trim();

                            if (!dayText || isNaN(dayText)) return;
                            const num = parseInt(dayText);
                            if (num < 1 || num > 31) return;

                            const tdBg = window.getComputedStyle(td).backgroundColor;
                            const innerBg = innerEl ? window.getComputedStyle(innerEl).backgroundColor : '';

                            // Only include cells that have some non-transparent background
                            const transparent = ['rgba(0, 0, 0, 0)', 'transparent', ''];
                            if (!transparent.includes(tdBg) || (innerBg && !transparent.includes(innerBg))) {
                                result.cells.push({
                                    day: dayText,
                                    tdBg: tdBg,
                                    innerBg: innerBg,
                                    tdStyle: td.getAttribute('style') || '',
                                    innerStyle: innerEl ? (innerEl.getAttribute('style') || '') : '',
                                    tdClass: td.className || '',
                                    innerClass: innerEl ? (innerEl.className || '') : '',
                                    title: '',
                                });
                            }
                        });
                    });
                }

                return result;
            }""")

            month_labels = calendar_data.get("months", [])
            cells = calendar_data.get("cells", [])
            debug = calendar_data.get("debugInfo", "")

            logger.info(f"Calendar debug: {debug}")
            logger.info(f"Calendar months: {month_labels}")
            logger.info(f"Calendar cells with color: {len(cells)}")

            # Determine which month each cell belongs to
            # The SPMT calendar shows 2 months side by side
            current_month = month_labels[0] if month_labels else "Unknown"

            for cell in cells:
                day = cell["day"]
                td_bg = cell.get("tdBg", "")
                inner_bg = cell.get("innerBg", "")
                td_style = cell.get("tdStyle", "")
                inner_style = cell.get("innerStyle", "")
                td_class = cell.get("tdClass", "")
                inner_class = cell.get("innerClass", "")

                # Determine which color this cell is
                status = self._detect_color(
                    td_bg, inner_bg, td_style, inner_style, td_class, inner_class
                )

                if status == "unknown":
                    continue

                # Try to figure out which month this date belongs to
                # Simple heuristic: day numbers reset when month changes
                month_for_date = current_month
                if len(month_labels) >= 2:
                    # We'll assign month based on the logged cell info
                    # For now use the first month; the JS already processes
                    # cells in DOM order (left calendar first, then right)
                    month_for_date = current_month  # Will be improved below

                slot = SlotDate(
                    date=day,
                    month=month_for_date,
                    status=status,
                    bg_color=td_bg or inner_bg,
                    css_class=f"td:{td_class} inner:{inner_class}",
                )

                if status == "available":
                    available.append(slot)
                    logger.info(f"  🟢 Available: day {day} | bg={td_bg} inner_bg={inner_bg} "
                               f"class={td_class} {inner_class}")
                elif status == "booked":
                    booked.append(slot)
                    logger.info(f"  🔴 Booked: day {day} | bg={td_bg} inner_bg={inner_bg}")

        except Exception as e:
            logger.error(f"Error reading calendar: {e}", exc_info=True)

        return available, booked, month_labels

    def _detect_color(self, td_bg: str, inner_bg: str, td_style: str,
                       inner_style: str, td_class: str, inner_class: str) -> str:
        """
        Detect whether a calendar cell is green (available) or red (booked)
        by analyzing its background color.

        Uses RGB value analysis instead of just matching exact color strings.
        """
        # Check all possible color sources
        colors_to_check = [td_bg, inner_bg]
        styles_to_check = [td_style, inner_style]
        classes_to_check = (td_class + " " + inner_class).lower()

        # ── Check CSS classes first (most reliable if used) ──
        green_classes = ["available", "green", "success", "open", "slot-available",
                         "bg-success", "bg-green", "active-slot"]
        red_classes = ["booked", "red", "danger", "full", "slot-booked",
                       "bg-danger", "bg-red", "closed"]

        for gc in green_classes:
            if gc in classes_to_check:
                return "available"
        for rc in red_classes:
            if rc in classes_to_check:
                return "booked"

        # ── Check inline styles for color keywords ──
        all_styles = " ".join(styles_to_check).lower()
        if "green" in all_styles or "#0f0" in all_styles or "#00ff00" in all_styles:
            return "available"
        if "red" in all_styles or "#f00" in all_styles or "#ff0000" in all_styles:
            return "booked"

        # ── Check computed background colors using RGB analysis ──
        for color_str in colors_to_check:
            if not color_str:
                continue

            color_lower = color_str.lower().strip()

            # Skip transparent/no-color
            if color_lower in ("rgba(0, 0, 0, 0)", "transparent", "", "rgb(255, 255, 255)"):
                continue

            # Parse RGB values
            rgb = self._parse_rgb(color_lower)
            if rgb is None:
                # Check for color keywords in the string
                if "green" in color_lower:
                    return "available"
                if "red" in color_lower:
                    return "booked"
                continue

            r, g, b = rgb

            # ── GREEN detection ──
            # Green means: G channel is dominant, significantly higher than R and B
            if g > 100 and g > r * 1.3 and g > b * 1.3:
                return "available"

            # ── RED detection ──
            # Red means: R channel is dominant, significantly higher than G and B
            if r > 150 and r > g * 1.5 and r > b * 1.5:
                return "booked"

            # Some orangey-reds (like the ones in the screenshot)
            if r > 200 and g < 100 and b < 100:
                return "booked"

            # Bright/pure greens
            if g > 180 and r < 150 and b < 150:
                return "available"

        # ── Check for common hex colors in styles ──
        for style in styles_to_check:
            style_lower = style.lower()
            # Green hex colors
            green_hexes = ["#0f0", "#00ff00", "#008000", "#28a745", "#4caf50",
                           "#66bb6a", "#22c55e", "#16a34a", "#2e7d32", "#388e3c",
                           "#43a047", "#4caf50", "#00c853", "#00e676"]
            for h in green_hexes:
                if h in style_lower:
                    return "available"
            # Red hex colors
            red_hexes = ["#f00", "#ff0000", "#dc3545", "#f44336", "#e53935",
                         "#d32f2f", "#c62828", "#b71c1c", "#ef5350", "#e74c3c"]
            for h in red_hexes:
                if h in style_lower:
                    return "booked"

        return "unknown"

    def _parse_rgb(self, color_str: str) -> Optional[tuple[int, int, int]]:
        """Parse an RGB/RGBA color string into (R, G, B) tuple."""
        # Match rgb(r, g, b) or rgba(r, g, b, a)
        match = re.match(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', color_str)
        if match:
            return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return None

    # ─── Public API ───────────────────────────────────────────────────────────

    async def get_states(self) -> list[DropdownOption]:
        """Fetch available states from the SPMT portal."""
        page = await self._new_page()
        try:
            if not await self._load_slot_page(page):
                return []
            mapping = await self._discover_selects(page)

            # Select India if Country dropdown exists
            if "country" in mapping:
                country_opts = await self._get_select_options(page, mapping["country"])
                india = next((o for o in country_opts if "india" in o.text.lower()), None)
                if india:
                    await self._select_option(page, mapping["country"], india.value)
                    # Re-discover after country selection (state dropdown may populate)
                    await page.wait_for_timeout(AJAX_WAIT)

            if "state" not in mapping:
                mapping = await self._discover_selects(page)
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

            if "country" in mapping:
                country_opts = await self._get_select_options(page, mapping["country"])
                india = next((o for o in country_opts if "india" in o.text.lower()), None)
                if india:
                    await self._select_option(page, mapping["country"], india.value)
                    await page.wait_for_timeout(AJAX_WAIT)
                    mapping = await self._discover_selects(page)

            if "state" not in mapping:
                return []

            await self._select_option(page, mapping["state"], state_value)
            await page.wait_for_timeout(AJAX_WAIT)
            mapping = await self._discover_selects(page)

            if "city" not in mapping:
                logger.error("No city dropdown found")
                return []

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
                    await page.wait_for_timeout(AJAX_WAIT)
                    mapping = await self._discover_selects(page)

            if "state" not in mapping:
                return []
            await self._select_option(page, mapping["state"], state_value)
            await page.wait_for_timeout(AJAX_WAIT)
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
        Selects all dropdowns, clicks Search, then reads the calendar.
        """
        result = SlotInfo(state=state_text, city=city_text, test_centre=centre_text)
        page = await self._new_page()

        try:
            if not await self._load_slot_page(page):
                return result

            mapping = await self._discover_selects(page)

            # 1. Select Country (India)
            if "country" in mapping:
                country_opts = await self._get_select_options(page, mapping["country"])
                india = next((o for o in country_opts if "india" in o.text.lower()), None)
                if india:
                    logger.info("Selecting country: India")
                    await self._select_option(page, mapping["country"], india.value)
                    await page.wait_for_timeout(AJAX_WAIT)
                    mapping = await self._discover_selects(page)

            # 2. Select State
            if "state" in mapping:
                logger.info(f"Selecting state: {state_text}")
                await self._select_option(page, mapping["state"], state_value)
                await page.wait_for_timeout(AJAX_WAIT)
                mapping = await self._discover_selects(page)

            # 3. Select City
            if "city" in mapping:
                logger.info(f"Selecting city: {city_text}")
                await self._select_option(page, mapping["city"], city_value)
                await page.wait_for_timeout(AJAX_WAIT)
                mapping = await self._discover_selects(page)

            # 4. Select Test Centre
            if "centre" in mapping:
                logger.info(f"Selecting centre: {centre_text}")
                await self._select_option(page, mapping["centre"], centre_value)
                await page.wait_for_timeout(AJAX_WAIT)

            # 5. Click Search button — THIS IS CRITICAL
            search_clicked = await self._click_search(page)
            if not search_clicked:
                logger.warning("Search button not clicked — calendar may not load")

            # 6. Wait for calendar to appear and render
            await page.wait_for_timeout(CALENDAR_WAIT)

            # Take a debug screenshot
            try:
                screenshot_path = os.path.join(os.path.dirname(__file__), "data", "last_check.png")
                os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"Debug screenshot saved: {screenshot_path}")
            except Exception:
                pass

            # 7. Read the calendar
            available, booked, month_labels = await self._read_calendar(page)

            result.available_dates = available
            result.booked_dates = booked
            result.calendar_months = month_labels

            # Check for "no slots" messages on the page
            try:
                body_text = await page.inner_text("body")
                no_slot_phrases = ["no slot", "no batch", "not available", "no record",
                                   "no data", "currently no", "no seats", "please select exam date"]
                for phrase in no_slot_phrases:
                    if phrase in body_text.lower():
                        if not available and not booked:
                            result.raw_text = f"Portal message found: '{phrase}'"
                        break
            except Exception:
                pass

            logger.info(f"Check complete: {len(available)} available, {len(booked)} booked")
            return result

        except Exception as e:
            logger.error(f"Error checking slots: {e}", exc_info=True)
            return result
        finally:
            await page.context.close()

    async def health_check(self) -> bool:
        page = await self._new_page()
        try:
            await page.goto(SLOT_DETAILS_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            return True
        except Exception:
            return False
        finally:
            await page.context.close()
