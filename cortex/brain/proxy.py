import urllib.request
import json
from ..logging_setup import get_logger

log = get_logger("brain.proxy")

def get_free_proxy() -> str | None:
    """Fetches a free HTTP proxy from proxyscrape or similar public API."""
    try:
        req = urllib.request.Request(
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
            headers={"User-Agent": "PlexusLab/1.0"}
        )
        res = urllib.request.urlopen(req, timeout=5)
        text = res.read().decode("utf-8").strip()
        proxies = text.splitlines()
        if proxies:
            log.info(f"Fetched {len(proxies)} proxies, returning first: {proxies[0]}")
            return f"http://{proxies[0]}"
    except Exception as e:
        log.warning(f"Failed to fetch proxies: {e}")
    return None
