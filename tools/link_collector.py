import json, time, random, requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

BLOG_URL = "https://indolawas.blogspot.com"
OUT = "data/links.json"

HEADERS = {"User-Agent": "Mozilla/5.0"}

def collect():
    links = set()
    start = 1

    while True:
        feed = f"{BLOG_URL}/feeds/posts/default?alt=atom&start-index={start}&max-results=500"
        r = requests.get(feed, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            break

        root = ET.fromstring(r.text)
        entries = root.findall("{http://www.w3.org/2005/Atom}entry")
        if not entries:
            break

        for e in entries:
            for l in e.findall("{http://www.w3.org/2005/Atom}link"):
                if l.attrib.get("rel") in (None, "alternate"):
                    href = l.attrib.get("href")
                    if href and "/20" in href:
                        links.add(href)

        start += 500
        time.sleep(random.uniform(0.3, 0.7))

    # fallback archive
    r = requests.get(BLOG_URL, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.select("h2 a, h3 a"):
        href = a.get("href")
        if href and "/20" in href:
            links.add(href)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(sorted(links), f, indent=2)

    print(f"✅ Terkumpul {len(links)} link")

if __name__ == "__main__":
    collect()