# 🎓 ICAI SPMT Slot Monitor Bot

A Telegram bot that monitors the [ICAI SPMT Portal](https://spmt.icai.org/ICAI/LoginAction_showSlotDetails.action) calendar and notifies you when **🟢 green dates** (available slots) appear.

## How It Actually Works

The SPMT portal shows slot availability on a **calendar datepicker**:
- 🟢 **Green** = Slots available → **Bot notifies you!**
- 🔴 **Red** = Fully booked
- ⬜ **Grey** = No slots / Holiday

This bot uses **Playwright** (headless Chromium browser) to:
1. Open the portal page
2. Select your State → City → Test Centre via dropdowns
3. **Read the calendar** — inspects CSS classes & background colors of each date cell
4. Detect green-highlighted dates = available slots
5. Notify you on Telegram only for **new** green dates

## ✨ Features

- 🎯 **Interactive Setup** via inline Telegram buttons
- 📅 **Calendar-aware** — reads green/red date highlighting
- 🔔 **Smart alerts** — only notifies about *new* green dates
- 💾 **Persistent config** — survives restarts
- 🔓 **No ICAI login** — monitors the public slot details page

## 🚀 Quick Start

### 1. Create Telegram Bot
1. Open Telegram → search **@BotFather**
2. Send `/newbot` → follow instructions
3. Copy the API token

### 2. Install & Run

```bash
cd "Batch Monitor"

# Install Python deps
pip install -r requirements.txt

# Install Playwright's Chromium browser (one-time, ~150MB)
playwright install chromium --with-deps

# Configure
echo 'TELEGRAM_BOT_TOKEN=your_token_here' > .env
echo 'CHECK_INTERVAL_MINUTES=5' >> .env

# Run!
python bot.py
```

Or use the start script:
```bash
chmod +x start.sh
./start.sh
```

### 3. Use the Bot
Open your bot on Telegram → `/setup` → select State/City/Centre → done!

---

## ☁️ Free Hosting

Since the bot needs a headless browser, it needs a platform that supports Playwright/Chromium.

### Option 1: Render (Recommended) 🌟

1. Push code to GitHub:
   ```bash
   git init && git add . && git commit -m "ICAI slot monitor"
   git remote add origin https://github.com/YOU/icai-slot-monitor.git
   git push -u origin main
   ```

2. Go to [render.com](https://render.com) → New → **Background Worker**

3. Settings:
   - **Build Command**: `pip install -r requirements.txt && playwright install chromium --with-deps`
   - **Start Command**: `python bot.py`

4. Add env vars:
   - `TELEGRAM_BOT_TOKEN` = your token
   - `CHECK_INTERVAL_MINUTES` = `5`

5. Deploy! ✅

### Option 2: Railway

1. Push to GitHub
2. [railway.app](https://railway.app) → Deploy from repo
3. Set env vars + build/start commands (same as Render)
4. Gets $5 free credits/month

### Option 3: Oracle Cloud Free Tier (Always Free VPS)

Best for 24/7 uptime — gives you a free Linux VM forever:

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com) (free tier)
2. Create an **Always Free** VM (ARM Ampere A1 or AMD E2.1.Micro)
3. SSH in and run:
   ```bash
   sudo apt update && sudo apt install -y python3 python3-pip
   git clone https://github.com/YOU/icai-slot-monitor.git
   cd icai-slot-monitor
   pip3 install -r requirements.txt
   playwright install chromium --with-deps
   echo 'TELEGRAM_BOT_TOKEN=your_token' > .env
   # Run in background with screen
   screen -S bot
   python3 bot.py
   # Ctrl+A then D to detach
   ```

### Option 4: Your Own PC

```bash
# Just keep it running
python bot.py

# Or in background (Linux/Mac)
nohup python bot.py &
```

---

## 🤖 Bot Commands

| Command    | Description                              |
|------------|------------------------------------------|
| `/start`   | Welcome message                          |
| `/setup`   | Pick State → City → Test Centre          |
| `/check`   | Check calendar right now                 |
| `/monitor` | Resume monitoring                        |
| `/stop`    | Pause monitoring                         |
| `/status`  | View your config                         |
| `/reset`   | Clear all settings                       |

## 📁 Files

```
├── bot.py              # Telegram bot — commands, setup, scheduling
├── scraper.py          # Playwright scraper — reads calendar green dates
├── requirements.txt    # Dependencies
├── start.sh            # Quick start script
├── .env                # Your bot token (edit this!)
├── Procfile            # For Render/Railway deployment
└── data/               # Auto-created: configs & seen slots
```

## ⚠️ Notes

- The portal sometimes goes under maintenance — bot handles this gracefully
- First run takes ~30s (Playwright loads Chromium + navigates the page)
- Subsequent checks are faster (~10-15s each)
- Keep intervals at 5+ minutes to be respectful
- For **personal use** only
