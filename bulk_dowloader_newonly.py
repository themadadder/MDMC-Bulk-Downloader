import os
import re
import requests

# ============================
# CONFIGURATION
# ============================
OUTPUT_DIR = "mdmc_all"   # <--- CHANGE THIS
API_URL = "https://api.mdmc.moe/v3/charts"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================
# Sanitize filenames
# ============================
def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)

# ============================
# Download a single chart file
# ============================
def download_chart(chart_id, song_title):
    safe_name = sanitize_filename(song_title) + ".mdm"
    path = os.path.join(OUTPUT_DIR, safe_name)

    # --- NEW: Skip if file already exists ---
    if os.path.exists(path):
        print(f"Skipping (already downloaded): {safe_name}")
        return

    url = f"https://api.mdmc.moe/v3/charts/{chart_id}/download"
    print(f"Downloading: {song_title}")

    r = requests.get(url, stream=True)
    r.raise_for_status()

    with open(path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)

    print(f"Saved as: {safe_name}")

# ============================
# Fetch all chart metadata
# ============================
def fetch_all_charts():
    charts = []
    page = 1

    while True:
        print(f"Fetching page {page}...")
        resp = requests.get(API_URL, params={"page": page})
        resp.raise_for_status()
        data = resp.json()

        page_charts = data.get("charts", [])
        if not page_charts:
            break

        charts.extend(page_charts)
        page += 1

    return charts

# ============================
# MAIN
# ============================
charts = fetch_all_charts()
print(f"Found {len(charts)} charts")

for chart in charts:
    chart_id = chart.get("id") or chart.get("_id")
    title = chart.get("title") or "Unknown Title"

    try:
        download_chart(chart_id, title)
    except Exception as e:
        print(f"Failed to download {chart_id}: {e}")

print("Done.")
