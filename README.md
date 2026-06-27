# 🎓 ICAI SPMT Slot Monitor (GitHub Actions)

A Python script that runs automatically on **GitHub Actions** (100% free) to monitor the [ICAI SPMT Portal](https://spmt.icai.org/ICAI/LoginAction_showSlotDetails.action) calendar. It notifies you on Telegram when **🟢 green dates** (available slots) appear.

## ✨ Features

- 📅 **Calendar-aware** — uses Playwright (headless browser) to read green/red date highlighting.
- 🔔 **Smart alerts** — remembers what you've seen across runs using Actions Cache; only alerts for *new* dates.
- 💸 **100% Free Hosting** — runs as a cron job on GitHub Actions (gives you 2,000 free minutes/month).
- 🔓 **No ICAI login** — monitors the public slot details page.

## 🚀 Setup Guide

### Step 1: Create Telegram Bot & Get Chat ID
1. Open Telegram → search **@BotFather**
2. Send `/newbot` → follow instructions to create a bot.
3. Copy the **API token** (e.g., `7123...:AAH...`).
4. Search for your new bot in Telegram and send it a message (e.g., "Hello").
5. Find your **Chat ID** by visiting: 
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   (Look for `"chat":{"id":123456789}` in the response).

### Step 2: Get Your Centre Values
You need the internal values for your State, City, and Centre. Run the provided script locally once to find them.

```bash
pip install -r requirements.txt
playwright install chromium --with-deps
python discover.py
```
Follow the interactive prompts. At the end, it will print out the exact values you need to copy.

### Step 3: Deploy to GitHub
1. Create a **Private** repository on GitHub.
2. Push this code to the repository.
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```

### Step 4: Add GitHub Secrets
Go to your GitHub Repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

Add the following secrets using the values from Steps 1 and 2:
- `TELEGRAM_BOT_TOKEN`: Your BotFather token
- `TELEGRAM_CHAT_ID`: Your personal chat ID
- `STATE_VALUE`
- `STATE_TEXT`
- `CITY_VALUE`
- `CITY_TEXT`
- `CENTRE_VALUE`
- `CENTRE_TEXT`

### Step 5: Enable GitHub Actions
1. Go to the **Actions** tab in your GitHub repo.
2. If it asks you to enable workflows, click **"I understand my workflows, go ahead and enable them"**.
3. It will now automatically run every 15 minutes! 
4. You can also trigger it manually by clicking on "ICAI Slot Monitor" on the left, then **Run workflow**.

## 📁 Files

```
├── check_slots.py        # Main script that runs on GH Actions
├── discover.py           # Helper script to find dropdown values locally
├── scraper.py            # Playwright scraper logic
├── requirements.txt      # Dependencies
└── .github/
    └── workflows/
        └── monitor.yml   # GitHub Actions configuration
```

## ⚠️ Notes
- GitHub Actions cron jobs (`*/15 * * * *`) are not always exact; they may run slightly delayed depending on GitHub's load.
- The portal sometimes goes under maintenance — the script handles this gracefully and just exits.
- For **personal use** only.
