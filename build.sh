#!/usr/bin/env bash
# Build script for Render deployment
set -e

pip install -r requirements.txt

# Install Playwright's Chromium browser + OS dependencies
playwright install --with-deps chromium
