"""app.py testleri. Flask test istemcisi ve geçici veritabanı; gerçek sunucu/model yok."""
import os
import tempfile

from app import create_app
from database import get_connection


def make_db(rows):
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    app = create_app(path)  # şemayı oluşturur
    c = get_connection(path)
    c.execute("INSERT INTO sites (url) VALUES ('https://s.com')")
    c.execute("INSERT INTO crawl_runs (site_id, status) VALUES (1, 'completed')")
    c.execute("INSERT INTO pages (crawl_run_id, url, status_code) VALUES (1, 'https://s.com/urun/x', 200)")
    for field, old, new, status in rows:
        c.execute("INSERT INTO applied_changes (page_id, change_type, old_value, new_value, status) "
                  "VALUES (1, ?, ?, ?, ?)", (field, old, new, status))
    c.commit()
    c.close()
    return app.test_client()


def main():
    empty = make_db([]).get("/")
    assert empty.status_code == 200 and "Bekleyen öneri yok" in empty.get_data(as_text=True)
    print("OK  boş veritabanı: 'Bekleyen öneri yok' mesajı")

    client = make_db([
        ("title", "Mont", "Kürklü Ekose Mont 9009 Lacivert", "pending"),
        ("meta_description", None, "Kısa", "pending"),
        ("title", "Eski", "Onaylı Başlık Metni Burada", "approved"),
    ])
    html = client.get("/").get_data(as_text=True)
    assert "Bekleyen öneriler (2)" in html, html
    assert "Kürklü Ekose Mont 9009 Lacivert" in html and "Başlık" in html and "Meta description" in html
    assert "Onaylı Başlık Metni Burada" not in html          # yalnızca bekleyenler
    assert "(yok)" in html and "uyarı: 4 karakter" in html   # eski metin yok; kısa meta uyarısı
    print("OK  yalnızca bekleyen öneriler listelendi; alan adı, eski/yeni metin ve uzunluk uyarısı görünüyor")

    xss = make_db([("title", None, "<script>alert(1)</script> Başlık metni", "pending")]).get("/")
    body = xss.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body
    print("OK  tarama/AI kaynaklı HTML metin kaçışlanıyor (XSS yok)")
    print("\nTüm arayüz testleri geçti.")


if __name__ == "__main__":
    main()