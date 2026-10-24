"""site_profile.py testleri. Model YOK; yalnızca geçici SQLite veritabanı kullanılır."""
import json
import os
import tempfile

from ai import LIMITS
from database import get_connection, init_db
from site_profile import check_profile, get_profile, import_profile, profile_for_run, set_value


def new_db():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(path)
    c = get_connection(path)
    c.execute("INSERT INTO sites (url) VALUES ('https://s.com')")
    c.execute("INSERT INTO sites (url) VALUES ('https://baska.com')")
    c.execute("INSERT INTO crawl_runs (site_id, status) VALUES (1, 'completed')")
    c.commit()
    return c


def expect_value_error(fn):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError("ValueError bekleniyordu")


def main():
    c = new_db()

    # boş profil: girilmemiş alanlar boş
    assert get_profile(c, 1) == {"brand_name": "", "example_titles": [],
                                 "example_meta_descriptions": [], "banned_phrases": []}
    print("OK  profil girilmemişse alanlar boş ('' / [])")

    # metin + liste gidiş-dönüş; boşluk düzeltme, boş ve tekrar eden elemanları eleme (sıra korunur)
    set_value(c, 1, "brand_name", "  Düşes   Butik ")
    set_value(c, 1, "banned_phrases", ["en ucuz", "  Bedava ", "", "bedava", "outlet"])
    c.commit()
    p = get_profile(c, 1)
    assert p["brand_name"] == "Düşes Butik"
    assert p["banned_phrases"] == ["en ucuz", "Bedava", "outlet"], p["banned_phrases"]
    stored = c.execute("SELECT value FROM site_profile WHERE key = 'banned_phrases'").fetchone()[0]
    assert json.loads(stored) == p["banned_phrases"]
    print("OK  metin/liste kaydı: boşluklar düzeldi, boş ve tekrar eden elemanlar elendi, Türkçe karakter korundu")

    # aynı anahtar yeniden yazılırsa güncellenir (tek satır kalır), kaynak da güncellenir
    set_value(c, 1, "brand_name", "Yeni Marka", source="auto")
    c.commit()
    assert get_profile(c, 1)["brand_name"] == "Yeni Marka"
    assert c.execute("SELECT COUNT(*), MAX(source) FROM site_profile WHERE key = 'brand_name'").fetchone() == (1, "auto")
    print("OK  aynı anahtar tekrar yazılınca güncellendi (tek satır)")

    # hatalı kullanım
    expect_value_error(lambda: set_value(c, 1, "tone", "samimi"))                  # bilinmeyen anahtar
    expect_value_error(lambda: set_value(c, 1, "banned_phrases", "tek metin"))      # liste yerine metin
    expect_value_error(lambda: import_profile(c, "https://s.com", {"tone": "x"}))
    print("OK  bilinmeyen anahtar ve liste yerine metin reddedildi")

    # siteler birbirinden bağımsız; tarama -> site profili
    set_value(c, 2, "brand_name", "Baska")
    c.commit()
    assert get_profile(c, 1)["brand_name"] == "Yeni Marka" and get_profile(c, 2)["brand_name"] == "Baska"
    assert profile_for_run(c, 1)["brand_name"] == "Yeni Marka"
    assert profile_for_run(c, 999)["brand_name"] == ""      # olmayan tarama: boş profil, hata yok
    print("OK  profiller siteye özgü; tarama numarasından site profiline ulaşılıyor")

    # içe aktarma: yeni site oluşur, uyarılar döner (engellemez)
    data = {
        "brand_name": "Düşes Butik",
        "example_titles": ["İthal Kadın Giyim & Yeni Sezon Koleksiyonu | Düşes Butik",   # 56 karakter
                           "Şık Kadın Bluz Modelleri | Düşes Butik"],
        "example_meta_descriptions": ["Kısa meta", "Düşes Butik'te garantili alışveriş ve şık kadın giyim modellerini keşfedin, hemen inceleyin."],
        "banned_phrases": ["garantili"],
    }
    site_id, warnings = import_profile(c, "https://yeni.com", data)
    assert c.execute("SELECT url FROM sites WHERE id = ?", (site_id,)).fetchone()[0] == "https://yeni.com"
    joined = " | ".join(warnings)
    assert "56 karakter" in joined and "Kısa meta" in joined and "yasaklı ifade 'garantili'" in joined, warnings
    assert get_profile(c, site_id)["example_titles"][0].startswith("İthal Kadın Giyim")   # uyarı kaydı engellemedi
    assert import_profile(c, "https://yeni.com", {"brand_name": "Düşes Butik"})[0] == site_id   # aynı site tekrar kullanılır
    assert check_profile(get_profile(c, site_id), LIMITS) == warnings
    print("OK  içe aktarma: site oluştu, uzunluk ve yasaklı-ifade çelişkisi uyarıldı, kayıt engellenmedi")

    c.close()
    print("\nTüm site profili testleri geçti.")


if __name__ == "__main__":
    main()