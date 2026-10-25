"""app.py testleri. Flask test istemcisi, geçici veritabanı ve sahte model; gerçek sunucu/model yok."""
import json
import os
import tempfile

from app import create_app
from apply_changes import PublishError, Publisher
from database import get_connection


def j(value, reason="Gerekçe."):
    return json.dumps({"value": value, "reason": reason}, ensure_ascii=False)


class FakePublisher(Publisher):
    def __init__(self, fail_first=False):
        self.calls, self.fail_first = [], fail_first

    def apply(self, change):
        self.calls.append(change["id"])
        if self.fail_first and len(self.calls) == 1:
            raise PublishError("WordPress yanıt vermedi")


def make(rows, chat=None, publisher=None):
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    app = create_app(path, brand="Marka", chat=chat or (lambda m, msgs, s: j("İthal Kürklü Ekose Mont 9009")),
                     publisher=publisher)
    c = get_connection(path)
    c.execute("INSERT INTO sites (url) VALUES ('https://s.com')")
    c.execute("INSERT INTO crawl_runs (site_id, status) VALUES (1, 'completed')")
    c.execute("INSERT INTO pages (crawl_run_id, url, status_code, title) VALUES (1, 'https://s.com/urun/x', 200, 'Mont')")
    c.execute("INSERT INTO ai_reports (crawl_run_id, page_id, model, recommendations) VALUES (1, 1, 'm', ?)",
              (json.dumps([{"field": "title", "reason": "AI gerekçesi"}]),))
    for field, old, new, status in rows:
        c.execute("INSERT INTO applied_changes (page_id, ai_report_id, change_type, old_value, new_value, status) "
                  "VALUES (1, 1, ?, ?, ?, ?)", (field, old, new, status))
    c.commit()
    return app, app.test_client(), c


def post(app, client, path, **data):
    return client.post(path, data={"csrf_token": app.config["CSRF_TOKEN"], **data})


def row(c, i=1):
    return c.execute("SELECT status, new_value FROM applied_changes WHERE id = ?", (i,)).fetchone()


PENDING = [("title", "Mont", "Kürklü Ekose Mont 9009 | Marka", "pending")]


