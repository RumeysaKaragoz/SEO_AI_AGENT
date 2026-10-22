"""
Modele gönderilecek sayfaya özgü bağlamı veritabanından seçer. Model ÇAĞRILMAZ, tabloya YAZILMAZ.

Kullanım (dry-run, istemi ekrana basar):
    python context.py --limit 3
    python context.py --limit 3 --field meta_description

build_context() iki şey döndürür:
    text        : kullanıcı mesajına eklenecek kısa bağlam (karakter bütçesiyle sınırlı)
    extra_facts : doğrulamanın (validate) 'facts' alanına eklenebilecek metin. Yalnızca
                  kategori sayfalarında dolu: kategorideki ürünlerin başlık/H1'i. Üslup örnekleri
                  ve onaylı öneriler buraya KONMAZ; model komşu sayfanın sayısını kopyalarsa
                  doğrulamadan geçmesin diye.
"""
import argparse
import re
from collections import Counter

from database import DB_PATH, get_connection

# Her bölümün karakter bütçesi (7B model ve num_ctx=4096 için küçük tutulur)
BUDGETS = {"examples": 450, "products": 500, "approved": 300, "duplicate": 200}
MAX_PRODUCTS = 8
MAX_EXAMPLES = 3
MAX_APPROVED = 2

# Kategori eşleştirmesinde ayırt edici olmayan kelimeler
GENERIC_TOKENS = {"giyim", "alt", "ust", "dis", "ic", "yeni", "tum", "koleksiyon", "urunleri", "modelleri"}

# Slug kelimesi ürünlerin bu oranından fazlasında geçiyorsa ayırt edici değildir ('ithal' gibi)
COMMON_TOKEN_RATIO = 0.5
MIN_PRODUCTS_FOR_COMMON = 10

# Üslup örneği sayılması için en kısa uzunluk: 'Koleksiyon' gibi tek kelimelik başlıklar örnek olamaz
MIN_EXAMPLE_LEN = {"title": 25, "meta_description": 70}

FIELD_NAMES = {"title": "başlık", "meta_description": "meta description"}
DUPLICATE_CODES = {"title": "DUPLICATE_TITLE", "meta_description": "DUPLICATE_META_DESC"}


# ----------------------------------------------------------------------------
# Küçük yardımcılar
# ----------------------------------------------------------------------------

def _path(url):
    return re.sub(r"^https?://[^/]+", "", url).split("?")[0].strip("/")


def kind_of(url):
    """'urun' | 'kategori' | 'ana' | 'diger'. Sorgu parametreli (?filtre=...) adresler 'varyant'."""
    if "?" in url:
        return "varyant"
    path = _path(url)
    if not path:
        return "ana"
    if path.startswith("urun/"):
        return "urun"
    if path.startswith("kategori/"):
        return "kategori"
    return "diger"


def slug_of(url):
    return _path(url).split("/", 1)[-1]


def _tr_upper_first(word):
    if not word:
        return word
    first = {"i": "İ", "ı": "I"}.get(word[0], word[0].upper())
    return first + word[1:]


def normalize_caps(text):
    """Çoğu harfi büyük olan metni 'Normal Yazım'a çevirir; model büyük harf tonunu taklit edebiliyor."""
    text = " ".join((text or "").split())
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) <= 0.6:
        return text
    lowered = text.replace("İ", "i").replace("I", "ı").lower()
    return " ".join(_tr_upper_first(w) for w in lowered.split(" "))


def _fit(header, lines, budget):
    """Başlık + satırları bütçeye sığdırır; sığmayan satırlar atılır. Hiç satır sığmazsa ''."""
    out, used = [], len(header)
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        out.append(line)
        used += len(line) + 1
    return header + "\n" + "\n".join(out) if out else ""


# ----------------------------------------------------------------------------
# Kategori - ürün eşleştirmesi (slug tabanlı; page_links gelene kadar)
# ----------------------------------------------------------------------------

def _tokens(slug):
    return {t for t in slug.split("-") if len(t) >= 3 and t not in GENERIC_TOKENS and not t.isdigit()}


def category_of_product(product_url, category_slugs, ignore=frozenset()):
    """
    Ürün slug'ına en iyi uyan kategorinin slug'ını döndürür (yoksa None).
    Puan: kategori kelimesi slug'da aynen geçerse 2, ürün kelimesi onunla başlıyorsa 1
    ('triko' -> 'trikolu', 'elbise' -> 'elbiseli'; en az 4 harfli kelimelerde).
    Eşitlikte: kelimelerinin daha büyük kısmı eşleşen kategori ('pantolon' 1/1, 'kot-pantolon' 1/2),
    sonra slug'da daha geç geçen kelime (ürün türü genelde adın sonundadır: 'kurk-deri-ceket' -> ceket).
    ignore: ayırt edici olmayan (çoğu üründe geçen) kelimeler.
    """
    words = slug_of(product_url).split("-")
    best, best_key = None, (0, 0, -1)
    for cat in sorted(category_slugs):
        tokens = _tokens(cat) - set(ignore)
        if not tokens:
            continue
        score, last = 0, -1
        for token in tokens:
            if token in words:
                score, last = score + 2, max(last, words.index(token))
                continue
            pos = next((i for i, w in enumerate(words) if len(token) >= 4 and w.startswith(token)), None)
            if pos is not None:
                score, last = score + 1, max(last, pos)
        key = (score, score / (2 * len(tokens)), last)
        if score and key > best_key:
            best, best_key = cat, key
    return best


