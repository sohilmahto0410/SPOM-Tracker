"""
ICAI SPMT Batch Monitor - Telegram Bot

Interactive Telegram bot that lets you:
1. Select State → City → Test Centre via inline buttons
2. Start/stop monitoring for slot availability
3. Get instant notifications when GREEN dates appear on the calendar
"""

import os
import json
import logging
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from scraper import SPMTScraper, SlotInfo, SlotDate, DropdownOption

# ─── Configuration ────────────────────────────────────────────────────────────
dotenv_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=dotenv_path)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
chromium_path = os.getenv("CHROMIUM_EXECUTABLE_PATH", "")

print("==========================================")
print("🎓 ICAI SPMT SLOT MONITOR STARTING...")
print(f"• Telegram Token Loaded: {'Yes' if BOT_TOKEN else 'No'}")
print(f"• Check Interval: {CHECK_INTERVAL} minutes")
print(f"• Chromium Path: '{chromium_path}'")
print("==========================================")

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

USER_CONFIG_FILE = DATA_DIR / "user_configs.json"
SEEN_SLOTS_FILE = DATA_DIR / "seen_slots.json"

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("BatchMonitorBot")

# ─── State Management ────────────────────────────────────────────────────────
user_setup_state: dict = {}

dropdown_cache: dict = {
    "states": [],
    "cities": {},
    "centres": {},
}


