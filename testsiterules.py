"""robots.txt / sitemap ve tekrar eden içerik kurallarının birim testleri."""
import functools
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from seo.rules import SEVERITY_BY_CODE
from seo.site_rules import check_duplicates, check_site

SITEMAP = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
ROBOTS = "User-agent: *\nDisallow: /admin\n"


class Routes(BaseHTTPRequestHandler):
    """routes: {path: (status, content_type, body)}; olmayan yol -> varsayılan"""

    def __init__(self, routes, default, *args, **kwargs):
        self.routes, self.default = routes, default
        super().__init__(*args, **kwargs)

    def do_GET(self):
        status, ctype, body = self.routes.get(self.path, self.default)
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def serve(routes, default=(404, "text/plain", "yok")):
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Routes, routes, default))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def codes(origin):
    return sorted(i["code"] for i in check_site(origin))


def main():
    # 1) Hiçbir dosya yok
    server, origin = serve({})
    assert codes(origin) == ["ROBOTS_TXT_MISSING", "SITEMAP_MISSING"]
    server.shutdown()
    print("OK  robots.txt ve sitemap yoksa ikisi de raporlandı")

    # 2) robots.txt var, Sitemap satırı ile geçerli sitemap gösteriyor
    routes = {"/sm.xml": (200, "application/xml", SITEMAP)}
    server, origin = serve(routes)
    routes["/robots.txt"] = (200, "text/plain", ROBOTS + f"Sitemap: {origin}/sm.xml\n")
    assert codes(origin) == []
    server.shutdown()
    print("OK  robots.txt + robots.txt'te belirtilen sitemap -> sorun yok")

    # 3) robots.txt'te Sitemap satırı yok ama /sitemap.xml var
    server, origin = serve({"/robots.txt": (200, "text/plain", ROBOTS),
                            "/sitemap.xml": (200, "application/xml", SITEMAP)})
    assert codes(origin) == []
    server.shutdown()
    print("OK  Sitemap satırı yok ama /sitemap.xml var -> sorun yok")

    # 4) robots.txt'te belirtilen sitemap erişilemiyor
    routes = {}
    server, origin = serve(routes)
    routes["/robots.txt"] = (200, "text/plain", ROBOTS + f"Sitemap: {origin}/yok.xml\n")
    assert codes(origin) == ["SITEMAP_MISSING"]
    server.shutdown()
    print("OK  belirtilen ama erişilemeyen sitemap raporlandı")

    # 5) Soft-404: her adrese 200 + HTML dönen site (SPA / e-ticaret sıkça yapar)
    server, origin = serve({}, default=(200, "text/html", "<html><body>Ana sayfa</body></html>"))
    assert codes(origin) == ["ROBOTS_TXT_MISSING", "SITEMAP_MISSING"]
    server.shutdown()
    print("OK  her adrese 200+HTML dönen site: sahte robots.txt/sitemap geçerli sayılmadı")

    # 6) Belirlenemeyen durum: 403 -> rapor YOK (bot engelleme "yok" demek değildir)
    server, origin = serve({}, default=(403, "text/plain", "forbidden"))
    assert codes(origin) == []
    server.shutdown()
    print("OK  403 durumunda belirlenemedi: yanlış 'eksik' raporlanmadı")

    # 7) Sunucu hiç yok (bağlantı hatası): rapor YOK
    server, origin = serve({})
    server.shutdown()
    server.server_close()
    assert codes(origin) == []
    print("OK  bağlantı hatasında belirlenemedi: yanlış 'eksik' raporlanmadı")

    # --- tekrar eden içerik ---
    def page(url, title, desc, chash):
        return {"url": url, "data": {"title": title, "meta_description": desc, "content_hash": chash}}

    pages = [
        page("https://s.com/", "Ana Sayfa", "Açıklama bir", "h1"),
        page("https://s.com/index.html", "  ana   SAYFA ", "Açıklama iki", "h1"),
        page("https://s.com/x", "Benzersiz", "Açıklama bir", "h2"),
        page("https://s.com/y", None, None, None),
        page("https://s.com/z", None, None, None),
    ]
    found = check_duplicates(pages)
    by = {u: sorted(i["code"] for i in v) for u, v in found.items()}
    # "/" hem içerik+başlığı index.html ile, hem meta description'ı /x ile paylaşıyor
    assert by["https://s.com/"] == ["DUPLICATE_CONTENT", "DUPLICATE_META_DESC", "DUPLICATE_TITLE"], by
    assert by["https://s.com/index.html"] == ["DUPLICATE_CONTENT", "DUPLICATE_TITLE"], by
    assert by["https://s.com/"] and "index.html" in found["https://s.com/"][0]["message"]
    assert "DUPLICATE_META_DESC" in [i["code"] for i in found["https://s.com/x"]]
    assert "https://s.com/y" not in by and "https://s.com/z" not in by  # boş başlıklar eşleşmez
    print("OK  tekrar eden başlık / içerik / meta description doğru sayfalara bağlandı, boşlar eşleşmedi")

    # Yalnızca sorgu dizesiyle ayrılan URL varyantları "tekrar eden başlık" sayılmaz
    variants = [
        page("https://s.com/kategori/x", "Koleksiyon", "Açıklama", "h1"),
        page("https://s.com/kategori/x?filtre=a", "Koleksiyon", "Açıklama", "h1"),
        page("https://s.com/kategori/x?filtre=b", "Koleksiyon", "Açıklama", "h1"),
    ]
    assert check_duplicates(variants) == {}
    mixed = variants + [page("https://s.com/kategori/y", "Koleksiyon", "Başka", "h2")]
    assert {"DUPLICATE_TITLE"} <= {i["code"] for i in check_duplicates(mixed)["https://s.com/kategori/y"]}
    print("OK  yalnızca ?filtre varyantlarından oluşan gruplar tekrar sayılmadı; farklı sayfayla çakışma yine yakalanıyor")

    # --- her kuralın bir önem derecesi var mı? ---
    import re
    import seo.rules as rules_module
    import seo.site_rules as site_module
    source = open(rules_module.__file__, encoding="utf-8").read() + open(site_module.__file__, encoding="utf-8").read()
    used = set(re.findall(r'make_issue\(\s*f?"([A-Z0-9_]+)"', source)) | {"PAGE_CONTENT_MISSING"}
    missing = used - set(SEVERITY_BY_CODE)
    assert not missing, missing
    assert set(SEVERITY_BY_CODE.values()) <= {"critical", "warning", "info"}
    print("OK  kullanılan her sorun kodunun SEVERITY_BY_CODE tablosunda önem derecesi var")

    print("\nTüm site kuralı testleri geçti.")


if __name__ == "__main__":
    main()