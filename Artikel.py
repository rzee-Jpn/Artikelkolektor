#!/usr/bin/env python3
"""
chord_collector_fast.py
Versi cepat + tahan lama untuk scraping banyak posting (3000+)
- ThreadPoolExecutor untuk paralelisasi
- requests.Session + HTTPAdapter untuk connection pooling
- Checkpointing (results.json + progress.json)
- Membagi output HTML per bagian agar tidak terlalu besar
- Stop / Resume
- Mode: GUI (tkinter) atau headless (CLI) — gunakan --headless di CI/GitHub
"""

import os
import json
import time
import math
import queue
import threading
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

# -------- CONFIG (bisa override via env) ----------
BLOG_URL = os.environ.get("BLOG_URL", "https://indolawas.blogspot.com/")
ITEMS_PER_PAGE = int(os.environ.get("ITEMS_PER_PAGE", "8"))
POSTS_CACHE = os.environ.get("POSTS_CACHE", "posts_cache.json")
RESULTS_FILE = os.environ.get("RESULTS_FILE", "results.json")
PROGRESS_FILE = os.environ.get("PROGRESS_FILE", "progress.json")
PART_PREFIX = os.environ.get("PART_PREFIX", "chord_part")
PART_SIZE = int(os.environ.get("PART_SIZE", "500"))
CHECKPOINT_EVERY = int(os.environ.get("CHECKPOINT_EVERY", "50"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "12"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))
# --------------------------------------------------

# Globals
stop_event = threading.Event()
ui_q = queue.Queue()
file_lock = Lock()

# ----------------- UTIL -----------------
def safe_load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def safe_save_json(path, data):
    tmp = path + ".tmp"
    with file_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

