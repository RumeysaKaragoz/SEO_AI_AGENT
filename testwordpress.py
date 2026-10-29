"""wordpress.py testleri: gerçek WordPress YOK; WordPress REST davranışını taklit eden yerel sahte sunucu."""
import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from editor.apply_changes import PublishError, apply_change
from database import get_connection
from testapply import build_db
from wordpress import WordPressPublisher

USER, PASSWORD = "editor", "abcd efgh ijkl"


class FakeWP(BaseHTTPRequestHandler):
    content, registered, stored, posts = {}, set(), {}, []

    def _send(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        return self.headers.get("Authorization") == expected

    def do_GET(self):
        if not self._authorized():
            return self._send(401, {"code": "rest_forbidden"})
        parsed = urlparse(self.path)
        if parsed.path == "/wp-json/wp/v2/users/me":
            return self._send(200, {"id": 1, "name": "Editor"})
        m = re.fullmatch(r"/wp-json/wp/v2/(pages|posts)", parsed.path)
        if not m:
            return self._send(404, {"code": "rest_no_route"})
        if "slug" not in parse_qs(parsed.query):       # liste (check)
            pages = [{"id": 5, "meta": {k: "" for k in FakeWP.registered}}] if m.group(1) == "pages" else []
            return self._send(200, pages)
        slug = parse_qs(parsed.query)["slug"][0]
        port = self.server.server_address[1]
        found = FakeWP.content.get(m.group(1), {}).get(slug, [])
        self._send(200, [{"id": i["id"], "link": f"http://127.0.0.1:{port}{i['path']}"} for i in found])

    def do_POST(self):
        if not self._authorized():
            return self._send(401, {"code": "rest_forbidden"})
        m = re.fullmatch(r"/wp-json/wp/v2/(pages|posts)/(\d+)", self.path)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeWP.posts.append((m.group(1), int(m.group(2)), body))
        for key, value in body.get("meta", {}).items():
            if key in FakeWP.registered:          # kayıtsız meta anahtarı: WordPress sessizce yok sayar
                FakeWP.stored[key] = value
        self._send(200, {"id": int(m.group(2)), "meta": {k: v for k, v in FakeWP.stored.items()}})

    def log_message(self, *a):
        pass


class HtmlNotFound(BaseHTTPRequestHandler):
    """Kalıcı bağlantı ayarı yok / mod_rewrite kapalı: /wp-json için Apache'nin HTML 404 sayfası."""

    def do_GET(self):
        data = b"<html><body><h1>Not Found</h1></body></html>"
        self.send_response(404)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def reset(registered=("_yoast_wpseo_title", "_yoast_wpseo_metadesc")):
    FakeWP.registered, FakeWP.stored, FakeWP.posts = set(registered), {}, []
    FakeWP.content = {"pages": {"hakkimizda": [{"id": 5, "path": "/hakkimizda/"},
                                               {"id": 6, "path": "/kurumsal/hakkimizda/"}]},
                      "posts": {"merhaba": [{"id": 9, "path": "/merhaba/"}]}}


def expect_error(fn, *needles):
    try:
        fn()
    except PublishError as e:
        for n in needles:
            assert n in str(e), (n, str(e))
        return
    raise AssertionError(f"PublishError bekleniyordu: {needles}")


def change(url, field="title", new="Yeni Başlık Metni Burada"):
    return {"id": 1, "url": url, "field": field, "old": "x", "new": new, "status": "approved"}


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWP)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    wp = WordPressPublisher(base, USER, PASSWORD, "yoast")

    reset()
    wp.apply(change(base + "/hakkimizda"))
    assert FakeWP.stored == {"_yoast_wpseo_title": "Yeni Başlık Metni Burada"}
    assert FakeWP.posts == [("pages", 5, {"meta": {"_yoast_wpseo_title": "Yeni Başlık Metni Burada"}})]
    wp.apply(change(base + "/hakkimizda/", "meta_description", "Yeni açıklama metni burada."))
    assert FakeWP.stored["_yoast_wpseo_metadesc"] == "Yeni açıklama metni burada."
    print("OK  Yoast: sayfa bulundu, doğru meta anahtarı yazıldı ve yanıttan doğrulandı")

    reset(registered=("rank_math_title", "rank_math_description"))
    WordPressPublisher(base, USER, PASSWORD, "rankmath").apply(change(base + "/merhaba", "meta_description", "Açıklama burada, yeterince uzun."))
    assert FakeWP.posts[0][:2] == ("posts", 9) and FakeWP.stored == {"rank_math_description": "Açıklama burada, yeterince uzun."}
    print("OK  Rank Math: sayfada yoksa yazı (posts) rotasında bulundu, rank_math_* anahtarı yazıldı")

    reset()
    wp.apply(change(base + "/kurumsal/hakkimizda"))
    assert FakeWP.posts[0][:2] == ("pages", 6), FakeWP.posts
    print("OK  aynı slug iki üst sayfada: tam yol eşleşen (id 6) seçildi")

    reset(registered=())
    expect_error(lambda: wp.apply(change(base + "/hakkimizda")), "REST API'ye açık değil", "wp_seo_rest.php")
    print("OK  alan REST'e açık değilse (WordPress sessizce yok sayar) başarı sayılmadı")

    reset()
    expect_error(lambda: WordPressPublisher(base, USER, "yanlis", "yoast").apply(change(base + "/hakkimizda")), "kimlik doğrulaması")
    expect_error(lambda: wp.apply(change(base + "/yok-boyle-sayfa")), "bulunamadı")
    expect_error(lambda: wp.apply(change(base + "/")), "Ana sayfa")
    expect_error(lambda: wp.apply(change("https://baska-site.com/hakkimizda")), "ait değil")
    expect_error(lambda: wp.apply(change(base + "/hakkimizda", "h1")), "desteklenmiyor")
    assert FakeWP.posts == [], "hata durumlarında yazma isteği gitmemeli"
    print("OK  yanlış parola / bulunamayan sayfa / ana sayfa / başka site / desteklenmeyen alan: yazma isteği gitmedi")

    reset()
    assert wp.check() == ("Editor", True)
    reset(registered=())
    assert wp.check() == ("Editor", False)
    expect_error(lambda: WordPressPublisher(base, USER, "yanlis", "yoast").check(), "kimlik doğrulaması")
    print("OK  check(): kullanıcı adı ve SEO alanlarının REST'e açık olup olmadığı; yanlış parola hata verir")
    reset()

    try:
        WordPressPublisher(base, USER, PASSWORD, "bilinmeyen")
    except ValueError:
        pass
    else:
        raise AssertionError("bilinmeyen eklenti kabul edildi")

    # apply_change ile uçtan uca: kayıt durumu
    c = build_db()
    c.execute("UPDATE pages SET url = ?", (base + "/hakkimizda",))
    c.commit()
    reset(registered=())
    assert apply_change(c, 1, wp)["status"] == "failed"
    reset()
    assert apply_change(c, 1, wp)["status"] == "applied"
    assert c.execute("SELECT status FROM applied_changes WHERE id = 1").fetchone()[0] == "applied"
    print("OK  apply_change: eklenti hazır değilken 'failed', hazır olunca yeniden denemede 'applied'")

    html404 = ThreadingHTTPServer(("127.0.0.1", 0), HtmlNotFound)
    threading.Thread(target=html404.serve_forever, daemon=True).start()
    site404 = f"http://127.0.0.1:{html404.server_address[1]}"
    expect_error(lambda: WordPressPublisher(site404, USER, PASSWORD, "yoast").apply(change(site404 + "/hakkimizda")),
                 "Kalıcı Bağlantılar", "mod_rewrite", "/wp-json/")
    html404.shutdown()
    html404.server_close()
    print("OK  Apache HTML 404'ü (wp-json ulaşılamıyor): HTML yığını yerine kalıcı bağlantı / mod_rewrite ipucu")

    server.shutdown()
    server.server_close()
    down = WordPressPublisher(base, USER, PASSWORD, "yoast", timeout=2)
    expect_error(lambda: down.apply(change(base + "/hakkimizda")), "bağlanılamadı")
    print("OK  site kapalıyken açık hata (PublishError)")
    print("\nTüm WordPress testleri geçti.")


if __name__ == "__main__":
    main()
