"""context.py testleri. Model YOK; yalnızca geçici SQLite veritabanı kullanılır."""
import os
import tempfile

from ai import FIELD_FOR_CODE
from context import BUDGETS, MAX_PRODUCTS, build_context, category_of_product, kind_of, normalize_caps
from database import get_connection, init_db

BASE = "https://s.com"
SHARED_META = "Düşes Butik şık kadın giyim modelleri ve en yeni koleksiyonu."


def add_page(c, url, title=None, meta=None, h1=None):
    cur = c.execute(
        "INSERT INTO pages (crawl_run_id, url, status_code, title, meta_description, h1_text, content_text) "
        "VALUES (1, ?, 200, ?, ?, ?, 'menü')", (BASE + url, title, meta, h1))
    return cur.lastrowid


def add_issue(c, page_id, code, status="open"):
    c.execute("INSERT INTO issues (page_id, code, category, severity, message, status) "
              "VALUES (?, ?, 'on-page', 'warning', ?, ?)", (page_id, code, code, status))


def build_db():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(path)
    c = get_connection(path)
    c.execute("INSERT INTO sites (url) VALUES (?)", (BASE,))
    c.execute("INSERT INTO crawl_runs (site_id, status) VALUES (1, 'completed')")
    ids = {}
    for slug, title in [("mont-kaban", "Mont-Kaban"), ("triko", "Triko"), ("pantolon", "Pantolon")]:
        ids[slug] = add_page(c, f"/kategori/{slug}", title, SHARED_META, None)
        add_issue(c, ids[slug], "TITLE_TOO_SHORT")
        add_issue(c, ids[slug], "DUPLICATE_META_DESC")
    ids["varyant"] = add_page(c, "/kategori/triko?filtre_secenekleri=renk-siyah", "Triko", SHARED_META, None)

    ids["mont1"] = add_page(c, "/urun/ithal-kurklu-ekose-mont-9009-lacivert-std", "Mont",
                            "ithal kürklü ekose mont 9009", "İTHAL KÜRKLÜ EKOSE MONT 9009 STD - Lacivert")
    add_issue(c, ids["mont1"], "TITLE_TOO_SHORT")
    add_issue(c, ids["mont1"], "META_DESC_TOO_SHORT")
    ids["mont2"] = add_page(c, "/urun/kaban-9010-siyah-std", "Yünlü Kaban Siyah | Düşes Butik",
                            "Yünlü kaban 9010 siyah renk seçeneğiyle inceleyin.", "YÜNLÜ KABAN 9010 STD - Siyah")
    ids["mont3"] = add_page(c, "/urun/uzun-mont-9011-bej-std", "Uzun Mont Bej | Düşes Butik",
                            "Uzun mont 9011 bej renkte inceleyin ve sipariş verin.", "UZUN MONT 9011 STD - Bej")
    ids["triko1"] = add_page(c, "/urun/klasik-triko-2001-krem-std", "Klasik Triko Krem | Düşes Butik",
                             "Klasik triko 2001 krem renkte inceleyin ve sipariş verin.", "KLASİK TRİKO 2001 STD - Krem")
    ids["triko2"] = add_page(c, "/urun/yuvarlak-yaka-triko-2002-gri-std", "Yuvarlak Yaka Triko Gri | Düşes Butik",
                             None, "YUVARLAK YAKA TRİKO 2002 STD - Gri")
    add_issue(c, ids["triko2"], "META_DESC_MISSING")
    ids["uye"] = add_page(c, "/hesabim/favorilerim", "Favorilerim", None, None)
    c.commit()
    c.close()
    return path, ids


def page_dict(c, pid):
    r = c.execute("SELECT id, url, title, meta_description, h1_text FROM pages WHERE id = ?", (pid,)).fetchone()
    return dict(zip(("id", "url", "title", "meta_description", "h1_text"), r))


def ctx(c, pid, field, codes):
    return build_context(c, 1, page_dict(c, pid), field, codes, FIELD_FOR_CODE)


def test_helpers():
    assert kind_of(BASE + "/urun/x") == "urun" and kind_of(BASE + "/kategori/triko") == "kategori"
    assert kind_of(BASE + "/kategori/triko?filtre_secenekleri=a") == "varyant"
    assert kind_of(BASE) == "ana" and kind_of(BASE + "/hakkimizda") == "diger"
    assert normalize_caps("YÜKSEK BEL KOT PANTOLON 1204 38 - Mavi") == "Yüksek Bel Kot Pantolon 1204 38 - Mavi"
    assert normalize_caps("İNCE TRİKO IŞIK") == "İnce Triko Işık"
    assert normalize_caps("Zaten Normal Yazılmış Başlık") == "Zaten Normal Yazılmış Başlık"
    cats = {"mont-kaban", "triko", "pantolon", "kot-pantolon", "alt-giyim"}
    assert category_of_product(BASE + "/urun/ithal-mont-9009-lacivert", cats) == "mont-kaban"
    assert category_of_product(BASE + "/urun/yuksek-bel-kot-pantolon-1204", cats) == "kot-pantolon"
    assert category_of_product(BASE + "/urun/duz-pantolon-1205", cats) == "pantolon"
    assert category_of_product(BASE + "/urun/alt-parca-77", cats) is None  # 'alt' ayırt edici sayılmaz
    print("OK  yardımcılar: sayfa türü, büyük harf düzeltme, slug ile kategori eşleştirme")