# ----------------- RSS / LIST -----------------
def ambil_daftar_post_from_feed(progress_cb=None):
    # Try load cache first
    if os.path.exists(POSTS_CACHE):
        try:
            with open(POSTS_CACHE, "r", encoding="utf-8") as f:
                posts = json.load(f)
            if progress_cb:
                progress_cb(f"📦 Memuat cache ({len(posts)} posting)")
            return posts
        except Exception:
            pass

    semua_post = []
    start_index = 1
    batch_size = 500
    while True:
        if stop_event.is_set():
            break
        feed_url = f"{BLOG_URL.rstrip('/')}/feeds/posts/default?alt=rss&start-index={start_index}&max-results={batch_size}"
        try:
            r = requests.get(feed_url, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200 or not r.text.strip():
                break
            root = ET.fromstring(r.text)
            items = root.findall(".//item")
            if not items:
                break
            for item in items:
                title_tag = item.find("title")
                link_tag = item.find("link")
                title = title_tag.text.strip() if title_tag is not None else "Tanpa Judul"
                link = link_tag.text.strip() if link_tag is not None else ""
                if link and "/20" in link and not any(l == link for _, l in semua_post):
                    semua_post.append((title, link))
            start_index += batch_size
            time.sleep(0.15)
        except Exception as e:
            print("Error ambil feed:", e)
            break

    # fallback: if nothing found, scrape homepage
    if not semua_post:
        try:
            r = requests.get(BLOG_URL, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for post_tag in soup.find_all(["h3", "h2"], class_=["post-title", "entry-title"]):
                a = post_tag.find("a", href=True)
                if a and "/20" in a["href"]:
                    title = a.get_text(strip=True)
                    link = urljoin(BLOG_URL, a["href"])
                    if not any(l == link for _, l in semua_post):
                        semua_post.append((title, link))
        except Exception as e:
            print("Fallback gagal:", e)

    # save cache
    try:
        with file_lock:
            with open(POSTS_CACHE, "w", encoding="utf-8") as f:
                json.dump(semua_post, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Gagal simpan posts cache:", e)

    if progress_cb:
        progress_cb(f"✅ Ditemukan {len(semua_post)} postingan")
    return semua_post

# ----------------- SCRAPER (parallel) -----------------
def make_session():
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=1)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; MiniatumScraper/1.0)"})
    return s

def fetch_post_content(session, title, link):
    """Return tuple (link, snippet_html)"""
    if stop_event.is_set():
        return link, None
    try:
        r = session.get(link, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        article = (
            soup.find("div", class_="post-body")
            or soup.find("div", class_="entry-content")
            or soup.find("article")
            or soup.find("div", {"id": "post-body"})
        )
        if article:
            isi_html = article.decode_contents()
        else:
            # fallback: whole body
            body = soup.find("body")
            isi_html = body.decode_contents() if body else "(Gagal ambil isi)"
        snippet = f"<div class='post'><h2>{title}</h2>{isi_html}<a class='source' href='{link}' target='_blank'>🔗 Lihat Asli</a></div>\n"
        return link, snippet
    except Exception as e:
        print("Fetch error:", link, e)
        err_snip = f"<div class='post'><h2>{title}</h2><p>(Error ambil isi: {e})</p><a class='source' href='{link}' target='_blank'>🔗 Lihat Asli</a></div>\n"
        return link, err_snip

def scrape_links_parallel(selected_links, ui_queue=None, max_workers=MAX_WORKERS):
    """
    selected_links: list of (title, link)
    writes progress to ui_queue: tuples like ("progress", i, total), ("status", text), ("done", None)
    Returns dict link -> snippet
    """
    results_cache = safe_load_json(RESULTS_FILE, {})
    session = make_session()
    total = len(selected_links)
    if ui_queue:
        ui_queue.put(("progress_max", total))
        ui_queue.put(("status", f"Memulai scraping {total} posting..."))

    # prepare worklist (skip already cached)
    worklist = [(t, l) for t, l in selected_links if l not in results_cache]
    already = total - len(worklist)
    if ui_queue:
        ui_queue.put(("status", f"{already}/{total} sudah ada di cache, mengambil sisa {len(worklist)}..."))
    else:
        print(f"{already}/{total} sudah ada di cache, mengambil sisa {len(worklist)}...")

    if not worklist:
        if ui_queue:
            ui_queue.put(("status", "Semua posting telah di-cache."))
            ui_queue.put(("done", None))
        return results_cache

    # submit tasks
    counter = already
    last_checkpoint = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_map = {ex.submit(fetch_post_content, session, t, l): (t, l) for t, l in worklist}
        for fut in as_completed(future_map):
            if stop_event.is_set():
                if ui_queue:
                    ui_queue.put(("status", "Stop diminta — menyimpan progress..."))
                break
            t, l = future_map[fut]
            try:
                link, snippet = fut.result()
            except Exception as e:
                link, snippet = l, f"<div class='post'><h2>{t}</h2><p>(Error: {e})</p></div>\n"
            if snippet is not None:
                results_cache[link] = snippet
            counter += 1
            if ui_queue:
                ui_queue.put(("progress", counter))
                ui_queue.put(("status", f"{counter}/{total} — {t[:60]}"))
            else:
                print(f"{counter}/{total} — {t[:60]}")

            # checkpoint
            if counter - last_checkpoint >= CHECKPOINT_EVERY:
                safe_save_json(RESULTS_FILE, results_cache)
                prog = safe_load_json(PROGRESS_FILE, {"completed": [], "selected": []})
                prog["completed"] = list(results_cache.keys())
                safe_save_json(PROGRESS_FILE, prog)
                last_checkpoint = counter

    # final save
    safe_save_json(RESULTS_FILE, results_cache)
    prog = safe_load_json(PROGRESS_FILE, {"completed": [], "selected": []})
    prog["completed"] = list(results_cache.keys())
    safe_save_json(PROGRESS_FILE, prog)

    if ui_queue:
        ui_queue.put(("status", "✅ Scraping selesai (cache updated)."))
        ui_queue.put(("done", None))
    else:
        print("✅ Scraping selesai (cache updated).")
    return results_cache

# ----------------- BUILD HTML PARTS -----------------
HTML_HEAD = """<!DOCTYPE html>
<html lang="id">
<head><meta charset="utf-8"><title>Chord Terpilih</title>
<style>
body{font-family:'Segoe UI',sans-serif;background:#f7f9ff;color:#222;max-width:90%;margin:auto;padding:15px;}
h2{border-left:4px solid #7aa5ff;padding-left:8px;}
.post{margin-bottom:18px;background:white;border-radius:10px;padding:14px;box-shadow:0 3px 8px rgba(0,0,0,0.06);}
a.source{display:inline-block;margin-top:8px;color:#2a5dff;text-decoration:none;}
</style></head><body><h1>🎸 Chord Terpilih</h1>
"""
HTML_TAIL = "</body></html>"

def build_html_parts(selected_links_ordered, results_cache, part_size=PART_SIZE, prefix=PART_PREFIX, out_dir="parts"):
    os.makedirs(out_dir, exist_ok=True)
    # group links in order into parts
    for i in range(0, len(selected_links_ordered), part_size):
        part_links = selected_links_ordered[i:i+part_size]
        part_index = i // part_size + 1
        path = os.path.join(out_dir, f"{prefix}{part_index}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(HTML_HEAD)
            for link in part_links:
                snippet = results_cache.get(link)
                if snippet:
                    f.write(snippet)
                else:
                    f.write(f"<div class='post'><h2>(Belum diambil)</h2><p>Link: <a href='{link}'>{link}</a></p></div>\n")
            f.write(HTML_TAIL)
    return True

# ----------------- Optional GUI APP (only if not headless) -----------------
def try_start_gui():
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except Exception:
        print("Tkinter tidak tersedia — melewati GUI.")
        return False

    class App:
        def __init__(self, root):
            self.root = root
            root.title("Miniatum Chord Collector — Fast")
            root.geometry("520x720")
            root.configure(bg="#f1f5fb")

            tk.Label(root, text="🎵 Miniatum Chord Collector — Fast", font=("Segoe UI", 14, "bold"), bg="#f1f5fb").pack(pady=8)

            # search
            self.search_var = tk.StringVar()
            self.search_entry = tk.Entry(root, textvariable=self.search_var, font=("Segoe UI", 10), width=48, bg="#fff", relief="flat")
            self.search_entry.insert(0, "Cari judul...")
            self.search_entry.pack(padx=10, ipady=6, fill="x")
            self.search_var.trace("w", self.on_search)

            # list frame
            frame = tk.Frame(root, bg="#f1f5fb")
            frame.pack(fill="both", expand=True, padx=10, pady=(6, 100))
            canvas = tk.Canvas(frame, bg="#ffffff", highlightthickness=0)
            scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
            self.list_frame = tk.Frame(canvas, bg="#ffffff")
            canvas.create_window((0,0), window=self.list_frame, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            self.list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

            # progress & buttons
            self.progress_label = tk.Label(root, text="", bg="#f1f5fb", font=("Segoe UI", 9))
            self.progress_label.pack(pady=4)
            self.progress_bar = ttk.Progressbar(root, orient="horizontal", mode="determinate", length=460)
            self.progress_bar.pack(pady=(0,6))

            bottom = tk.Frame(root, bg="#f1f5fb")
            bottom.pack(side="bottom", fill="x", pady=8)
            btn_frame = tk.Frame(bottom, bg="#f1f5fb")
            btn_frame.pack()

            tk.Button(btn_frame, text="Select All", command=self.select_all, bg="#7ed957", fg="white", width=12).pack(side="left", padx=6)
            tk.Button(btn_frame, text="Unselect All", command=self.unselect_all, bg="#ff7b7b", fg="white", width=12).pack(side="left", padx=6)
            tk.Button(bottom, text="📥 Ambil Chord Terpilih", command=self.start_scrape, bg="#5b8cff", fg="white", font=("Segoe UI", 11, "bold"), width=36, height=2).pack(pady=6)
            tk.Button(bottom, text="⏹️ Stop", command=self.request_stop, bg="#ffa500", fg="white", width=36).pack(pady=3)

            nav = tk.Frame(bottom, bg="#f1f5fb"); nav.pack()
            tk.Button(nav, text="⬅️", command=self.prev_page, bg="#e3e9ff", relief="flat").pack(side="left", padx=8)
            self.halaman_label = tk.Label(nav, text="Halaman 1", bg="#f1f5fb")
            self.halaman_label.pack(side="left", padx=8)
            tk.Button(nav, text="➡️", command=self.next_page, bg="#e3e9ff", relief="flat").pack(side="left", padx=8)

            # state
            self.posts = []            # list of (title, link)
            self.checkbox_vars = []    # list of (title, link, tk.BooleanVar)
            self.filtered = []
            self.current_page = tk.IntVar(value=1)
            self.scrape_thread = None

            # load posts (from feed/cache)
            self.progress_label.config(text="🔍 Mengambil daftar posting...")
            self.root.update()
            self.posts = ambil_daftar_post_from_feed(self.enqueue_status)
            for title, link in self.posts:
                var = tk.BooleanVar(value=False)
                self.checkbox_vars.append((title, link, var))
            self.filtered = list(self.checkbox_vars)
            self.tampilkan_halaman()

            # load progress/results if any
            self.results_cache = safe_load_json(RESULTS_FILE, {})
            self.progress = safe_load_json(PROGRESS_FILE, {"completed": [], "selected": []})
            self.update_progress_ui()

            # start UI updater loop
            self.root.after(150, self.process_ui_queue)

            # on close
            root.protocol("WM_DELETE_WINDOW", self.on_close)

        # UI helpers
        def enqueue_status(self, text):
            ui_q.put(("status", text))

        def process_ui_queue(self):
            try:
                while True:
                    typ, payload = ui_q.get_nowait()
                    if typ == "status":
                        self.progress_label.config(text=payload)
                    elif typ == "progress_max":
                        self.progress_bar["maximum"] = payload
                    elif typ == "progress":
                        self.progress_bar["value"] = payload
                    elif typ == "done":
                        messagebox.showinfo("Selesai", "✅ Scraping selesai atau dihentikan. Hasil di folder 'parts' dan cache.")
                    elif typ == "log":
                        print(payload)
            except queue.Empty:
                pass
            self.root.after(150, self.process_ui_queue)

        def tampilkan_halaman(self):
            for w in list(self.list_frame.winfo_children()):
                w.destroy()
            start = (self.current_page.get() - 1) * ITEMS_PER_PAGE
            end = start + ITEMS_PER_PAGE
            self.filtered = [(t,l,v) for (t,l,v) in self.checkbox_vars if self.search_var.get().strip().lower() in (t.lower())] if self.search_var.get().strip() and self.search_var.get().strip()!="Cari judul..." else list(self.checkbox_vars)
            data = self.filtered[start:end]
            for title, link, var in data:
                cb = tk.Checkbutton(self.list_frame, text=title, variable=var, anchor="w", bg="#fff", wraplength=420, justify="left")
                cb.pack(fill="x", padx=6, pady=2)
            total = max(1, math.ceil(len(self.filtered)/ITEMS_PER_PAGE))
            self.halaman_label.config(text=f"Halaman {self.current_page.get()} / {total}")

        def on_search(self, *args):
            self.current_page.set(1)
            self.tampilkan_halaman()

        def prev_page(self):
            if self.current_page.get()>1:
                self.current_page.set(self.current_page.get()-1)
                self.tampilkan_halaman()

        def next_page(self):
            if self.current_page.get() < math.ceil(len(self.filtered)/ITEMS_PER_PAGE):
                self.current_page.set(self.current_page.get()+1)
                self.tampilkan_halaman()

        def select_all(self):
            for _,_,v in self.filtered:
                v.set(True)

        def unselect_all(self):
            for _,_,v in self.filtered:
                v.set(False)

        # scraping control
        def start_scrape(self):
            # collect selected in displayed order
            terpilih = [(t,l) for t,l,v in self.checkbox_vars if v.get()]
            if not terpilih:
                messagebox.showwarning("Peringatan", "Pilih minimal satu postingan dulu.")
                return
            # save selected to progress
            self.progress = safe_load_json(PROGRESS_FILE, {"completed": [], "selected": []})
            self.progress["selected"] = [l for _,l in terpilih]
            safe_save_json(PROGRESS_FILE, self.progress)
            # reset stop flag
            stop_event.clear()
            # start background thread to manage parallel scraping
            if self.scrape_thread and self.scrape_thread.is_alive():
                messagebox.showinfo("Info", "Proses sudah berjalan.")
                return
            self.scrape_thread = threading.Thread(target=self._scrape_worker, args=(terpilih,), daemon=True)
            self.scrape_thread.start()

        def _scrape_worker(self, terpilih):
            ui_q.put(("status", "Menjalankan scraping..."))
            results = scrape_links_parallel(terpilih, ui_q, max_workers=MAX_WORKERS)
            selected_order = [l for _,l in terpilih]
            build_html_parts(selected_order, results)
            ui_q.put(("status", "🏁 Semua part HTML telah dibuat di folder 'parts'"))
            ui_q.put(("done", None))

        def request_stop(self):
            stop_event.set()
            self.progress_label.config(text="⏸️ Stop diminta — menyimpan progress...")

        def update_progress_ui(self):
            self.progress = safe_load_json(PROGRESS_FILE, {"completed": [], "selected": []})
            completed = len(self.progress.get("completed", []))
            selected = len(self.progress.get("selected", []))
            if selected:
                self.progress_label.config(text=f"🔁 Session tersimpan — {completed}/{selected} selesai. Tekan Ambil untuk lanjut.")
                self.progress_bar["maximum"] = selected
                self.progress_bar["value"] = completed
            else:
                self.progress_label.config(text=f"✅ Ditemukan {len(self.posts)} postingan")

        def on_close(self):
            if messagebox.askokcancel("Keluar", "Keluar dan menyimpan progress?"):
                stop_event.set()
                time.sleep(0.3)
                # ensure checkpoint saved
                safe_save_json(RESULTS_FILE, safe_load_json(RESULTS_FILE, {}))
                safe_save_json(PROGRESS_FILE, safe_load_json(PROGRESS_FILE, {}))
                self.root.destroy()

    root = tk.Tk()
    app = App(root)
    root.mainloop()
    return True

# ----------------- CLI / Headless runner -----------------
def cli_run(headless=True, select_all=True, selected_links_env=None):
    """
    headless: boolean (if False, attempt GUI)
    select_all: if True select all posts found
    selected_links_env: comma-separated list of links (overrides select_all)
    """
    if not headless:
        # try GUI
        started = try_start_gui()
        if started:
            return

    # headless flow
    print("Mode headless: mengambil daftar posting...")
    posts = ambil_daftar_post_from_feed(lambda s: print("STATUS:", s))
    if not posts:
        print("Tidak ada posting ditemukan.")
        return

    if selected_links_env:
        # user provides explicit list of links (comma sep)
        selected_links = []
        wanted = [x.strip() for x in selected_links_env.split(",") if x.strip()]
        for t,l in posts:
            if l in wanted:
                selected_links.append((t,l))
        if not selected_links:
            print("Link yang diminta tidak ditemukan di daftar. Membatalkan.")
            return
    elif select_all:
        selected_links = posts
    else:
        # if not select_all and no env, pick first N (safe default)
        N = min(50, len(posts))
        print(f"select_all=False -> memilih {N} posting pertama sebagai contoh")
        selected_links = posts[:N]

    # store selected to progress
    prog = safe_load_json(PROGRESS_FILE, {"completed": [], "selected": []})
    prog["selected"] = [l for _,l in selected_links]
    safe_save_json(PROGRESS_FILE, prog)

    # reset stop flag and run scrape
    stop_event.clear()
    results = scrape_links_parallel(selected_links, ui_queue=None, max_workers=MAX_WORKERS)
    selected_order = [l for _,l in selected_links]
    build_html_parts(selected_order, results)
    print("Selesai. Part HTML disimpan di ./parts")

# ----------------- ENTRYPOINT -----------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Miniatum Chord Collector — Fast (GUI + Headless)")
    parser.add_argument("--headless", action="store_true", help="Jalankan tanpa GUI (untuk CI / GitHub Actions).")
    parser.add_argument("--no-select-all", dest="select_all", action="store_false", help="Jangan pilih semua post secara otomatis (headless).")
    parser.add_argument("--selected-links", dest="selected_links", help="Daftar link dipisah koma; jika diisi akan override select_all.")
    args = parser.parse_args()

    # If running in headless mode or no DISPLAY, use CLI
    if args.headless or os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
        cli_run(headless=True, select_all=args.select_all, selected_links_env=args.selected_links)
    else:
        # try GUI, fallback to headless if GUI not possible
        try:
            cli_run(headless=False, select_all=args.select_all, selected_links_env=args.selected_links)
        except Exception as e:
            print("GUI gagal, fallback headless:", e)
            cli_run(headless=True, select_all=args.select_all, selected_links_env=args.selected_links)

if __name__ == "__main__":
    main()
