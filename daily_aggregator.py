#!/usr/bin/env python3
"""
MODE C – BLOG QUEUE AGGREGATOR SUPER AMAN

✔ 1 blog diproses penuh per hari
✔ Antrian per blog
✔ Output HTML dipisah per blog
✔ Resume otomatis per artikel jika workflow mati
✔ Delay acak & jeda panjang untuk anti-deteksi spam
"""

import os
import json
import time
import random
import requests
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET
from urllib.parse import urlparse

# ================= CONFIG =================

QUEUE_FILE = "queue/blogs.json"
DONE_FILE = "queue/blogs_done.json"
OUTPUT_ROOT = "output"
CACHE_ROOT = "cache"

PART_SIZE = 500
TIMEOUT = 20

# Super aman delay config
REQUEST_DELAY_MIN = 0.6
REQUEST_DELAY_MAX = 1.4
LONG_PAUSE_EVERY = 25
LONG_PAUSE_MIN = 5
LONG_PAUSE_MAX = 12
ERROR_PAUSE = 10

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}

HTML_HEAD = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Chord Blog</title>
</head>
<body>
<h1>🎸 Arsip Chord</h1>
"""

HTML_TAIL = "</body></html>"

# ================= UTIL =================

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def blog_slug(url):
    return urlparse(url).netloc

# ================= RSS =================

def fetch_all_links(blog_url):
    print("Ambil RSS:", blog_url)
    links = []
    start = 1

    while True:
        feed = f"{blog_url.rstrip('/')}/feeds/posts/default?alt=rss&start-index={start}&max-results=500"
        try:
            r = requests.get(feed, timeout=TIMEOUT, headers=HEADERS)
            if r.status_code != 200:
                break

            root = ET.fromstring(r.text)
            items = root.findall(".//item")
            if not items:
                break

            for it in items:
                link = it.findtext("link")
                if link:
                    links.append(link)

            start += 500
            time.sleep(random.uniform(0.2,0.5))
        except Exception as e:
            print("Error ambil RSS:", e)
            time.sleep(ERROR_PAUSE)

    print(f"Total artikel: {len(links)}")
    return links

# ================= SCRAPER =================

def fetch_article(link):
    try:
        r = requests.get(link, timeout=TIMEOUT, headers=HEADERS)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.text if soup.title else "Tanpa Judul"

        body = (
            soup.find("div", class_="post-body")
            or soup.find("div", class_="entry-content")
            or soup.find("article")
        )

        content = body.decode_contents() if body else "<p>(Gagal ambil isi)</p>"
        return f"<hr>\n<h2>{title}</h2>\n{content}\n<p><a href='{link}' target='_blank'>🔗 Sumber</a></p>"
    except Exception as e:
        print("Error fetch article:", e)
        time.sleep(ERROR_PAUSE)
        return f"<hr><p>Gagal ambil: {link}</p>"

# ================= BUILD =================

def build_parts(blog_dir, snippets):
    parts = []
    for i in range(0, len(snippets), PART_SIZE):
        part_no = i // PART_SIZE + 1
        part_path = os.path.join(blog_dir, f"part{part_no}.html")
        parts.append(part_path)

        with open(part_path, "w", encoding="utf-8") as f:
            f.write(HTML_HEAD)
            for s in snippets[i:i + PART_SIZE]:
                f.write(s)
            f.write(HTML_TAIL)

    index = os.path.join(blog_dir, "index.html")
    with open(index, "w", encoding="utf-8") as f:
        f.write(HTML_HEAD)
        f.write("<h2>Daftar Bagian</h2><ul>")
        for p in parts:
            name = os.path.basename(p)
            f.write(f'<li><a href="{name}">{name}</a></li>')
        f.write("</ul>")
        f.write(HTML_TAIL)

# ================= MAIN =================

def main():
    blogs = load_json(QUEUE_FILE, [])
    done = load_json(DONE_FILE, [])

    if not blogs:
        print("Tidak ada blog dalam antrian.")
        return

    blog = blogs.pop(0)
    slug = blog_slug(blog)
    print("Proses blog:", blog)

    links = fetch_all_links(blog)
    blog_cache_file = os.path.join(CACHE_ROOT, f"{slug}.json")
    snippets_cache = load_json(blog_cache_file, {})

    snippets = []
    for i, link in enumerate(links, 1):
        if link in snippets_cache:
            snippets.append(snippets_cache[link])
            continue

        print(f"[{i}/{len(links)}] {link}")
        snippet = fetch_article(link)
        snippets.append(snippet)
        snippets_cache[link] = snippet

        # Delay acak
        delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
        time.sleep(delay)

        # Jeda panjang tiap N artikel
        if i % LONG_PAUSE_EVERY == 0:
            long_pause = random.uniform(LONG_PAUSE_MIN, LONG_PAUSE_MAX)
            print(f"⏸ Jeda panjang {int(long_pause)} detik…")
            time.sleep(long_pause)

        # Simpan cache setiap 10 artikel
        if i % 10 == 0:
            save_json(blog_cache_file, snippets_cache)

    # Simpan cache terakhir
    save_json(blog_cache_file, snippets_cache)

    blog_dir = os.path.join(OUTPUT_ROOT, slug)
    os.makedirs(blog_dir, exist_ok=True)
    build_parts(blog_dir, snippets)

    done.append(blog)
    save_json(QUEUE_FILE, blogs)
    save_json(DONE_FILE, done)

    print("SELESAI:", blog)

# ================= ENTRY =================

if __name__ == "__main__":
    os.makedirs(CACHE_ROOT, exist_ok=True)
    main()