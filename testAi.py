"""ai.py ve review.py testleri. Gerçek model YOK: sahte yanıtlar ve sahte Ollama sunucusu kullanılır."""
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ai import generate_suggestions, ollama_chat, tr_lower, validate, SCHEMA
from database import get_connection, init_db
from review import approve, length_warning, list_pending, reject

H1 = "İTHAL KÜRKLÜ EKOSE TASARIM MONT 9009 STD - Lacivert"
MENU = "Koleksiyon Üst Giyim Bluz Triko Alt Giyim Pantolon Dış Giyim Mont-Kaban " * 3
FACTS = f"İTHAL KÜRKLÜ EKOSE TASARIM MONT 9009 {H1} ithal kürklü ekose tasarım mont 9009 https://s.com/urun/x"


def build_db():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(path)
    c = get_connection(path)
    c.execute("INSERT INTO sites (url) VALUES ('https://s.com')")
    c.execute("INSERT INTO crawl_runs (site_id, status) VALUES (1, 'completed')")
    # sayfa 1: ürün — başlık kısa + meta kısa
    c.execute("INSERT INTO pages (crawl_run_id, url, status_code, title, meta_description, h1_text, content_text) "
              "VALUES (1, 'https://s.com/urun/x', 200, 'Mont', 'ithal kürklü ekose tasarım mont 9009', ?, ?)",
              (H1, MENU + H1 + " Kategori Mont-Kaban Renk Lacivert Beden STD Fiyat 8.390,00 TL"))
    # sayfa 2: kategori — H1 yok, meta yok
    c.execute("INSERT INTO pages (crawl_run_id, url, status_code, title, h1_text, content_text) "
              "VALUES (1, 'https://s.com/kategori/triko', 200, 'Triko', NULL, ?)", (MENU,))
    # sayfa 3: sorunu AI ile ilgisiz (görsel) — hiç işlenmemeli
    c.execute("INSERT INTO pages (crawl_run_id, url, status_code, title) VALUES (1, 'https://s.com/a', 200, 'x')")
    # sayfa 4: üye sayfası (atlanmalı), sayfa 5: ana sayfa yalnızca tekrar eden başlık yüzünden işaretli (atlanmalı)
    c.execute("INSERT INTO pages (crawl_run_id, url, status_code, title) VALUES (1, 'https://s.com/hesabim/favorilerim', 200, 'Ab')")
    c.execute("INSERT INTO pages (crawl_run_id, url, status_code, title) VALUES (1, 'https://s.com', 200, 'Ana Sayfa Başlığı Uygun')")
    c.execute("INSERT INTO issues (page_id, code, category, severity, message) VALUES (4, 'TITLE_TOO_SHORT', 'on-page', 'warning', 'kısa')")
    c.execute("INSERT INTO issues (page_id, code, category, severity, message) VALUES (5, 'DUPLICATE_TITLE', 'content', 'warning', 'aynı başlık')")
    for pid, code, msg in [
        (1, "TITLE_TOO_SHORT", "Başlık kısa"), (1, "META_DESC_TOO_SHORT", "Meta kısa"),
        (2, "META_DESC_MISSING", "Meta yok"), (3, "IMG_ALT_MISSING", "alt yok"),
    ]:
        sev = "warning"
        c.execute("INSERT INTO issues (page_id, code, category, severity, message) VALUES (?, ?, 'on-page', ?, ?)",
                  (pid, code, sev, msg))
    c.commit()
    c.close()
    return path