def build_category_map(pages, category_slugs):
    """{ürün_sayfa_id: kategori_slug | None}. Çoğu üründe geçen kelimeler eşleştirmede yok sayılır."""
    products = [p for p in pages if kind_of(p["url"]) == "urun"]
    ignore = set()
    if len(products) >= MIN_PRODUCTS_FOR_COMMON:
        counts = Counter(t for p in products for t in set(slug_of(p["url"]).split("-")))
        ignore = {t for t, n in counts.items() if n / len(products) > COMMON_TOKEN_RATIO}
    return {p["id"]: category_of_product(p["url"], category_slugs, ignore) for p in products}


# ----------------------------------------------------------------------------
# Bağlam bölümleri
# ----------------------------------------------------------------------------

def _load_pages(conn, run_id):
    rows = conn.execute(
        "SELECT id, url, title, meta_description, h1_text FROM pages "
        "WHERE crawl_run_id = ? AND status_code = 200", (run_id,)).fetchall()
    keys = ("id", "url", "title", "meta_description", "h1_text")
    return [dict(zip(keys, r)) for r in rows]


def _pages_with_open_issues(conn, run_id, codes):
    placeholders = ",".join("?" * len(codes))
    rows = conn.execute(
        f"SELECT DISTINCT i.page_id FROM issues i JOIN pages p ON p.id = i.page_id "
        f"WHERE p.crawl_run_id = ? AND i.status = 'open' AND i.code IN ({placeholders})",
        (run_id, *codes)).fetchall()
    return {r[0] for r in rows}


def _well_written(text):
    """Büyük harfle başlıyor ve çoğu harf büyük değil. Sitenin kendi TAMAMEN BÜYÜK veya küçük harfle
    kopyalanmış metinleri üslup örneği olarak modele kötü ton öğretir."""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and letters[0].isupper() and sum(c.isupper() for c in letters) / len(letters) <= 0.6


def _style_examples(page, field, pages, problem_ids, category_map):
    """Aynı türden, bu alanda sorunu olmayan sayfaların değerleri. Aynı kategoridekiler önce gelir."""
    kind = kind_of(page["url"])
    my_cat = category_map.get(page["id"])
    candidates = []
    for p in pages:
        if p["id"] == page["id"] or p["id"] in problem_ids or kind_of(p["url"]) != kind:
            continue
        value = " ".join((p[field] or "").split())
        if len(value) < MIN_EXAMPLE_LEN[field] or not _well_written(value):
            continue
        same_cat = my_cat is not None and category_map.get(p["id"]) == my_cat
        candidates.append((not same_cat, p["id"], value))
    candidates.sort()
    lines = [f"- {v}" for _, _, v in candidates[:MAX_EXAMPLES]]
    return _fit(f"Sitede aynı türden sorunsuz {FIELD_NAMES[field]} örnekleri (yalnızca üslup için, kopyalama):",
                lines, BUDGETS["examples"])


def _category_products(page, pages, category_map):
    """
    Kategori sayfası için o kategoriye ait ürünlerin adları. Başlık kullanılır (H1 beden/renk eki taşır:
    'STD - Siyah'); başlık çok kısaysa H1. Marka eki atılır, aynı ad (renk varyantları) tek sayılır.
    """
    my_slug = slug_of(page["url"])
    names, seen = [], set()
    for p in sorted(pages, key=lambda p: p["id"]):
        if category_map.get(p["id"]) != my_slug:
            continue
        title = " ".join((p["title"] or "").split(" | ")[0].split())
        name = title if len(title) >= 10 else " ".join((p["h1_text"] or "").split())
        key = normalize_caps(name).lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names[:MAX_PRODUCTS]


def _approved_examples(conn, page, field):
    rows = conn.execute(
        "SELECT p.url, ac.new_value FROM applied_changes ac JOIN pages p ON p.id = ac.page_id "
        "WHERE ac.change_type = ? AND ac.status IN ('approved', 'applied') AND ac.page_id != ? "
        "ORDER BY ac.id DESC LIMIT 20", (field, page["id"])).fetchall()
    kind = kind_of(page["url"])
    lines = [f"- {new}" for url, new in rows if kind_of(url) == kind and new][:MAX_APPROVED]
    return _fit(f"Daha önce onayladığın {FIELD_NAMES[field]} önerileri (aynı üslupta yaz):",
                lines, BUDGETS["approved"])


