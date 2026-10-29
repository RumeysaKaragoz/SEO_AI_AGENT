"""Arayüzden tarama -> öneri üretme -> inceleme akışı, site profili ve WordPress bağlantısı.
Yerel sahte site, sahte model ve sahte WordPress kullanılır; gerçek ağ/model yok."""
import functools
import json
import os
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

from app import create_app
from database import get_connection
from site_profile import get_profile
from testintegration import PAGES, QuietHandler
from testwordpress import FakeWP, PASSWORD, USER, reset


def j(value, reason="Gerekçe."):
    return json.dumps({"value": value, "reason": reason}, ensure_ascii=False)


def fake_chat(model, messages, schema):
    if "alan: title" in messages[0]["content"]:
        return j("Örnek Ürün Sayfası Başlığı")
    return j("Bu sayfadaki ürünleri inceleyin ve size uygun olanı seçerek hemen sipariş verin.")


def serve_site():
    site_dir = tempfile.mkdtemp()
    for name, html in PAGES.items():
        with open(os.path.join(site_dir, name), "w", encoding="utf-8") as f:
            f.write(html)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=site_dir))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def post(app, client, path, **data):
    return client.post(path, data={"csrf_token": app.config["CSRF_TOKEN"], **data})


def finish(client, location, timeout=60):
    """Yönlendirilen iş sayfasının bitmesini bekler; son durumu döndürür."""
    job_id = location.rstrip("/").split("/")[-1]
    end = time.time() + timeout
    while time.time() < end:
        snap = client.get(f"/jobs/{job_id}/status.json").get_json()
        if snap["state"] != "running":
            return snap
        time.sleep(0.1)
    raise AssertionError("iş bitmedi")