class FakeModel:
    """Alan adına göre senaryolu sahte model. calls: (alan, deneme) kaydı."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def __call__(self, model, messages, schema):
        system = messages[0]["content"]
        field = "title" if "alan: title" in system else "meta_description"
        user = messages[-1]["content"]
        page = "urun" if "/urun/" in user else "kategori"
        key = (page, field)
        n = sum(1 for c in self.calls if c[:2] == key)
        self.calls.append((page, field, n, user))
        answers = self.script[key]
        return answers[min(n, len(answers) - 1)]


def j(value, reason="Gerekçe."):
    return json.dumps({"value": value, "reason": reason}, ensure_ascii=False)


def test_validate():
    ok = lambda f, v, old=None, facts=FACTS, **kw: validate(f, v, old, facts, **kw)
    assert ok("title", "İthal Kürklü Ekose Tasarım Mont 9009")[1] is None
    assert "kısa" in ok("title", "Mont")[1]
    assert "uzun" in ok("title", "A" * 60 + "b")[1]
    assert "HTML" in ok("title", "<b>İthal Kürklü Mont</b> 9009")[1]
    assert "aynı" in ok("title", "ithal kürklü ekose tasarım mont 9009", old="İTHAL KÜRKLÜ EKOSE TASARIM MONT 9009")[1]
    assert "büyük harf" in ok("title", "İTHAL KÜRKLÜ EKOSE MONT 9009")[1]
    assert "sayı" in ok("meta_description", "İthal kürklü ekose tasarım mont 9009 şimdi sadece 7990 TL ile sizlerle, hemen inceleyin.")[1]
    assert "'indirim'" in ok("meta_description", "İthal kürklü ekose tasarım mont 9009 modelinde büyük indirim fırsatını kaçırmayın hemen.")[1]
    assert ok("meta_description", "İthal kürklü ekose tasarım mont 9009 modelini lacivert renkte inceleyin ve hemen sipariş verin.")[1] is None
    assert ok("title", "İthal Mont 9009", max_len=12)[1] and "uzun" in ok("title", "İthal Mont 9009", max_len=12)[1]
    # Türkçe büyük/küçük harf: "İNDİRİM" sayfada geçiyorsa yasaklı kelime sayılmaz
    assert validate("title", "İndirimli Ekose Mont 9009", None, FACTS + " İNDİRİM")[1] is None
    assert tr_lower("İNDİRİM ISPARTA") == "indirim ısparta"
    print("OK  doğrulama: uzunluk, HTML, aynı metin, büyük harf, uydurma sayı/vaat, Türkçe küçük harf")


def test_pipeline():
    path = build_db()
    model = FakeModel({
        # 1. deneme marka ekiyle birlikte sınırı aşar (45 > 41), 2. deneme sığar
        ("urun", "title"): [j("İthal Kürklü Ekose Tasarım Mont 9009 Lacivert"),
                            j("Kürklü Ekose Mont 9009 Lacivert", "Renk ve model eklendi.")],
        # 1. deneme uydurma fiyat/indirim, 2. deneme düzgün
        ("urun", "meta_description"): [
            j("İthal kürklü ekose tasarım mont 9009 şimdi 7990 TL, büyük indirim fırsatı seni bekliyor."),
            j("İthal kürklü ekose tasarım mont 9009 modelini lacivert renkte inceleyin, beden seçenekleriyle sipariş verin.",
              "Ürün adı ve rengi kullanıldı."),
        ],
        # kategori meta: model iki denemede de çok kısa yazıyor -> öneri yok
        ("kategori", "meta_description"): [j("Triko modelleri"), j("Triko kazaklar")],
    })
    summary = generate_suggestions(path, limit=5, model="fake", brand="Düşes Butik", chat=model)
    assert summary == {"run_id": 1, "pages": 2, "proposed": 2, "failed": 1}, summary
    print("OK  özet: 2 sayfa, 2 öneri, 1 başarısız; AI ile ilgisiz sayfa işlenmedi")

    c = get_connection(path)
    assert c.execute("SELECT COUNT(*) FROM applied_changes WHERE page_id IN (4, 5)").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM ai_reports WHERE page_id IN (4, 5)").fetchone()[0] == 0
    print("OK  üye sayfası ve yalnızca 'tekrar' yüzünden işaretli ana sayfa AI'a verilmedi")
    rows = c.execute("SELECT page_id, change_type, old_value, new_value, status, issue_id, ai_report_id "
                     "FROM applied_changes ORDER BY id").fetchall()
    assert len(rows) == 2 and all(r[4] == "pending" for r in rows), rows
    title = [r for r in rows if r[1] == "title"][0]
    assert title[3] == "Kürklü Ekose Mont 9009 Lacivert | Düşes Butik" and len(title[3]) <= 55, title
    assert title[2] == "Mont" and title[5] is not None and title[6] is not None
    meta = [r for r in rows if r[1] == "meta_description"][0]
    assert "indirim" not in meta[3] and "7990" not in meta[3]
    print("OK  öneriler pending yazıldı; marka eklendi ve toplam ≤55; fazla uzun/uydurma öneriler 2. denemede düzeldi")

    assert c.execute("SELECT COUNT(*) FROM applied_changes WHERE page_id = 2").fetchone()[0] == 0
    report2 = c.execute("SELECT report_content FROM ai_reports WHERE page_id = 2").fetchone()[0]
    assert "geçerli öneri üretilemedi" in report2 and "çok kısa" in report2, report2
    assert c.execute("SELECT COUNT(*) FROM ai_reports").fetchone()[0] == 2
    print("OK  başarısız alan için değişiklik yazılmadı, nedenler ai_reports'ta kayıtlı")

    # İstem içeriği: menü kırpılmış mı, kaynak bilgiler var mı
    first_user = model.calls[0][3]
    assert H1 in first_user and "Fiyat 8.390,00 TL" in first_user and "Koleksiyon Üst Giyim Bluz" not in first_user
    cat_user = [c_[3] for c_ in model.calls if c_[0] == "kategori"][0]
    assert "güvenilir kesit yok" in cat_user
    retry_user = [c_[3] for c_ in model.calls if c_[:3] == ("urun", "meta_description", 1)][0]
    assert "Önceki öneri reddedildi" in retry_user and "sayı" in retry_user
    print("OK  istem: menü kırpıldı, H1 yoksa kesit verilmedi, ret nedeni yeniden denemede modele iletildi")

    # İstem yapısı: örnek konuşma var, istemde tamamen büyük harfli kelime yok (model bu tonu taklit ediyor)
    import re
    from ai import build_messages
    page = {"id": 1, "url": "https://s.com/urun/x", "title": "Mont", "meta_description": None,
            "h1_text": None, "content_text": ""}
    for field in ("title", "meta_description"):
        msgs = build_messages(field, page, [{"message": "kısa"}], 41, " | Marka",
                              previous_error="çok fazla büyük harf kullanılmış; yalnızca cümle başları büyük olmalı")
        assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"], msgs
        # Talimatlar (system), örnek cevap (assistant) ve ret mesajı büyük harfli kelime içermemeli.
        # Örnek GİRDİ büyük harfli olabilir: model onu normal yazıma çevirmeyi öğrenir.
        checked = msgs[0]["content"] + " " + msgs[2]["content"] + " " + msgs[3]["content"].split("Önceki")[-1]
        shouting = set(re.findall(r"\b[A-ZÇĞİÖŞÜ]{5,}\b", checked))
        assert not shouting, shouting
        example = json.loads(msgs[2]["content"])
        assert not example["value"].isupper() and example["value"] != example["value"].upper()
    print("OK  istem: örnek konuşma var, talimatlarda 'bağıran' büyük harfli kelime yok")

    # Tekrar çalıştırma: onaylı/bekleyen alanlar yeniden üretilmez
    before = c.execute("SELECT COUNT(*) FROM applied_changes").fetchone()[0]
    model2 = FakeModel({("kategori", "meta_description"): [j("Triko modelleri")]})
    generate_suggestions(path, limit=5, model="fake", brand="Düşes Butik", chat=model2)
    assert c.execute("SELECT COUNT(*) FROM applied_changes").fetchone()[0] == before
    assert all(call[0] == "kategori" for call in model2.calls), "ürün sayfası yeniden işlendi"
    print("OK  yeniden çalıştırmada mevcut öneriler tekrar üretilmedi")

    # Geçersiz JSON yanıtı çökertmez
    path2 = build_db()
    bad = FakeModel({("urun", "title"): ["bu json değil"], ("urun", "meta_description"): ["{}"],
                     ("kategori", "meta_description"): ["{}"]})
    s = generate_suggestions(path2, limit=5, model="fake", chat=bad)
    assert s["proposed"] == 0 and s["failed"] == 3, s
    print("OK  geçersiz/boş model yanıtı hata vermeden 'başarısız' sayıldı")

    # limit
    path3 = build_db()
    s = generate_suggestions(path3, limit=1, model="fake", chat=FakeModel({
        ("urun", "title"): [j("İthal Kürklü Ekose Tasarım Mont 9009 Lacivert")],
        ("urun", "meta_description"): [j("İthal kürklü ekose tasarım mont 9009 modelini lacivert renkte inceleyin, hemen sipariş verin.")]}))
    assert s["pages"] == 1
    print("OK  --limit uygulanıyor")
    c.close()
    return path


def test_review(path):
    c = get_connection(path)
    pending = list_pending(c)
    assert len(pending) == 2 and pending[0]["reason"], pending
    title_id = [p for p in pending if p["field"] == "title"][0]["id"]
    meta_id = [p for p in pending if p["field"] == "meta_description"][0]["id"]

    approve(c, title_id)
    approve(c, meta_id, "Kendi yazdığım meta description metni burada, elli karakterden uzun.")
    st = dict(c.execute("SELECT id, status FROM applied_changes").fetchall())
    assert st[title_id] == st[meta_id] == "approved"
    assert c.execute("SELECT new_value FROM applied_changes WHERE id = ?", (meta_id,)).fetchone()[0].startswith("Kendi yazdığım")
    assert list_pending(c) == []
    print("OK  onay ve kullanıcının kendi metniyle onay çalışıyor; bekleyen listesi boşaldı")

    for fn in (lambda: approve(c, title_id), lambda: reject(c, title_id), lambda: approve(c, 9999)):
        try:
            fn()
        except ValueError:
            pass
        else:
            raise AssertionError("geçersiz geçiş kabul edildi")
    assert c.execute("SELECT status FROM applied_changes WHERE id = ?", (title_id,)).fetchone()[0] == "approved"
    print("OK  onaylı/olmayan kayıt tekrar onaylanamaz veya reddedilemez")

    assert length_warning("title", "Kısa") and not length_warning("title", "Yeterince uzun bir başlık")
    c.close()


class FakeOllama(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.seen.append((self.path, body))
        if body["model"] == "yok-model":
            status, out = 404, {"error": "model 'yok-model' not found"}
        else:
            status, out = 200, {"message": {"role": "assistant", "content": j("Test başlığı yeterince uzun")}}
        data = json.dumps(out).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_ollama_http():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/api/chat"

    out = ollama_chat("qwen2.5:7b", [{"role": "user", "content": "x"}], SCHEMA, url=url)
    assert json.loads(out)["value"] == "Test başlığı yeterince uzun"
    path, body = FakeOllama.seen[0]
    assert path == "/api/chat" and body["model"] == "qwen2.5:7b" and body["stream"] is False
    assert body["format"] == SCHEMA and body["options"]["num_ctx"] == 4096
    print("OK  Ollama isteği: doğru adres, model, stream=false, JSON şeması (format)")

    try:
        ollama_chat("yok-model", [], SCHEMA, url=url)
    except RuntimeError as e:
        assert "ollama pull yok-model" in str(e), e
    else:
        raise AssertionError("404 hata vermedi")
    print("OK  kurulu olmayan model: 'ollama pull' yönlendirmeli açık hata")
    server.shutdown()
    server.server_close()

    try:
        ollama_chat("qwen2.5:7b", [], SCHEMA, url=url)
    except RuntimeError as e:
        assert "Ollama'ya bağlanılamadı" in str(e), e
    else:
        raise AssertionError("bağlantı hatası fark edilmedi")
    print("OK  Ollama kapalıyken açıklayıcı hata mesajı")


def test_brand_not_duplicated():
    from ai import propose
    page = {"id": 1, "url": "https://s.com/kategori/ust-giyim", "title": "Üst Giyim", "meta_description": None,
            "h1_text": None, "content_text": ""}
    chat = FakeModel({("kategori", "title"): [j("Üst Giyim - Düşes Butik"), j("Üst Giyim Modelleri")]})
    result, notes = propose("title", page, [{"message": "kısa"}], chat, "fake", "Düşes Butik")
    assert result["new"] == "Üst Giyim Modelleri | Düşes Butik" and "marka adı" in notes[0], (result, notes)
    print("OK  başlıkta marka adı geçerse reddedildi, 2. denemede tekrar yok")


if __name__ == "__main__":
    test_validate()
    test_brand_not_duplicated()
    path = test_pipeline()
    test_review(path)
    test_ollama_http()
    print("\nTüm AI testleri geçti.")