def _duplicate_info(page, field, pages, issue_codes):
    if DUPLICATE_CODES[field] not in issue_codes or not page[field]:
        return ""
    others = [p for p in pages if p["id"] != page["id"] and kind_of(p["url"]) != "varyant"
              and p[field] == page[field]]
    if not others:
        return ""
    sample = ", ".join("/" + _path(p["url"]) for p in others[:2])
    text = (f"Bu {FIELD_NAMES[field]} sitede {len(others) + 1} sayfada aynı (örn. {sample}). "
            f"Bu sayfaya özgü yaz.")
    return text[:BUDGETS["duplicate"]]


# ----------------------------------------------------------------------------
# Ana fonksiyon
# ----------------------------------------------------------------------------

def build_context(conn, run_id, page, field, issue_codes, field_codes):
    """
    conn        : sqlite3 bağlantısı
    page        : {"id", "url", "title", "meta_description", "h1_text", ...}
    field       : "title" | "meta_description"
    issue_codes : bu sayfanın bu alandaki açık sorun kodları
    field_codes : {sorun_kodu: alan}  (ai.FIELD_FOR_CODE); "sorunsuz örnek" seçimi için
    Döndürür: {"text": str, "extra_facts": str}
    """
    pages = _load_pages(conn, run_id)
    category_slugs = {slug_of(p["url"]) for p in pages if kind_of(p["url"]) == "kategori"}
    category_map = build_category_map(pages, category_slugs)
    field_code_list = [c for c, f in field_codes.items() if f == field]
    problem_ids = _pages_with_open_issues(conn, run_id, field_code_list)

    kind = kind_of(page["url"])
    sections, extra_facts = [], ""

    if kind == "kategori":
        names = _category_products(page, pages, category_map)
        extra_facts = " ".join(names)
        lines = [f"- {normalize_caps(n)}" for n in names]
        sections.append(_fit("Bu kategorideki ürünler (sayfada gerçekten bulunan ürün türlerini yazmak için):",
                             lines, BUDGETS["products"]))
    sections.append(_style_examples(page, field, pages, problem_ids, category_map))
    sections.append(_approved_examples(conn, page, field))
    sections.append(_duplicate_info(page, field, pages, issue_codes))

    return {"text": "\n\n".join(s for s in sections if s), "extra_facts": extra_facts}


# ----------------------------------------------------------------------------
# Dry-run: gerçek veritabanında modelsiz bağlam önizlemesi
# ----------------------------------------------------------------------------

def main():
    import ai  # tembel import: ai.py ileride context'i import edecek, döngü olmasın

    parser = argparse.ArgumentParser(description="Öneri bağlamını modele göndermeden önizler.")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--field", choices=sorted(FIELD_NAMES), default=None)
    args = parser.parse_args()

    conn = get_connection(args.db)
    try:
        run_id = args.run_id or ai._latest_run_id(conn)
        codes = list(ai.FIELD_FOR_CODE)
        rows = conn.execute(
            f"SELECT p.id, p.url, p.title, p.meta_description, p.h1_text, i.code "
            f"FROM issues i JOIN pages p ON p.id = i.page_id "
            f"WHERE p.crawl_run_id = ? AND i.status = 'open' AND i.code IN ({','.join('?' * len(codes))}) "
            f"ORDER BY p.id, i.id", (run_id, *codes)).fetchall()
        pages = {}
        for pid, url, title, meta, h1, code in rows:
            if ai.is_skipped_page(url) or kind_of(url) == "varyant":
                continue
            if code.startswith("DUPLICATE_") and kind_of(url) == "ana":
                continue
            entry = pages.setdefault(pid, {"page": {"id": pid, "url": url, "title": title,
                                                    "meta_description": meta, "h1_text": h1}, "codes": {}})
            entry["codes"].setdefault(ai.FIELD_FOR_CODE[code], []).append(code)

        shown = 0
        for entry in pages.values():
            for field, field_issue_codes in entry["codes"].items():
                if args.field and field != args.field:
                    continue
                if shown >= args.limit:
                    return
                shown += 1
                ctx = build_context(conn, run_id, entry["page"], field, field_issue_codes, ai.FIELD_FOR_CODE)
                print("=" * 70)
                print(f"{entry['page']['url']}  [{field}]  bağlam: {len(ctx['text'])} karakter")
                print("-" * 70)
                print(ctx["text"] or "(bağlam boş)")
                if ctx["extra_facts"]:
                    print(f"\n[doğrulama facts'ine eklenecek: {len(ctx['extra_facts'])} karakter]")
    finally:
        conn.close()


if __name__ == "__main__":
    main()