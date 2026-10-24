"""
Site profili: sitenin SAHİBİNİN girdiği kalıcı bilgiler. Model bunları uydurmaz, taramadan çıkarılmaz.

  brand_name                 marka adı (başlıklara ' | Marka' olarak kodla eklenir)
  example_titles             beğenilen örnek başlıklar (modele üslup örneği olarak gösterilir)
  example_meta_descriptions  beğenilen örnek meta description'lar
  banned_phrases             kullanılmak istenmeyen ifadeler (öneride geçerse reddedilir)

Kullanım:
    python site_profile.py --site https://www.duses.tr --import duses_profile.json
    python site_profile.py --site https://www.duses.tr --show
"""
import argparse
import json

from database import DB_PATH, get_connection, init_db
from storage import get_or_create_site

# Anahtar -> tür ("text" ya da "list")
PROFILE_KEYS = {
    "brand_name": "text",
    "example_titles": "list",
    "example_meta_descriptions": "list",
    "banned_phrases": "list",
}
# Alan adı (ai.LIMITS anahtarı) -> o alanın örnek listesinin profildeki anahtarı
EXAMPLE_KEY_FOR_FIELD = {"title": "example_titles", "meta_description": "example_meta_descriptions"}


def _clean_list(values):
    """Boşlukları düzeltir, boşları atar, tekrarları sırayı bozmadan eler."""
    seen, out = set(), []
    for value in values:
        text = " ".join(str(value).split())
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out


def _empty_profile():
    return {key: ("" if kind == "text" else []) for key, kind in PROFILE_KEYS.items()}


def set_value(conn, site_id, key, value, source="user"):
    """Bir profil değerini yazar (varsa günceller). Liste değerleri JSON olarak saklanır."""
    if key not in PROFILE_KEYS:
        raise ValueError(f"Bilinmeyen profil anahtarı: '{key}' (geçerli: {', '.join(PROFILE_KEYS)})")
    if PROFILE_KEYS[key] == "list":
        if isinstance(value, str) or not hasattr(value, "__iter__"):
            raise ValueError(f"'{key}' bir liste olmalı")
        stored = json.dumps(_clean_list(value), ensure_ascii=False)
    else:
        stored = " ".join(str(value).split())
    conn.execute(
        "INSERT INTO site_profile (site_id, key, value, source) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (site_id, key) DO UPDATE SET value = excluded.value, "
        "source = excluded.source, updated_at = CURRENT_TIMESTAMP",
        (site_id, key, stored, source),
    )


def get_profile(conn, site_id):
    """Profili sözlük olarak döndürür; girilmemiş alanlar boş ('' ya da [])."""
    profile = _empty_profile()
    rows = conn.execute("SELECT key, value FROM site_profile WHERE site_id = ?", (site_id,)).fetchall()
    for key, value in rows:
        kind = PROFILE_KEYS.get(key)
        if kind == "list":
            try:
                profile[key] = [str(v) for v in json.loads(value)]
            except ValueError:
                profile[key] = []
        elif kind == "text":
            profile[key] = value
    return profile


def profile_for_run(conn, run_id):
    """Bir taramanın ait olduğu sitenin profili (tarama yoksa boş profil)."""
    row = conn.execute("SELECT site_id FROM crawl_runs WHERE id = ?", (run_id,)).fetchone()
    return get_profile(conn, row[0]) if row else _empty_profile()


def check_profile(profile, limits):
    """
    Profil içeriği için uyarılar döndürür (engellemez):
      - örnek metin uzunluk sınırının dışında,
      - örnek metin kendi yasaklı ifadelerinden birini içeriyor.
    limits: {"title": (en_az, en_çok), "meta_description": (...)} — ai.LIMITS
    """
    warnings = []
    banned = [b.lower() for b in profile["banned_phrases"]]
    for field, key in EXAMPLE_KEY_FOR_FIELD.items():
        low, high = limits[field]
        for example in profile[key]:
            if not low <= len(example) <= high:
                warnings.append(f"{key}: {len(example)} karakter (önerilen {low}-{high}): {example}")
            hit = [b for b in banned if b in example.lower()]
            if hit:
                warnings.append(f"{key}: yasaklı ifade '{hit[0]}' içeriyor: {example}")
    return warnings


def import_profile(conn, site_url, data):
    """JSON verisini siteye yazar. Bilinmeyen anahtar hata verir. Dönüş: (site_id, uyarılar)."""
    unknown = set(data) - set(PROFILE_KEYS)
    if unknown:
        raise ValueError(f"Bilinmeyen profil anahtarı: {', '.join(sorted(unknown))}")
    site_id = get_or_create_site(conn, site_url)
    for key, value in data.items():
        set_value(conn, site_id, key, value)
    conn.commit()
    return site_id, check_profile(get_profile(conn, site_id), _limits())


def _limits():
    import ai  # tembel import: ai.py context'i, context de bu modülü import ediyor (döngü olmasın)
    return ai.LIMITS


def main():
    parser = argparse.ArgumentParser(description="Site profilini içe aktarır / gösterir.")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--site", required=True, help="Site adresi, örn. https://www.duses.tr")
    parser.add_argument("--import", dest="import_file", help="Profil JSON dosyası")
    parser.add_argument("--show", action="store_true", help="Kayıtlı profili göster")
    args = parser.parse_args()

    init_db(args.db)
    conn = get_connection(args.db)
    try:
        if args.import_file:
            with open(args.import_file, encoding="utf-8") as f:
                site_id, warnings = import_profile(conn, args.site.rstrip("/"), json.load(f))
            print(f"Profil kaydedildi (site #{site_id}).")
            for w in warnings:
                print(f"  uyarı: {w}")
        if args.show or not args.import_file:
            row = conn.execute("SELECT id FROM sites WHERE url = ?", (args.site.rstrip("/"),)).fetchone()
            if row is None:
                print("Bu site veritabanında yok.")
                return
            for key, value in get_profile(conn, row[0]).items():
                print(f"{key}:")
                for item in (value if isinstance(value, list) else [value]):
                    print(f"  - {item}" if item else "  (boş)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()