"""
WordPress'e SEO başlığı / meta description yazan Publisher (apply_changes.Publisher).

Kimlik doğrulama: WordPress'in kendi "Uygulama Parolası" (Kullanıcılar > Profil). Asıl parolanı kullanma.
SEO eklentisinin (Yoast veya Rank Math) alanları REST API'ye varsayılan olarak AÇIK DEĞİLDİR;
siteye wp_seo_rest.php eklenmeli. Eklenmediyse WordPress isteği sessizce yok sayar: bu yüzden yazdıktan
sonra yanıttaki değer kontrol edilir ve uyuşmazsa kayıt 'applied' DEĞİL 'failed' olur.

Yalnızca sayfa (page) ve yazı (post) desteklenir; kategori, ürün ve ana sayfa için PublishError verir.
"""
from urllib.parse import urlparse

import requests

from editor.apply_changes import PublishError, Publisher

SEO_META_KEYS = {
    "yoast": {"title": "_yoast_wpseo_title", "meta_description": "_yoast_wpseo_metadesc"},
    "rankmath": {"title": "rank_math_title", "meta_description": "rank_math_description"},
}
ROUTES = ("pages", "posts")


def _same(a, b):
    return " ".join((a or "").split()) == " ".join((b or "").split())


class WordPressPublisher(Publisher):
    def __init__(self, site_url, username, app_password, seo_plugin="yoast", timeout=30):
        if seo_plugin not in SEO_META_KEYS:
            raise ValueError(f"seo_plugin şunlardan biri olmalı: {', '.join(SEO_META_KEYS)}")
        self.site = site_url.rstrip("/")
        self.auth = (username, app_password)
        self.keys = SEO_META_KEYS[seo_plugin]
        self.timeout = timeout

    def _request(self, method, path, **kwargs):
        try:
            response = requests.request(method, self.site + path, auth=self.auth,
                                        timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise PublishError(f"WordPress'e bağlanılamadı: {e}")
        if response.status_code in (401, 403):
            raise PublishError("WordPress kimlik doğrulaması reddedildi "
                               "(kullanıcı adı, uygulama parolası veya yetki kontrol edilmeli)")
        if response.status_code == 404 and "json" not in response.headers.get("Content-Type", "").lower():
            # JSON olmayan 404: sunucu /wp-json adresini hiç WordPress'e iletmiyor (kalıcı bağlantı / mod_rewrite)
            raise PublishError(f"WordPress REST adresi bulunamadı (404). Ayarlar > Kalıcı Bağlantılar'da 'Yazı adı' "
                               f"seçili olmalı ve Apache'de mod_rewrite açık olmalı. Kontrol: tarayıcıda "
                               f"{self.site}/wp-json/ adresi JSON göstermeli.")
        if response.status_code >= 400:
            raise PublishError(f"WordPress hatası ({response.status_code}): {response.text[:200]}")
        try:
            return response.json()
        except ValueError:
            raise PublishError("WordPress yanıtı JSON değil (REST API kapalı veya kalıcı bağlantılar bozuk olabilir)")

    def _find(self, url):
        """URL'ye karşılık gelen (rota, id). Aynı slug farklı üst sayfalarda olabilir: tam yol eşleşmeli."""
        parsed = urlparse(url)
        if parsed.netloc != urlparse(self.site).netloc:
            raise PublishError("Bu adres, ayarlanan WordPress sitesine ait değil")
        path = parsed.path.strip("/")
        if not path:
            raise PublishError("Ana sayfa slug ile bulunamıyor; elle uygulayın")
        slug = path.split("/")[-1]
        for route in ROUTES:
            items = self._request("GET", f"/wp-json/wp/v2/{route}", params={"slug": slug, "context": "edit"})
            if not isinstance(items, list):
                raise PublishError("WordPress beklenmeyen bir yanıt döndürdü")
            for item in items:
                if urlparse(item.get("link", "")).path.strip("/") == path:
                    return route, item["id"]
        raise PublishError("WordPress'te bu adrese ait sayfa/yazı bulunamadı "
                           "(kategori ve ürün sayfaları desteklenmiyor)")

    def check(self):
        """Bağlantıyı doğrular. (kullanıcı adı, SEO alanları REST'e açık mı: True / False / None=sitede sayfa yok)."""
        me = self._request("GET", "/wp-json/wp/v2/users/me", params={"context": "edit"})
        name = me.get("name") if isinstance(me, dict) else None
        pages = self._request("GET", "/wp-json/wp/v2/pages",
                              params={"per_page": 1, "context": "edit", "_fields": "id,meta"})
        exposed = None
        if isinstance(pages, list) and pages:
            meta = pages[0].get("meta")
            exposed = isinstance(meta, dict) and all(k in meta for k in self.keys.values())
        return name, exposed

    def apply(self, change):
        key = self.keys.get(change["field"])
        if key is None:
            raise PublishError(f"'{change['field']}' alanı WordPress için desteklenmiyor")
        route, post_id = self._find(change["url"])
        data = self._request("POST", f"/wp-json/wp/v2/{route}/{post_id}", json={"meta": {key: change["new"]}})
        meta = data.get("meta") if isinstance(data, dict) else None
        if not isinstance(meta, dict) or not _same(meta.get(key), change["new"]):
            raise PublishError(f"'{key}' alanı REST API'ye açık değil veya yazılamadı; "
                               "wp_seo_rest.php dosyasını siteye ekleyin")
