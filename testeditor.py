"""editor.py testleri. Gerçek model YOK: sahte sohbet fonksiyonu kullanılır."""
import json
import os
import tempfile

from database import get_connection, init_db
from editor import get_change, propose_merged, save_draft
from review import approve


def build_db(field="title", new="Kürklü Ekose Mont 9009 | Marka", status="pending"):
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(path)
    c = get_connection(path)
    c.execute("INSERT INTO sites (url) VALUES ('https://s.com')")
    c.execute("INSERT INTO crawl_runs (site_id, status) VALUES (1, 'completed')")
    c.execute("INSERT INTO pages (crawl_run_id, url, status_code, title, h1_text) "
              "VALUES (1, 'https://s.com/urun/x', 200, 'Mont', 'KÜRKLÜ EKOSE MONT 9009 STD - Lacivert')")
    c.execute("INSERT INTO ai_reports (crawl_run_id, page_id, model, recommendations) VALUES (1, 1, 'm', '[]')")
    c.execute("INSERT INTO applied_changes (page_id, ai_report_id, change_type, old_value, new_value, status) "
              "VALUES (1, 1, ?, 'Mont', ?, ?)", (field, new, status))
    c.commit()
    return c


class Fake:
    def __init__(self, *answers):
        self.answers, self.users = list(answers), []

    def __call__(self, model, messages, schema):
        self.users.append(messages[-1]["content"])
        return self.answers[min(len(self.users) - 1, len(self.answers) - 1)]


def j(value, reason="Gerekçe."):
    return json.dumps({"value": value, "reason": reason}, ensure_ascii=False)


def expect_value_error(fn):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError("ValueError bekleniyordu")


def main():
    c = build_db()
    before = c.total_changes
    chat = Fake(j("İthal Kürklü Ekose Mont 9009"))
    result, notes = propose_merged(c, 1, "ithal olsun", chat=chat, brand="Marka")
    assert result["merged"] == "İthal Kürklü Ekose Mont 9009 | Marka", result
    assert result["ai_suggestion"].endswith("| Marka") and result["current"] == "Mont"
    assert "AI önerisi: Kürklü Ekose Mont 9009\n" in chat.users[0], chat.users[0]  # marka eki modele gitmedi
    assert c.total_changes == before, "propose_merged veritabanına yazmamalı"
    print("OK  ortak metin üretildi; marka kodla eklendi; veritabanına yazılmadı")

    chat = Fake(j("İthal Kürklü Ekose Mont 9009 şimdi 7990 TL"), j("İthal Kürklü Ekose Mont 9009"))
    result, notes = propose_merged(c, 1, "ithal olsun", chat=chat, brand="Marka")
    assert result and len(notes) == 1 and "sayı" in notes[0]
    assert "Önceki öneri reddedildi" in chat.users[1] and "sayı" in chat.users[1]
    chat = Fake(j("İthal Kürklü Mont 9009 - Marka"))
    result, notes = propose_merged(c, 1, "Mont 9009 | Marka", chat=chat, brand="Marka")
    assert result["merged"] == "İthal Kürklü Mont 9009 | Marka" and notes == [] and len(chat.users) == 1, (result, notes)
    print("OK  model markayı yazsa bile temizlenir, tek '| Marka' kalır (yeniden deneme gerekmez)")

    print("OK  uydurma sayı reddedildi, ret nedeni 2. denemede modele iletildi")

    result, _ = propose_merged(c, 1, "indirim yazsın", chat=Fake(j("Kürklü Ekose Mont 9009 indirim")), brand=None)
    assert result and "indirim" in result["merged"]
    print("OK  kullanıcının kendi yazdığı ifade ('indirim') yasak sayılmadı")

    result, notes = propose_merged(c, 1, "ithal olsun", chat=Fake("json değil", j("Mont")))
    assert result is None and len(notes) == 2, notes
    print("OK  geçersiz JSON / kısa yanıt: sonuç None, nedenler notlarda")

    # --- site profili: marka profilden gelir, yasaklı ifadeler uygulanır, kullanıcı isteği önceliklidir
    from site_profile import import_profile
    pc = build_db()
    import_profile(pc, "https://s.com", {"brand_name": "Marka", "banned_phrases": ["garantili"]})
    result, _ = propose_merged(pc, 1, "ithal olsun", chat=Fake(j("İthal Kürklü Ekose Mont 9009")))  # brand verilmedi
    assert result["merged"] == "İthal Kürklü Ekose Mont 9009 | Marka", result
    result, notes = propose_merged(pc, 1, "ithal olsun", chat=Fake(j("Garantili Ekose Mont 9009 Modeli")))
    assert result is None and "kullanılmak istenmeyen" in notes[0], (result, notes)
    result, notes = propose_merged(pc, 1, "garantili olsun", chat=Fake(j("Garantili Ekose Mont 9009 Modeli")))
    assert result and "Garantili" in result["merged"], (result, notes)
    print("OK  profil: marka profilden alındı, yasaklı ifade reddedildi, kullanıcı özellikle istediyse serbest")

    expect_value_error(lambda: propose_merged(c, 1, "   ", chat=Fake(j("x"))))
    expect_value_error(lambda: propose_merged(c, 99, "x", chat=Fake(j("x"))))
    expect_value_error(lambda: propose_merged(build_db(status="approved"), 1, "x", chat=Fake(j("x"))))
    expect_value_error(lambda: propose_merged(build_db(field="h1"), 1, "x", chat=Fake(j("x"))))
    assert get_change(c, 1)["status"] == "pending"
    print("OK  boş istek, olmayan kayıt, bekleyen olmayan kayıt, desteklenmeyen alan (h1) reddedildi")
    c = build_db()
    assert save_draft(c, 1, "  İthal Kürklü   Ekose Mont 9009 | Marka ") is None
    g = get_change(c, 1)
    assert g["suggestion"] == "İthal Kürklü Ekose Mont 9009 | Marka" and g["status"] == "pending", g
    assert "uyarı" in save_draft(c, 1, "Kısa")
    save_draft(c, 1, "Son Metin Burada Yeterince Uzun")
    approve(c, 1)  # new_value verilmeden onay: kaydedilen taslak onaylanır
    g = get_change(c, 1)
    assert g["status"] == "approved" and g["suggestion"] == "Son Metin Burada Yeterince Uzun", g
    expect_value_error(lambda: save_draft(c, 1, "Onaylıya yazılamaz"))
    expect_value_error(lambda: save_draft(build_db(), 1, "   "))
    expect_value_error(lambda: save_draft(build_db(), 99, "x"))
    print("OK  taslak kaydedildi (pending kaldı), uzunluk uyarısı döndü, review.approve taslağı onayladı, onaylı kayda yazılamadı")
    print("\nTüm editor testleri geçti.")


if __name__ == "__main__":
    main()