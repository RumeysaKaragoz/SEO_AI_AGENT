"""apply_changes.py testleri. Sahte Publisher kullanılır; siteye hiçbir istek gitmez."""
import os
import tempfile

from apply_changes import (Publisher, PublishError, apply_change, export_csv, list_unapplied, mark_applied, reopen)
from database import get_connection, init_db


def build_db():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(path)
    c = get_connection(path)
    c.execute("INSERT INTO sites (url) VALUES ('https://s.com')")
    c.execute("INSERT INTO crawl_runs (site_id, status) VALUES (1, 'completed')")
    c.execute("INSERT INTO pages (crawl_run_id, url, status_code) VALUES (1, 'https://s.com/urun/x', 200)")
    for field, old, new, status in [
        ("title", "Mont", "Kürklü Mont 9009 | Marka", "approved"),
        ("meta_description", None, "=HYPERLINK(\"http://x\")", "approved"),
        ("title", "Eski", "Bekleyen Başlık Metni", "pending"),
        ("title", "Eski", "Reddedilen Başlık Metni", "rejected"),
    ]:
        c.execute("INSERT INTO applied_changes (page_id, change_type, old_value, new_value, status) "
                  "VALUES (1, ?, ?, ?, ?)", (field, old, new, status))
    c.commit()
    return c


def row(c, i):
    return c.execute("SELECT status, applied_at FROM applied_changes WHERE id = ?", (i,)).fetchone()


def expect_value_error(fn):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError("ValueError bekleniyordu")


class Recorder(Publisher):
    def __init__(self, fail_times=0):
        self.calls, self.fail_times = [], fail_times

    def apply(self, change):
        self.calls.append(change["id"])
        if len(self.calls) <= self.fail_times:
            raise PublishError("Site yanıt vermedi")


def main():
    c = build_db()
    assert [x["id"] for x in list_unapplied(c)] == [1, 2]
    print("OK  yalnızca uygulanmamış onaylı kayıtlar listelendi")

    text = export_csv(list_unapplied(c))
    lines = text.splitlines()
    assert lines[0] == "No;Sayfa;Alan;Eski metin;Yeni metin;Yeni karakter", lines[0]
    assert "Kürklü Mont 9009 | Marka" in lines[1] and "sayfa başlığı" in lines[1]
    assert "'=HYPERLINK" in text and ";=HYPERLINK" not in text
    print("OK  CSV: başlık satırı, Türkçe alan adı, formül olarak çalışabilecek hücre etkisizleştirildi")

    mark_applied(c, 1)
    st = row(c, 1)
    assert st[0] == "applied" and st[1] is not None
    expect_value_error(lambda: mark_applied(c, 1))     # zaten uygulandı
    expect_value_error(lambda: mark_applied(c, 3))     # bekliyor
    expect_value_error(lambda: mark_applied(c, 99))
    assert [x["id"] for x in list_unapplied(c)] == [2]
    print("OK  elle işaretleme: applied + zaman damgası; tekrar/bekleyen/olmayan kayıt reddedildi")

    pub = Recorder(fail_times=1)
    r = apply_change(c, 2, pub)
    assert r == {"id": 2, "status": "failed", "error": "Site yanıt vermedi"} and row(c, 2)[0] == "failed"
    assert 2 in [x["id"] for x in list_unapplied(c)]    # başarısız kayıt listede kalır (yeniden denenebilir)
    r = apply_change(c, 2, pub)                         # başarısız kayıt yeniden denenebilir
    assert r["status"] == "applied" and row(c, 2)[0] == "applied" and pub.calls == [2, 2]
    print("OK  publisher: başarısızlıkta 'failed', yeniden denemede 'applied'")

    pub = Recorder()
    expect_value_error(lambda: apply_change(c, 3, pub))  # pending: siteye gitmemeli
    expect_value_error(lambda: apply_change(c, 4, pub))  # rejected
    expect_value_error(lambda: apply_change(c, 1, pub))  # zaten applied
    assert pub.calls == [], pub.calls
    print("OK  bekleyen / reddedilen / uygulanmış kayıtlar için publisher hiç çağrılmadı")
    c = build_db()
    reopen(c, 1)
    assert row(c, 1)[0] == "pending" and row(c, 1)[1] is None
    expect_value_error(lambda: reopen(c, 1))     # artık bekliyor
    expect_value_error(lambda: reopen(c, 3))     # bekliyor
    mark_applied(c, 2)
    expect_value_error(lambda: reopen(c, 2))     # uygulanmış: geri alınamaz
    assert row(c, 2)[0] == "applied"
    print("OK  beklemeye geri alma: approved -> pending; bekleyen/uygulanmış kayıt reddedildi")
    print("\nTüm uygulama katmanı testleri geçti.")


if __name__ == "__main__":
    main()