def main():
    site, base = serve_site()
    work = tempfile.mkdtemp()
    path = os.path.join(work, "t.db")
    app = create_app(path, chat=fake_chat, reports_dir=os.path.join(work, "reports"))
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    assert "Henüz tarama yok" in html and "bağlı değil" in html
    assert post(app, client, "/crawl", url="", max_pages="5").status_code == 400
    assert post(app, client, "/crawl", url=base, max_pages="abc").status_code == 400
    assert post(app, client, "/crawl", url=base, max_pages="9999").status_code == 400
    assert client.post("/crawl", data={"url": base}).status_code == 400            # token yok
    print("OK  panel; geçersiz tarama formları (boş adres, sayı değil, aralık dışı, token yok) reddedildi")

    r = post(app, client, "/crawl", url=base + "/index.html", max_pages="20", delay="0")
    assert r.status_code == 302 and "/jobs/" in r.headers["Location"]
    snap = finish(client, r.headers["Location"])
    assert snap["state"] == "done" and snap["result"]["run_id"] == 1 and "report" not in snap["result"], snap
    assert any("Taranıyor" in line for line in snap["lines"]), snap["lines"]
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Sonuçları gör" in page and "öneri üret" in page
    print("OK  tarama arka planda çalıştı: günlük satırları, sonuç, sonraki adım bağlantıları")

    report = client.get("/runs/1").get_data(as_text=True)
    assert "a.html" in report and "etiketi bulunamadı" in report and "En sık görülen sorunlar" in report
    only_warn = client.get("/runs/1?severity=warning").get_data(as_text=True)
    assert "a.html" in only_warn and "Uyarı" in only_warn
    assert client.get("/runs/99").status_code == 404
    assert "#1" in client.get("/crawl").get_data(as_text=True)
    print("OK  tarama sonuçları sayfası: özet, en sık sorunlar, önem filtresi; olmayan tarama 404")

    assert post(app, client, "/suggest", run_id="1", limit="0").status_code == 400
    assert post(app, client, "/suggest", run_id="99", limit="3").status_code == 400
    r = post(app, client, "/suggest", run_id="1", limit="10", brand="")
    snap = finish(client, r.headers["Location"])
    assert snap["state"] == "done" and snap["result"]["proposed"] >= 2, snap
    pending = client.get("/pending").get_data(as_text=True)
    assert "Örnek Ürün Sayfası Başlığı" in pending and "Bekleyen öneriler (" in pending
    assert "Önerileri incele" in client.get(r.headers["Location"]).get_data(as_text=True)
    print("OK  öneri üretme arka planda çalıştı (sahte model): öneriler bekleyenlerde")

    # çalışan işi iptal / aynı anda ikinci iş
    slow_app = create_app(os.path.join(work, "slow.db"), chat=fake_chat, reports_dir=work)
    slow_client = slow_app.test_client()
    r = post(slow_app, slow_client, "/crawl", url=base + "/index.html", max_pages="20", delay="2")
    assert r.status_code == 302
    time.sleep(0.5)
    r2 = post(slow_app, slow_client, "/crawl", url=base + "/index.html", max_pages="20", delay="0")
    assert r2.status_code == 409 and "Başka bir iş çalışıyor" in r2.get_data(as_text=True)
    assert post(slow_app, slow_client, r.headers["Location"] + "/cancel").status_code == 302
    assert finish(slow_client, r.headers["Location"])["state"] == "cancelled"
    assert post(slow_app, slow_client, "/jobs/yok/cancel").status_code == 404
    print("OK  aynı anda ikinci iş 409; çalışan tarama iptal edildi")

    c = get_connection(path)
    site_id = c.execute("SELECT id FROM sites").fetchone()[0]
    c.close()
    page = client.get("/profile").get_data(as_text=True)
    assert base in page
    long_title = "Bu örnek başlık elli beş karakter sınırını açıkça aşıyor, tam bir deneme metni"
    r = post(client.application, client, "/profile", site_id=str(site_id), brand_name="  Düşes  Butik ",
             example_titles=f"Şık Kadın Bluz Modelleri\n\n{long_title}",
             example_meta_descriptions="", banned_phrases="outlet\nGarantili\noutlet")
    html = r.get_data(as_text=True)
    assert "Profil kaydedildi." in html and "uyarı" in html and long_title in html
    c = get_connection(path)
    prof = get_profile(c, site_id)
    c.close()
    assert prof["brand_name"] == "Düşes Butik" and prof["banned_phrases"] == ["outlet", "Garantili"]
    assert prof["example_titles"] == ["Şık Kadın Bluz Modelleri", long_title]
    assert client.get("/profile?site_id=999").status_code == 404
    print("OK  site profili: kaydedildi, boşluk/tekrar temizlendi, uzunluk uyarısı gösterildi")

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWP)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    wp_url = f"http://127.0.0.1:{server.server_address[1]}"
    reset()
    bad = post(app, client, "/wordpress/connect", url=wp_url, user=USER, password="yanlis", plugin="yoast")
    assert bad.status_code == 502 and "kimlik doğrulaması" in bad.get_data(as_text=True)
    assert "bağlı değil" in client.get("/").get_data(as_text=True)
    assert post(app, client, "/wordpress/connect", url="localhost", user=USER, password="x", plugin="yoast").status_code == 400
    ok = post(app, client, "/wordpress/connect", url=wp_url, user=USER, password=PASSWORD, plugin="yoast")
    html = ok.get_data(as_text=True)
    assert ok.status_code == 200 and "Bağlandı: Editor" in html and PASSWORD not in html
    assert "REST API" not in html and "açık değil" not in html.replace("bağlı değil", "")
    assert "bağlı</span>" in client.get("/").get_data(as_text=True)
    reset(registered=())
    warn = post(app, client, "/wordpress/connect", url=wp_url, user=USER, password=PASSWORD, plugin="yoast")
    warn_html = warn.get_data(as_text=True)
    assert "SEO alanları REST API" in warn_html and "wp_seo_rest.php" in warn_html   # (kesme işareti HTML'de kaçışlanır)
    assert post(app, client, "/wordpress/disconnect").status_code == 302
    assert "bağlı değil" in client.get("/").get_data(as_text=True)
    print("OK  WordPress bağlantısı arayüzden: yanlış parola reddedildi, bağlandı, parola sayfada görünmüyor, kes")

    site.shutdown()
    server.shutdown()
    print("\nTüm iş akışı testleri geçti.")


if __name__ == "__main__":
    main()
