#!/bin/bash
# Install dependencies and Playwright browser, then run the bot
git clone https://github.com/sohilmahto0410/ICAI-SPOM-MONITOR.git SPOM
cd SPOM

# Install Python deps
pip install -r requirements.txt

# Install Playwright's Chromium browser (one-time, ~150MB)
playwright install chromium --with-deps

# Configure
echo 'TELEGRAM_BOT_TOKEN=8666798642:AAGTrAh7N5CIOSbe0YxaZju7qHsxUG3XUkU' > .env
echo 'CHECK_INTERVAL_MINUTES=3' >> .env

# Run!
python bot.py
