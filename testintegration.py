"""Yerel bir test sitesiyle uçtan uca test: crawler -> analiz -> veritabanı + TXT."""
import functools
import os
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from database import get_connection
from main import run

PAGES = {
    "index.html": """<html><head><title>Ana sayfa başlığı örnek</title>
        <meta name="description" content="Bu açıklama elli karakterden uzun olacak şekilde yazıldı, tamam."></head>
        <body><h1>Merhaba</h1><p>Hoş geldiniz.</p>
        <a href="/a.html">A</a> <a href="/b.html">B</a> <a href="/yok.html">Yok</a>
        <a href="http://baska-site.example/x">Dış</a> <a href="mailto:a@b.c">Mail</a>
        <a href="#top">Üst</a> <a href="#">Menü</a> <a href="/logo.png">Logo</a> <a href="/c.html">C</a></body></html>""",
    "a.html": """<html><head></head><body><h1>Bir</h1><h1>İki</h1>
        <img src="x.jpg"><img src="y.jpg" alt="var"><img src="z.jpg" alt=""><noscript><img src="https://fb.example/tr?id=1&noscript=1" width="1" height="1"></noscript><a href="/index.html">Ana</a></body></html>""",
    "b.html": """<html><head><title>B sayfası, yeterince uzun</title></head>
        <body><h1>B</h1><p>Bu sayfanın metni en az elli karakter olmalı ki içerik karşılaştırılsın.</p>
        <a href="/a.html">A</a><a href="/index.html">tekrar</a></body></html>""",
}
PAGES["c.html"] = PAGES["b.html"]  # b.html'in birebir kopyası: tekrar eden başlık ve içerik


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    site_dir = tempfile.mkdtemp()
    for name, html in PAGES.items():
        with open(os.path.join(site_dir, name), "w", encoding="utf-8") as f:
            f.write(html)
    handler = functools.partial(QuietHandler, directory=site_dir)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    work = tempfile.mkdtemp()
    db_path = os.path.join(work, "test.db")
    reports_dir = os.path.join(work, "reports")

    summary = run(base + "/index.html", db_path=db_path, reports_dir=reports_dir, delay=0)

    conn = get_connection(db_path)
    site = conn.execute("SELECT url FROM sites").fetchall()
    assert site == [(base,)], site
    print("OK  site kaydı (origin) oluştu")

    run_row = conn.execute("SELECT status, pages_crawled, report_path, ended_at FROM crawl_runs").fetchone()
    assert run_row[0] == "completed" and run_row[1] == 4 and run_row[3] is not None, run_row
    assert os.path.exists(run_row[2])
    print("OK  crawl_run: completed, 4 sayfa, bitiş zamanı ve rapor yolu dolu")

    pages = {u: (s, e) for u, s, e in conn.execute("SELECT url, status_code, error_message FROM pages")}
    assert len(pages) == 5, pages
    assert pages[base + "/yok.html"] == (404, "HTTP 404")
    print("OK  5 sayfa kaydı (404'lü sayfa durum ve hata mesajıyla), dış/mailto/png eklenmedi")

    a_issues = {c for (c,) in conn.execute(
        "SELECT i.code FROM issues i JOIN pages p ON p.id = i.page_id WHERE p.url = ?",
        (base + "/a.html",))}
    assert {"TITLE_MISSING", "META_DESC_MISSING", "H1_MULTIPLE", "IMG_ALT_MISSING"} <= a_issues, a_issues
    print("OK  a.html için beklenen sorunlar issues tablosunda:", sorted(a_issues))

    # alt="" hata sayılmaz, yalnızca alt'ı hiç olmayan görsel sayılır
    alt_count = conn.execute(
        "SELECT COUNT(*) FROM issues i JOIN pages p ON p.id = i.page_id "
        "WHERE p.url = ? AND i.code = 'IMG_ALT_MISSING'", (base + "/a.html",)).fetchone()[0]
    assert alt_count == 1, alt_count
    print('OK  alt="" hata sayılmadı, yalnızca alt\'ı olmayan görsel işaretlendi')

    def codes_of(path):
        return {c for (c,) in conn.execute(
            "SELECT i.code FROM issues i JOIN pages p ON p.id = i.page_id WHERE p.url = ?",
            (base + path,))}

    idx = codes_of("/index.html")
    assert "LINK_NOT_CRAWLABLE" in idx and "LINK_HREF_INVALID" not in idx, idx
    print('OK  href="#" işaretlendi, "#top" gibi sayfa içi bağlantılar işaretlenmedi')

    assert {"ROBOTS_TXT_MISSING", "SITEMAP_MISSING"} <= idx, idx
    for other in ("/a.html", "/b.html", "/c.html"):
        assert not ({"ROBOTS_TXT_MISSING", "SITEMAP_MISSING"} & codes_of(other))
    print("OK  robots.txt / sitemap bulguları yalnızca başlangıç sayfasına bağlandı")

    for path in ("/b.html", "/c.html"):
        assert {"DUPLICATE_TITLE", "DUPLICATE_CONTENT"} <= codes_of(path), path
    assert not ({"DUPLICATE_TITLE", "DUPLICATE_CONTENT"} & codes_of("/a.html"))
    print("OK  birebir aynı iki sayfa tekrar eden başlık/içerik olarak işaretlendi")

    assert {"CANONICAL_MISSING", "OG_MISSING", "TWITTER_CARD_MISSING", "SCHEMA_MISSING"} <= codes_of("/b.html")
    print("OK  canonical / Open Graph / Twitter / schema kuralları devrede")

    row = conn.execute(
        "SELECT title, h1_count, image_count, images_missing_alt, content_text FROM pages WHERE url = ?",
        (base + "/a.html",)).fetchone()
    assert row[1] == 2 and row[2] == 3 and row[3] == 1 and "Bir" in row[4], row
    print("OK  ham sayfa verisi (h1_count, görsel sayıları, content_text) doğru")

    import json
    extra = conn.execute("SELECT extra_data FROM pages WHERE url = ?", (base + "/b.html",)).fetchone()[0]
    assert json.loads(extra)["content_hash"], extra
    print("OK  content_hash pages.extra_data içinde saklanıyor")

    orphan = conn.execute(
        "SELECT COUNT(*) FROM issues WHERE page_id NOT IN (SELECT id FROM pages)").fetchone()[0]
    assert orphan == 0

    report = open(run_row[2], encoding="utf-8").read()
    assert "Erişilemeyen sayfa: 1" in report and "Erişilemedi: HTTP 404" in report
    assert "Kritik:" in report and "Uyarı:" in report and "Bilgi:" in report
    assert "[UYARI]" in report and "[KRİTİK]" in report
    total_db = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    assert f"Toplam sorun: {total_db}" in report
    print("OK  TXT raporu veritabanıyla aynı sayıları gösteriyor")

    # İkinci tarama ayrı bir crawl_run olarak, aynı site altında saklanır
    run(base + "/index.html", db_path=db_path, reports_dir=reports_dir, delay=0)
    assert conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 10
    print("OK  ikinci tarama: 1 site, 2 tarama, geçmiş korunuyor")

    conn.close()
    server.shutdown()
    print("\nTüm entegrasyon testleri geçti.")


if __name__ == "__main__":
    main()