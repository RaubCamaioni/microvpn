"""Image build helper: download the current DB-IP "IP to City Lite" MMDB (or last month's) to the
given path. CC BY 4.0, https://db-ip.com. Exits non-zero if neither month can be fetched, so a
build never silently ships without the database."""

import datetime
import gzip
import os
import shutil
import sys
import urllib.request

out = sys.argv[1]
today = datetime.date.today().replace(day=1)
months = [today, (today - datetime.timedelta(days=1)).replace(day=1)]
for month in months:
    url = f"https://download.db-ip.com/free/dbip-city-lite-{month:%Y-%m}.mmdb.gz"
    # the download host answers 403 to Python's default user agent
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) microvpn-image-build"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response, open(out + ".gz", "wb") as f:
            shutil.copyfileobj(response, f)
    except Exception as e:  # noqa: BLE001 - try the previous month
        print(f"{url}: {e}", file=sys.stderr)
        continue
    with gzip.open(out + ".gz", "rb") as src, open(out, "wb") as dst:
        shutil.copyfileobj(src, dst)
    os.remove(out + ".gz")
    print(f"dbip: {url} -> {out} ({os.path.getsize(out) // 1_000_000} MB)")
    sys.exit(0)
print("dbip: no database could be downloaded", file=sys.stderr)
sys.exit(1)
