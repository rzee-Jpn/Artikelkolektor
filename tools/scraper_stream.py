import json, time, random, os, requests
from bs4 import BeautifulSoup
from tools.html_cleaner import clean_html

LINKS_FILE = "data/links.json"
PROGRESS_FILE = "data/progress.json"
OUTPUT = "output/chord.html"

HEADERS = {"User-Agent": "Mozilla/5.0"}

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        return set(json.load(open(PROGRESS_FILE)))
    return set()

def save_progress(done):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(list(done), f)

def init_output():
    if not os.path.exists(OUTPUT):
        with open(OUTPUT, "w", encoding="utf-8") as f:
            f.write("<html><body><h1>🎸 Koleksi Chord</h1>")

def scrape():
    links = json.load(open(LINKS_FILE))
    done = load_progress()
    init_output()

    for i, link in enumerate(links, 1):
        if link in done:
            continue

        try:
            r = requests.get(link, headers=HEADERS, timeout=20)
            soup = BeautifulSoup(r.text, "html.parser")

            body = (
                soup.find("div", class_="post-body")
                or soup.find("article")
            )

            content = clean_html(body.decode_contents()) if body else "<p>(Kosong)</p>"
            title = soup.title.text if soup.title else "Tanpa Judul"

            with open(OUTPUT, "a", encoding="utf-8") as f:
                f.write(f"<hr><h2>{title}</h2>{content}<a href='{link}'>Sumber</a>")

            done.add(link)
            save_progress(done)

            print(f"✅ {i}/{len(links)} {title}")
            time.sleep(random.uniform(0.6, 1.3))

        except Exception as e:
            print("❌", e)
            time.sleep(5)

    with open(OUTPUT, "a", encoding="utf-8") as f:
        f.write("</body></html>")

if __name__ == "__main__":
    scrape()