def test_category_context():
    path, ids = build_db()
    c = get_connection(path)
    out = ctx(c, ids["triko"], "meta_description", ["DUPLICATE_META_DESC"])
    text = out["text"]
    for expected in ("Klasik Triko 2001 Std - Krem", "Yuvarlak Yaka Triko 2002 Std - Gri"):
        assert expected in text, text
    assert "Ekose" not in text and "Kaban" not in text, text      # başka kategorinin ürünleri yok
    assert "3 sayfada aynı" in text, text                          # 3 kategori; filtre varyantı sayılmaz
    assert "filtre" not in text and "?" not in text
    assert "KLASİK" in out["extra_facts"] and "EKOSE" not in out["extra_facts"]  # facts ham metni taşır
    print("OK  kategori sayfası: yalnızca kendi ürünleri, normal yazım, ham metin extra_facts'te, varyant sayılmadı")
    c.close()


def test_product_context():
    path, ids = build_db()
    c = get_connection(path)
    out = ctx(c, ids["mont1"], "title", ["TITLE_TOO_SHORT"])
    lines = [l for l in out["text"].splitlines() if l.startswith("- ")]
    assert out["extra_facts"] == ""                                # yalnızca kategori sayfasında dolu
    assert "Mont" != lines[0][2:]                                  # kendisi örnek seçilmez
    assert lines[0].startswith("- Uzun Mont") or lines[0].startswith("- Yünlü Kaban"), lines  # aynı kategori önce
    assert len(lines) == 3, lines
    assert all("Favorilerim" not in l and "Mont-Kaban" not in l for l in lines)  # üye/kategori sayfası yok

    meta_out = ctx(c, ids["triko2"], "meta_description", ["META_DESC_MISSING"])
    assert "ithal kürklü ekose mont 9009" not in meta_out["text"]   # meta sorunu açık olan sayfa örnek olmaz
    assert "Klasik triko 2001" in meta_out["text"].splitlines()[1]    # aynı kategori ilk sırada
    print("OK  ürün sayfası: kendisi ve sorunlu sayfalar örnek değil, aynı kategori önce, extra_facts boş")
    c.close()


def test_approved_examples():
    path, ids = build_db()
    c = get_connection(path)
    for page, status, new in [(ids["mont2"], "approved", "Onaylı Kaban Başlığı Siyah"),
                              (ids["mont3"], "rejected", "Reddedilen Mont Başlığı Bej"),
                              (ids["triko1"], "pending", "Bekleyen Triko Başlığı Krem"),
                              (ids["triko"], "approved", "Onaylı Kategori Başlığı Triko")]:
        c.execute("INSERT INTO applied_changes (page_id, change_type, new_value, status) VALUES (?, 'title', ?, ?)",
                  (page, new, status))
    c.commit()
    text = ctx(c, ids["mont1"], "title", ["TITLE_TOO_SHORT"])["text"]
    assert "Onaylı Kaban Başlığı Siyah" in text
    assert "Reddedilen" not in text and "Bekleyen" not in text      # yalnızca approved/applied
    assert "Onaylı Kategori" not in text                            # ürün sayfasına kategori önerisi karışmaz
    print("OK  onaylı öneriler: yalnızca approved/applied ve aynı sayfa türünden")
    c.close()


def test_budget_and_readonly():
    path, ids = build_db()
    c = get_connection(path)
    for n in range(30):
        add_page(c, f"/urun/uzun-adli-triko-modeli-{3000 + n}-renk-std", f"Triko {n}", None,
                 f"ÇOK UZUN ADLI TRİKO MODELİ {3000 + n} STD - Renk Seçeneği Bir İki Üç Dört")
    c.commit()
    before = c.total_changes
    out = ctx(c, ids["triko"], "meta_description", ["DUPLICATE_META_DESC"])
    sections = out["text"].split("\n\n")
    product_section = [s for s in sections if s.startswith("Bu kategorideki")][0]
    assert product_section.count("\n- ") <= MAX_PRODUCTS and len(product_section) <= BUDGETS["products"], len(product_section)
    assert len(out["text"]) <= sum(BUDGETS.values()) + 6, len(out["text"])
    assert c.total_changes == before, "build_context veritabanına yazmamalı"

    empty = ctx(c, ids["uye"], "title", [])
    assert empty["extra_facts"] == "" and "Bu kategorideki" not in empty["text"]
    print("OK  bütçe: bölümler sınırı aşmıyor, ürün sayısı ≤ 8; veritabanına yazılmıyor")
    c.close()


if __name__ == "__main__":
    test_helpers()
    test_category_context()
    test_product_context()
    test_approved_examples()
    test_budget_and_readonly()
    print("\nTüm bağlam testleri geçti.")