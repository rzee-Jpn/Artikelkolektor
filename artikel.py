#!/usr/bin/env python3 """ MODE B – DAILY AGGREGATOR

✔ Jalan harian (queue-based, GitHub Actions friendly) ✔ Ambil N artikel per hari ✔ Output HTML GABUNGAN seperti script LAMA parts/chord_part1.html, chord_part2.html, dst

Konsep: queue/links.json  -> antrian URL artikel results_cache.json -> cache snippet HTML per artikel parts/            -> hasil akhir (HTML gabungan) """

import os, json, time, re, requests from bs4 import BeautifulSoup from xml.etree import ElementTree as ET

================= CONFIG =================

BLOG_URL = os.environ.get("BLOG_URL", "https://indolawas.blogspot.com/") QUEUE_FILE = "queue/links.json" RESULTS_FILE = "results_cache.json" OUTPUT_DIR = "parts" PART_PREFIX = "chord_part" PART_SIZE = int(os.environ.get("PART_SIZE", "500")) DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "3"))  # MODE B: N artikel per hari TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))

HTML_HEAD = """ <!doctype html>

<html><head><meta charset='utf-8'>
<title>Chord Terpilih</title>
</head><body>
<h1>🎸 Chord Terpilih</h1>
"""
HTML_TAIL = "</body></html>"================= UTIL =================

def load_json(path, default): if os.path.exists(path): try: with open(path, "r", encoding="utf-8") as f: return json.load(f) except Exception: pass return default

def save_json(path, data): os.makedirs(os.path.dirname(path) or ".", exist_ok=True) with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def slugify(text): return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()

================= FEED → QUEUE =================

def init_queue_if_empty(): if os.path.exists(QUEUE_FILE): return

print("Queue belum ada, mengambil RSS Blogger…")
links = []
start = 1
while True:
    feed = f"{BLOG_URL.rstrip('/')}/feeds/posts/default?alt=rss&start-index={start}&max-results=500"
    r = requests.get(feed, timeout=TIMEOUT)
    if r.status_code != 200:
        break
    root = ET.fromstring(r.text)
    items = root.findall('.//item')
    if not items:
        break
    for it in items:
        link = it.findtext('link')
        if link and link not in links:
            links.append(link)
    start += 500
    time.sleep(0.2)

save_json(QUEUE_FILE, links)
print(f"Queue dibuat: {len(links)} artikel")

================= SCRAPER =================

def fetch_snippet(link): r = requests.get(link, timeout=TIMEOUT) r.raise_for_status() soup = BeautifulSoup(r.text, "html.parser")

title = soup.title.text if soup.title else "Tanpa Judul"

article = (
    soup.find("div", class_="post-body")
    or soup.find("div", class_="entry-content")
    or soup.find("article")
    or soup.find("div", id="post-body")
)

isi_html = article.decode_contents() if article else "<p>(Gagal ambil isi)</p>"

snippet = f"""

<hr>
<h2>{title}</h2>
{isi_html}
<p><a href=\"{link}\">🔗 Lihat Asli</a></p>
"""
    return snippet================= BUILD PARTS =================

def build_parts(results_cache): os.makedirs(OUTPUT_DIR, exist_ok=True) links = list(results_cache.keys())

for i in range(0, len(links), PART_SIZE):
    part_links = links[i:i+PART_SIZE]
    idx = i // PART_SIZE + 1
    path = os.path.join(OUTPUT_DIR, f"{PART_PREFIX}{idx}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(HTML_HEAD)
        for l in part_links:
            f.write(results_cache[l])
        f.write(HTML_TAIL)

print(f"✔ Parts dibangun: {((len(links)-1)//PART_SIZE)+1}")

================= DAILY RUN =================

def daily_run(): init_queue_if_empty()

queue = load_json(QUEUE_FILE, [])
results = load_json(RESULTS_FILE, {})

if not queue:
    print("Queue kosong. Tidak ada pekerjaan.")
    return

today = min(DAILY_LIMIT, len(queue))
print(f"Ambil {today} artikel hari ini…")

for i in range(today):
    link = queue.pop(0)
    print(f"[{i+1}/{today}] {link}")
    try:
        if link not in results:
            results[link] = fetch_snippet(link)
    except Exception as e:
        print("Error:", e)

save_json(QUEUE_FILE, queue)
save_json(RESULTS_FILE, results)
build_parts(results)

print("Selesai. Sisa queue:", len(queue))

================= ENTRY =================

if name == "main": daily_run()