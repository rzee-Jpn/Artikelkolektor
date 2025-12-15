#!/usr/bin/env python3
import os, json, re, requests
from bs4 import BeautifulSoup

QUEUE_FILE = "queue/links.json"
OUTPUT_DIR = "output"
TIMEOUT = 20

# ---------- UTIL ----------
def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_queue(q):
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(q, f, indent=2)

def slugify(text):
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text)
    return text.strip("-")

# ---------- CORE ----------
def process_one():
    queue = load_queue()

    if not queue:
        print("Queue kosong. Tidak ada pekerjaan.")
        return

    link = queue.pop(0)
    print("Memproses:", link)

    r = requests.get(link, timeout=TIMEOUT)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    title = soup.title.text if soup.title else "tanpa-judul"
    folder = slugify(title)

    out_dir = os.path.join(OUTPUT_DIR, folder)
    os.makedirs(out_dir, exist_ok=True)

    article = (
        soup.find("div", class_="post-body")
        or soup.find("div", class_="entry-content")
        or soup.find("article")
    )

    content = article.decode_contents() if article else "<p>Gagal ambil konten</p>"

    with open(os.path.join(out_dir, "result.html"), "w", encoding="utf-8") as f:
        f.write(content)

    save_queue(queue)

    print("Selesai 1 link. Sisa:", len(queue))

# ---------- ENTRY ----------
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--daily-one", action="store_true")
    args = p.parse_args()

    if args.daily_one:
        process_one()