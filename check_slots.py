"""
ICAI SPMT Batch Monitor - GitHub Actions Runner

This script runs on a schedule via GitHub Actions.
It checks for available slots using the config from GitHub Secrets,
and sends a Telegram message if new GREEN dates are found.
"""

import os
import json
import logging
import asyncio
from scraper import SPMTScraper
from telegram import Bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("GHActionRunner")

# Cache file to track seen slots across GH Action runs
# In GH Actions, the filesystem resets every run, so we use GitHub Actions Cache
# to persist this file.
CACHE_FILE = "seen_slots_cache.json"

async def main():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    state_val = os.getenv("STATE_VALUE")
    state_txt = os.getenv("STATE_TEXT", "State")
    city_val = os.getenv("CITY_VALUE")
    city_txt = os.getenv("CITY_TEXT", "City")
    centre_val = os.getenv("CENTRE_VALUE")
    centre_txt = os.getenv("CENTRE_TEXT", "Centre")

    if not all([bot_token, chat_id, state_val, city_val, centre_val]):
        logger.error("Missing required environment variables. Check GitHub Secrets.")
        return

    bot = Bot(token=bot_token)
    scraper = SPMTScraper()

    try:
        logger.info(f"Checking slots for {centre_txt}...")
        slot_info = await scraper.check_slots(
            state_val, city_val, centre_val,
            state_txt, city_txt, centre_txt
        )

        if not slot_info.has_availability():
            logger.info("No available green dates found.")
            # Optional: You could send a daily heartbeat message here
            return

        # Load previously seen slots
        seen_set = set()
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    seen_set = set(json.load(f))
            except Exception as e:
                logger.error(f"Error loading cache: {e}")

        new_dates = []
        for slot_date in slot_info.available_dates:
            key = slot_date.unique_key()
            if key not in seen_set:
                new_dates.append(slot_date)
                seen_set.add(key)

        # Save updated seen slots
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(list(seen_set), f)
        except Exception as e:
            logger.error(f"Error saving cache: {e}")

        if new_dates:
            dates_str = ", ".join(f"*{d.date}*" for d in new_dates)
            all_dates_str = ", ".join(d.date for d in slot_info.available_dates)

            msg = (
                f"🚨🟢 *NEW SLOTS AVAILABLE!* 🟢🚨\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🏢 *{centre_txt}*\n"
                f"🏙️ {city_txt}, {state_txt}\n"
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
                parse_mode="Markdown", disable_web_page_preview=True
            )
            logger.info(f"Notification sent for new dates: {dates_str}")
        else:
            logger.info("Green dates found, but already notified.")

    except Exception as e:
        logger.error(f"Error checking slots: {e}")
    finally:
        await scraper.close()

if __name__ == "__main__":
    asyncio.run(main())