def load_user_configs() -> dict:
    if USER_CONFIG_FILE.exists():
        try:
            with open(USER_CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_user_configs(configs: dict):
    with open(USER_CONFIG_FILE, "w") as f:
        json.dump(configs, f, indent=2)


def load_seen_slots() -> dict:
    if SEEN_SLOTS_FILE.exists():
        try:
            with open(SEEN_SLOTS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_seen_slots(seen: dict):
    with open(SEEN_SLOTS_FILE, "w") as f:
        json.dump(seen, f, indent=2)


# ─── Scraper Instance ────────────────────────────────────────────────────────
scraper = SPMTScraper()


# ─── Telegram Command Handlers ────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    welcome = (
        "🎓 *ICAI SPMT Slot Monitor Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "I monitor the ICAI SPMT portal calendar and\n"
        "notify you when 🟢 *GREEN dates* appear\\!\n\n"
        "📋 *Commands:*\n"
        "  /setup — Select State, City \\& Test Centre\n"
        "  /monitor — Start monitoring\n"
        "  /stop — Pause monitoring\n"
        "  /status — View your configuration\n"
        "  /check — Check slots right now\n"
        "  /reset — Clear all settings\n"
        "  /help — Show this message\n\n"
        "👉 Start with /setup to configure\\!"
    )
    await update.message.reply_text(welcome, parse_mode="MarkdownV2")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the setup flow — fetch and display states."""
    chat_id = str(update.effective_chat.id)
    msg = await update.message.reply_text(
        "🔄 Connecting to ICAI SPMT portal...\n"
        "This may take 15-30 seconds (loading page in headless browser)."
    )

    try:
        if dropdown_cache["states"]:
            states = dropdown_cache["states"]
        else:
            states = await scraper.get_states()
            dropdown_cache["states"] = states

        if not states:
            await msg.edit_text(
                "❌ Could not fetch states from the ICAI portal.\n\n"
                "Possible reasons:\n"
                "• Portal is under maintenance\n"
                "• Network issue\n"
                "• Page structure changed\n\n"
                "Try again in a few minutes with /setup"
            )
            return

        user_setup_state[chat_id] = {"step": "select_state"}

        # Create inline keyboard (2 per row)
        keyboard = []
        row = []
        for state in states:
            # Telegram callback_data max is 64 bytes
            cb_data = f"st:{state.value}:{state.text[:40]}"
            if len(cb_data.encode()) > 64:
                cb_data = f"st:{state.value}:{state.text[:25]}"
            row.append(InlineKeyboardButton(state.text, callback_data=cb_data))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        await msg.edit_text(
            f"📍 *Step 1/3: Select your State*\n\n"
            f"Found {len(states)} states. Tap to select:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(f"Setup error: {e}")
        await msg.edit_text(f"❌ Error: {e}\nTry /setup again.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    chat_id = str(query.message.chat_id)
    data = query.data

    if data.startswith("st:"):
        await _handle_state_selection(query, chat_id, data)
    elif data.startswith("ci:"):
        await _handle_city_selection(query, chat_id, data)
    elif data.startswith("ce:"):
        await _handle_centre_selection(query, chat_id, data)
    elif data == "confirm_yes":
        await _handle_confirm(query, chat_id, context)
    elif data == "confirm_no":
        await query.edit_message_text("❌ Setup cancelled. Use /setup to start over.")
        user_setup_state.pop(chat_id, None)


async def _handle_state_selection(query, chat_id: str, data: str):
    """User selected a state → fetch cities."""
    parts = data.split(":", 2)
    state_value = parts[1]
    state_text = parts[2] if len(parts) > 2 else state_value

    user_setup_state[chat_id] = {
        "step": "select_city",
        "state_value": state_value,
        "state_text": state_text,
    }

    await query.edit_message_text(
        f"✅ State: {state_text}\n\n🔄 Fetching cities..."
    )

    try:
        cache_key = state_value
        if cache_key in dropdown_cache["cities"]:
            cities = dropdown_cache["cities"][cache_key]
        else:
            cities = await scraper.get_cities(state_value)
            dropdown_cache["cities"][cache_key] = cities

        if not cities:
            await query.edit_message_text(
                f"✅ State: {state_text}\n\n"
                "❌ No cities found. Portal may not have data for this state.\n"
                "Try /setup for a different state."
            )
            return

        keyboard = []
        row = []
        for city in cities:
            cb_data = f"ci:{city.value}:{city.text[:40]}"
            if len(cb_data.encode()) > 64:
                cb_data = f"ci:{city.value}:{city.text[:25]}"
            row.append(InlineKeyboardButton(city.text, callback_data=cb_data))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        await query.edit_message_text(
            f"✅ State: *{state_text}*\n\n"
            f"📍 *Step 2/3: Select your City*\n"
            f"Found {len(cities)} cities:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(f"City fetch error: {e}")
        await query.edit_message_text(f"❌ Error: {e}\nTry /setup again.")


async def _handle_city_selection(query, chat_id: str, data: str):
    """User selected a city → fetch test centres."""
    parts = data.split(":", 2)
    city_value = parts[1]
    city_text = parts[2] if len(parts) > 2 else city_value

    setup = user_setup_state.get(chat_id, {})
    setup["city_value"] = city_value
    setup["city_text"] = city_text
    setup["step"] = "select_centre"
    user_setup_state[chat_id] = setup

    state_text = setup.get("state_text", "?")
    await query.edit_message_text(
        f"✅ State: {state_text}\n✅ City: {city_text}\n\n🔄 Fetching test centres..."
    )

    try:
        state_value = setup["state_value"]
        cache_key = f"{state_value}::{city_value}"
        if cache_key in dropdown_cache["centres"]:
            centres = dropdown_cache["centres"][cache_key]
        else:
            centres = await scraper.get_test_centres(state_value, city_value)
            dropdown_cache["centres"][cache_key] = centres

        if not centres:
            await query.edit_message_text(
                f"✅ State: {state_text}\n✅ City: {city_text}\n\n"
                "❌ No test centres found. Try /setup."
            )
            return

        keyboard = []
        for centre in centres:
            cb_data = f"ce:{centre.value}:{centre.text[:40]}"
            if len(cb_data.encode()) > 64:
                cb_data = f"ce:{centre.value}:{centre.text[:25]}"
            keyboard.append([InlineKeyboardButton(centre.text, callback_data=cb_data)])

        await query.edit_message_text(
            f"✅ State: *{state_text}*\n"
            f"✅ City: *{city_text}*\n\n"
            f"📍 *Step 3/3: Select Test Centre*\n"
            f"Found {len(centres)} centres:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(f"Centre fetch error: {e}")
        await query.edit_message_text(f"❌ Error: {e}\nTry /setup again.")


async def _handle_centre_selection(query, chat_id: str, data: str):
    """User selected a test centre → show confirmation."""
    parts = data.split(":", 2)
    centre_value = parts[1]
    centre_text = parts[2] if len(parts) > 2 else centre_value

    setup = user_setup_state.get(chat_id, {})
    setup["centre_value"] = centre_value
    setup["centre_text"] = centre_text
    user_setup_state[chat_id] = setup

    keyboard = [[
        InlineKeyboardButton("✅ Confirm & Start", callback_data="confirm_yes"),
        InlineKeyboardButton("❌ Cancel", callback_data="confirm_no"),
    ]]

    await query.edit_message_text(
        f"📋 *Configuration Summary*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📍 State: *{setup.get('state_text', '?')}*\n"
        f"🏙️ City: *{setup.get('city_text', '?')}*\n"
        f"🏢 Centre: *{centre_text}*\n\n"
        f"⏱️ Checking every {CHECK_INTERVAL} minutes\n"
        f"🔍 Looking for: 🟢 GREEN dates on calendar\n\n"
        f"Confirm to start monitoring?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def _handle_confirm(query, chat_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Confirm setup and start monitoring."""
    setup = user_setup_state.get(chat_id, {})
    if not setup:
        await query.edit_message_text("❌ No setup data. Use /setup.")
        return

    configs = load_user_configs()
    configs[chat_id] = {
        "state_value": setup.get("state_value", ""),
        "state_text": setup.get("state_text", ""),
        "city_value": setup.get("city_value", ""),
        "city_text": setup.get("city_text", ""),
        "centre_value": setup.get("centre_value", ""),
        "centre_text": setup.get("centre_text", ""),
        "monitoring": True,
        "created_at": datetime.now().isoformat(),
    }
    save_user_configs(configs)
    user_setup_state.pop(chat_id, None)

    await query.edit_message_text(
        f"✅ *Monitoring Activated!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📍 {setup.get('state_text')} → {setup.get('city_text')} → {setup.get('centre_text')}\n\n"
        f"🟢 I'll check the calendar every *{CHECK_INTERVAL} min*\n"
        f"and notify you when GREEN dates appear!\n\n"
        f"/check — check right now\n"
        f"/stop — pause monitoring",
        parse_mode="Markdown",
    )

    # Do an immediate check
    await _do_check(int(chat_id), context.bot)


async def cmd_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    configs = load_user_configs()
    if chat_id not in configs:
        await update.message.reply_text("⚠️ Not configured yet. Use /setup first.")
        return
    configs[chat_id]["monitoring"] = True
    save_user_configs(configs)
    cfg = configs[chat_id]
    await update.message.reply_text(
        f"✅ Monitoring resumed!\n"
        f"📍 {cfg['state_text']} → {cfg['city_text']} → {cfg['centre_text']}\n"
        f"⏱️ Every {CHECK_INTERVAL} min"
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    configs = load_user_configs()
    if chat_id in configs:
        configs[chat_id]["monitoring"] = False
        save_user_configs(configs)
    await update.message.reply_text("⏸️ Monitoring paused.\n/monitor to resume.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    configs = load_user_configs()
    if chat_id not in configs:
        await update.message.reply_text("📭 Not configured. Use /setup.")
        return
    cfg = configs[chat_id]
    status = "🟢 Active" if cfg.get("monitoring") else "🔴 Paused"
    await update.message.reply_text(
        f"📋 *Your Configuration*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📍 State: {cfg.get('state_text')}\n"
        f"🏙️ City: {cfg.get('city_text')}\n"
        f"🏢 Centre: {cfg.get('centre_text')}\n\n"
        f"📡 Status: {status}\n"
        f"⏱️ Interval: Every {CHECK_INTERVAL} min",
        parse_mode="Markdown",
    )


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    configs = load_user_configs()
    if chat_id not in configs:
        await update.message.reply_text("⚠️ Not configured. Use /setup first!")
        return
    await _do_check(update.effective_chat.id, context.bot)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    configs = load_user_configs()
    configs.pop(chat_id, None)
    save_user_configs(configs)
    seen = load_seen_slots()
    seen.pop(chat_id, None)
    save_seen_slots(seen)
    user_setup_state.pop(chat_id, None)
    await update.message.reply_text("🗑️ All settings cleared! Use /setup to reconfigure.")


# ─── Slot Checking Logic ─────────────────────────────────────────────────────

async def _do_check(chat_id: int, bot):
    """Check for available slots and notify the user."""
    chat_id_str = str(chat_id)
    configs = load_user_configs()
    if chat_id_str not in configs:
        return

    cfg = configs[chat_id_str]

    await bot.send_message(
        chat_id=chat_id,
        text=f"🔍 Checking calendar at *{cfg['centre_text']}*...\n"
             f"(Opening portal in headless browser)",
        parse_mode="Markdown",
    )

    try:
        slot_info: SlotInfo = await scraper.check_slots(
            cfg["state_value"], cfg["city_value"], cfg["centre_value"],
            cfg.get("state_text", ""), cfg.get("city_text", ""),
            cfg.get("centre_text", ""),
        )

        if not slot_info.has_availability():
            msg = (
                f"📭 *No green dates* on the calendar\n"
                f"📍 {cfg['centre_text']}\n"
                f"🏙️ {cfg['city_text']}, {cfg['state_text']}\n"
            )
            if slot_info.calendar_month:
                msg += f"📅 Calendar showing: {slot_info.calendar_month}\n"
            if slot_info.booked_dates:
                booked_str = ", ".join(d.date for d in slot_info.booked_dates[:10])
                msg += f"🔴 Booked dates: {booked_str}\n"
            if slot_info.raw_text:
                msg += f"\n💬 {slot_info.raw_text}\n"
            msg += f"\n⏱️ Next check in {CHECK_INTERVAL} min"

            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            return

        # We have available dates! Check which are new
        seen = load_seen_slots()
        seen_set = set(seen.get(chat_id_str, []))

        new_dates = []
        for slot_date in slot_info.available_dates:
            key = slot_date.unique_key()
            if key not in seen_set:
                new_dates.append(slot_date)
                seen_set.add(key)

        seen[chat_id_str] = list(seen_set)
        save_seen_slots(seen)

        if new_dates:
            dates_str = ", ".join(f"*{d.date}*" for d in new_dates)
            all_dates_str = ", ".join(d.date for d in slot_info.available_dates)

            msg = (
                f"🚨🟢 *SLOTS AVAILABLE!* 🟢🚨\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🏢 *{cfg['centre_text']}*\n"
                f"🏙️ {cfg['city_text']}, {cfg['state_text']}\n"
            )
            if slot_info.calendar_month:
                msg += f"📅 {slot_info.calendar_month}\n"
            msg += (
                f"\n🆕 *New green dates:* {dates_str}\n"
                f"🟢 All available dates: {all_dates_str}\n"
            )
            if slot_info.booked_dates:
                booked_str = ", ".join(d.date for d in slot_info.booked_dates[:10])
                msg += f"🔴 Booked dates: {booked_str}\n"
            msg += (
                f"\n🔗 [Book Now on SPMT Portal]"
                f"(https://spmt.icai.org/ICAI/LoginAction_showSlotDetails.action)\n\n"
                f"⚡ *Hurry! Slots fill up fast!*"
            )

            await bot.send_message(
                chat_id=chat_id, text=msg,
                parse_mode="Markdown", disable_web_page_preview=True,
            )
        else:
            all_dates_str = ", ".join(d.date for d in slot_info.available_dates)
            msg = (
                f"📊 Green dates found but all previously seen:\n"
                f"🟢 {all_dates_str}\n"
                f"📍 {cfg['centre_text']}\n\n"
                f"I'll notify when *new* dates appear."
            )
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Check error for {chat_id}: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Error checking slots: {str(e)[:200]}\nWill retry next interval.",
        )


# ─── Scheduled Job ────────────────────────────────────────────────────────────

async def scheduled_check(context: ContextTypes.DEFAULT_TYPE):
    """Periodic job — check slots for all active monitors."""
    logger.info("Running scheduled slot check...")
    configs = load_user_configs()

    for chat_id_str, cfg in configs.items():
        if not cfg.get("monitoring"):
            continue
        try:
            await _do_check(int(chat_id_str), context.bot)
            await asyncio.sleep(5)  # Small delay between users
        except Exception as e:
            logger.error(f"Scheduled check error for {chat_id_str}: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
        print("=" * 60)
        print("  ERROR: TELEGRAM_BOT_TOKEN not configured!")
        print()
        print("  Steps:")
        print("  1. Open Telegram → search @BotFather")
        print("  2. Send /newbot and follow instructions")
        print("  3. Copy the API token")
        print("  4. Edit .env file:")
        print("     TELEGRAM_BOT_TOKEN=your_token_here")
        print("=" * 60)
        return

    print("🎓 ICAI SPMT Slot Monitor Bot")
    print("━" * 40)
    print(f"  Check interval: {CHECK_INTERVAL} minutes")
    print(f"  Method:         Headless browser (Playwright)")
    print(f"  Looking for:    🟢 Green calendar dates")
    print("━" * 40)
    print("  Starting bot...")

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("monitor", cmd_monitor))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("reset", cmd_reset))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Scheduled job
    app.job_queue.run_repeating(
        scheduled_check,
        interval=CHECK_INTERVAL * 60,
        first=CHECK_INTERVAL * 60,
        name="slot_check",
    )

    print("  ✅ Bot running! Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