def main():
    empty = make([])[1].get("/")
    assert "Bekleyen öneri yok" in empty.get_data(as_text=True)
    app, client, c = make(PENDING + [("meta_description", None, "Kısa", "pending"),
                                      ("title", "Eski", "Onaylı Başlık Metni Burada", "approved")])
    html = client.get("/").get_data(as_text=True)
    assert "Bekleyen öneriler (2)" in html and "Onaylı Başlık" not in html
    assert 'href="/change/1"' in html and "uyarı: 4 karakter" in html
    print("OK  liste: yalnızca bekleyenler, İncele bağlantısı, uzunluk uyarısı")

    page = client.get("/change/1")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Kürklü Ekose Mont 9009 | Marka" in html and "AI gerekçesi" in html
    assert "Mont" in html and app.config["CSRF_TOKEN"] in html
    assert client.get("/change/999").status_code == 404
    print("OK  İncele ekranı: mevcut metin, kayıtlı öneri, gerekçe; olmayan kayıt 404")

    assert client.post("/change/1/approve", data={"final_text": "Başlık Metni Burada"}).status_code == 400
    assert client.post("/change/1/approve", data={"csrf_token": "yanlis", "final_text": "x"}).status_code == 400
    assert row(c)[0] == "pending"
    print("OK  csrf_token olmadan / yanlış tokenla gönderilen istek reddedildi, kayıt değişmedi")

    r = post(app, client, "/change/1/merge", user_text="<b>ithal</b> olsun", final_text="Elle yazılmış metin")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "İthal Kürklü Ekose Mont 9009 | Marka" in html
    assert "<b>ithal</b>" not in html and "&lt;b&gt;ithal&lt;/b&gt;" in html
    assert row(c) == ("pending", "Kürklü Ekose Mont 9009 | Marka")
    print("OK  ortak metin son metin kutusuna geldi, marka eklendi, kullanıcı metni kaçışlandı, veritabanı değişmedi")

    def boom(*a):
        raise RuntimeError("Ollama'ya bağlanılamadı.")
    app2, client2, _ = make(PENDING, chat=boom)
    r = post(app2, client2, "/change/1/merge", user_text="ithal olsun", final_text="Elle yazılmış metin")
    html = r.get_data(as_text=True)
    assert r.status_code == 502 and "Ollama&#39;ya bağlanılamadı" in html and "Elle yazılmış metin" in html
    app3, client3, _ = make(PENDING, chat=lambda m, msgs, s: "json değil")
    html = post(app3, client3, "/change/1/merge", user_text="ithal olsun").get_data(as_text=True)
    assert "geçerli bir ortak metin üretemedi" in html and "deneme 1" in html
    assert post(app, client, "/change/1/merge", user_text="  ").status_code == 400
    print("OK  Ollama hatası / geçersiz model yanıtı / boş istek: açık hata, yazdığın metin korunuyor")

    r = post(app, client, "/change/1/save", final_text="  Taslak   Başlık Metni Burada ")
    html = r.get_data(as_text=True)
    assert "Taslak kaydedildi." in html and row(c) == ("pending", "Taslak Başlık Metni Burada")
    assert "uyarı" in post(app, client, "/change/1/save", final_text="Kısa").get_data(as_text=True)
    assert post(app, client, "/change/1/save", final_text=" ").status_code == 400
    print("OK  taslak kaydı: durum pending kaldı, boşluklar düzeldi, uzunluk uyarısı gösterildi, boş metin reddedildi")

    r = post(app, client, "/change/1/approve", final_text="Kısa")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Yine de onayla" in html and row(c)[0] == "pending"
    r = post(app, client, "/change/1/approve", final_text="Kısa", confirm="1")
    assert r.status_code == 302 and row(c) == ("approved", "Kısa")
    assert post(app, client, "/change/1/save", final_text="Onaylıya yazılamaz").status_code == 400
    assert row(c) == ("approved", "Kısa")
    assert "düzenlenemez" in client.get("/change/1").get_data(as_text=True)
    print("OK  onay: uyarıda önce onay sorusu, sonra approved; onaylı kayıt düzenlenemiyor")

    app4, client4, c4 = make(PENDING)
    r = post(app4, client4, "/change/1/approve", final_text="İthal Kürklü Ekose Mont 9009 | Marka")
    assert r.status_code == 302 and row(c4) == ("approved", "İthal Kürklü Ekose Mont 9009 | Marka")
    app5, client5, c5 = make(PENDING)
    assert post(app5, client5, "/change/1/reject").status_code == 302 and row(c5)[0] == "rejected"
    assert post(app5, client5, "/change/1/reject").status_code == 400
    print("OK  uyarısız onay doğrudan approved; ret rejected; ikinci ret 400")
    app6, client6, c6 = make([("title", "Mont", "Onaylı Başlık Metni Burada", "approved"),
                              ("meta_description", None, "=HYPERLINK(1)", "approved"),
                              ("title", "x", "Bekleyen Başlık Metni Burada", "pending")])
    html = client6.get("/approved").get_data(as_text=True)
    assert "Onaylananlar (2)" in html and "Bekleyen Başlık" not in html and "Onaylı Başlık Metni Burada" in html
    r = client6.get("/approved.csv")
    body = r.get_data()
    assert r.mimetype == "text/csv" and body.startswith(b"\xef\xbb\xbf") and "attachment" in r.headers["Content-Disposition"]
    assert "Onaylı Başlık Metni Burada" in body.decode("utf-8-sig") and "'=HYPERLINK" in body.decode("utf-8-sig")
    print("OK  /approved: yalnızca onaylılar; CSV indirme (BOM, ayraç, formül etkisizleştirme)")

    assert client6.post("/change/1/mark-applied").status_code == 400 and row(c6)[0] == "approved"  # token yok
    assert post(app6, client6, "/change/1/mark-applied").status_code == 302 and row(c6)[0] == "applied"
    assert "Onaylananlar (1)" in client6.get("/approved").get_data(as_text=True)
    assert post(app6, client6, "/change/1/mark-applied").status_code == 400   # zaten uygulandı
    assert post(app6, client6, "/change/3/mark-applied").status_code == 400   # bekleyen kayıt
    assert post(app6, client6, "/change/99/mark-applied").status_code == 404
    print("OK  'Uygulandı olarak işaretle': token zorunlu; applied olunca listeden düştü; geçersiz geçişler reddedildi")
    ROWS = [("title", "Mont", "Onaylı Başlık Metni Burada", "approved"),
            ("title", "x", "Bekleyen Başlık Metni Burada", "pending")]
    app7, client7, c7 = make(ROWS)                       # publisher yok: düğme de yok, rota da yok
    assert 'action="/change/1/apply"' not in client7.get("/approved").get_data(as_text=True)
    assert post(app7, client7, "/change/1/apply").status_code == 404 and row(c7)[0] == "approved"
    pub = FakePublisher(fail_first=True)
    app8, client8, c8 = make(ROWS, publisher=pub)
    assert 'action="/change/1/apply"' in client8.get("/approved").get_data(as_text=True)
    assert client8.post("/change/1/apply").status_code == 400 and pub.calls == []      # token yok
    r = post(app8, client8, "/change/1/apply")                                          # 1. deneme başarısız
    html = r.get_data(as_text=True)
    assert r.status_code == 502 and "WordPress yanıt vermedi" in html and "son deneme başarısız" in html
    assert row(c8)[0] == "failed"
    assert post(app8, client8, "/change/1/apply").status_code == 302 and row(c8)[0] == "applied"  # yeniden deneme
    assert post(app8, client8, "/change/2/apply").status_code == 400 and pub.calls == [1, 1]      # bekleyen: siteye gitmez
    print("OK  siteye uygula: publisher yoksa kapalı; token zorunlu; hata mesajı + yeniden deneme; bekleyen kayıt siteye gitmedi")
    print("\nTüm arayüz testleri geçti.")


if __name__ == "__main__":
    main()