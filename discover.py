"""
ICAI SPMT - Discover available States, Cities, and Test Centres.

Run this ONCE locally to find the values you need for GitHub Actions config.

Usage:
    pip install playwright
    playwright install chromium --with-deps
    python discover.py
"""

import asyncio
from scraper import SPMTScraper


async def main():
    scraper = SPMTScraper()

    print("=" * 60)
    print("  ICAI SPMT — Discover Your Centre Details")
    print("=" * 60)
    print("\n🔄 Connecting to SPMT portal (takes ~15s)...\n")

    # Step 1: Get States
    states = await scraper.get_states()
    if not states:
        print("❌ Could not fetch states. Portal may be under maintenance.")
        await scraper.close()
        return

    print("📍 Available States:")
    print("-" * 40)
    for i, s in enumerate(states, 1):
        print(f"  {i}. {s.text}  (value: {s.value})")

    print()
    choice = input("Enter state number: ").strip()
    try:
        state = states[int(choice) - 1]
    except (ValueError, IndexError):
        print("Invalid choice.")
        await scraper.close()
        return

    # Step 2: Get Cities
    print(f"\n🔄 Fetching cities for {state.text}...\n")
    cities = await scraper.get_cities(state.value)
    if not cities:
        print("❌ No cities found for this state.")
        await scraper.close()
        return

    print("🏙️ Available Cities:")
    print("-" * 40)
    for i, c in enumerate(cities, 1):
        print(f"  {i}. {c.text}  (value: {c.value})")

    print()
    choice = input("Enter city number: ").strip()
    try:
        city = cities[int(choice) - 1]
    except (ValueError, IndexError):
        print("Invalid choice.")
        await scraper.close()
        return

    # Step 3: Get Test Centres
    print(f"\n🔄 Fetching test centres in {city.text}...\n")
    centres = await scraper.get_test_centres(state.value, city.value)
    if not centres:
        print("❌ No test centres found.")
        await scraper.close()
        return

    print("🏢 Available Test Centres:")
    print("-" * 40)
    for i, c in enumerate(centres, 1):
        print(f"  {i}. {c.text}  (value: {c.value})")

    print()
    choice = input("Enter centre number: ").strip()
    try:
        centre = centres[int(choice) - 1]
    except (ValueError, IndexError):
        print("Invalid choice.")
        await scraper.close()
        return

    # Print the config
    print("\n" + "=" * 60)
    print("  ✅ YOUR CONFIGURATION")
    print("=" * 60)
    print(f"""
    State:  {state.text}
    City:   {city.text}
    Centre: {centre.text}

  Copy these values into your GitHub Secrets:

    STATE_VALUE  = {state.value}
    STATE_TEXT   = {state.text}
    CITY_VALUE   = {city.value}
    CITY_TEXT    = {city.text}
    CENTRE_VALUE = {centre.value}
    CENTRE_TEXT  = {centre.text}
""")
    print("=" * 60)

    await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
