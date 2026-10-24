import os
import sqlite3
import tempfile

from database import MIGRATIONS, get_connection, get_schema_version, init_db


def expect_error(connection, sql, params=()):
    """Verilen SQL'in IntegrityError ile reddedilmesini bekler."""
    try:
        connection.execute(sql, params)
    except sqlite3.IntegrityError as e:
        return str(e)
    raise AssertionError(f"Hata bekleniyordu ama kabul edildi: {sql}")


def main():
    # Gerçek veritabanına dokunmamak için geçici dosya kullan
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    init_db(db_path)
    init_db(db_path)  # ikinci çağrı hata vermemeli (IF NOT EXISTS)

    conn = get_connection(db_path)

    # --- Zinciri kur: site -> crawl_run -> page -> issue -> ai_report -> applied_change
    conn.execute("INSERT INTO sites (url) VALUES ('https://example.com')")
    conn.execute("INSERT INTO crawl_runs (site_id, status) VALUES (1, 'completed')")
    conn.execute(
        "INSERT INTO pages (crawl_run_id, url, status_code, title, h1_count, image_count, images_missing_alt) "
        "VALUES (1, 'https://example.com/a', 200, 'Kısa', 0, 3, 2)"
    )
    conn.execute(
        "INSERT INTO pages (crawl_run_id, url, status_code, error_message) "
        "VALUES (1, 'https://example.com/b', NULL, 'Timeout')"
    )
    conn.execute("UPDATE pages SET content_text = 'Örnek sayfa metni' WHERE id = 1")
    conn.execute(
        "INSERT INTO issues (page_id, code, category, severity, message) "
        "VALUES (1, 'H1_MISSING', 'on-page', 'critical', 'Sayfada <h1> yok')"
    )
    conn.execute(
        "INSERT INTO issues (page_id, code, category, severity, message) "
        "VALUES (1, 'TITLE_SHORT', 'on-page', 'warning', 'Title çok kısa')"
    )
    conn.execute(
        "INSERT INTO ai_reports (crawl_run_id, page_id, model, recommendations) "
        "VALUES (1, 1, 'test-model', 'Title genişletilmeli')"
    )
    conn.execute(
        "INSERT INTO applied_changes (page_id, issue_id, ai_report_id, change_type, old_value, new_value) "
        "VALUES (1, 2, 1, 'title', 'Kısa', 'Daha açıklayıcı bir başlık')"
    )
    conn.commit()
    print("OK  zincir oluşturuldu")

    # --- JOIN ile ilişkiler çalışıyor mu?
    rows = conn.execute(
        """
        SELECT s.url, p.url, i.code, i.severity
        FROM issues i
        JOIN pages p ON p.id = i.page_id
        JOIN crawl_runs r ON r.id = p.crawl_run_id
        JOIN sites s ON s.id = r.site_id
        ORDER BY i.id
        """
    ).fetchall()
    assert rows == [
        ("https://example.com", "https://example.com/a", "H1_MISSING", "critical"),
        ("https://example.com", "https://example.com/a", "TITLE_SHORT", "warning"),
    ], rows
    print("OK  JOIN sorgusu doğru sonuç verdi")

    linked = conn.execute(
        "SELECT i.code FROM applied_changes c JOIN issues i ON i.id = c.issue_id"
    ).fetchall()
    assert linked == [("TITLE_SHORT",)], linked
    print("OK  değişiklik -> çözdüğü sorun bağı çalışıyor")

    err = conn.execute("SELECT error_message FROM pages WHERE id = 2").fetchone()[0]
    assert err == "Timeout"
    print("OK  ulaşılamayan sayfa hata mesajıyla kaydedildi")

    # --- Kısıtlar
    expect_error(conn, "INSERT INTO pages (crawl_run_id, url) VALUES (999, 'x')")
    print("OK  var olmayan crawl_run_id reddedildi (foreign key)")

    expect_error(conn, "INSERT INTO pages (crawl_run_id, url) VALUES (1, 'https://example.com/a')")
    print("OK  aynı taramada aynı URL reddedildi (UNIQUE)")

    expect_error(
        conn,
        "INSERT INTO issues (page_id, code, category, severity, message) VALUES (1, 'X', 'c', 'urgent', 'm')",
    )
    print("OK  geçersiz severity reddedildi (CHECK)")

    expect_error(
        conn,
        "INSERT INTO applied_changes (page_id, change_type, status) VALUES (1, 'title', 'done')",
    )
    print("OK  geçersiz değişiklik durumu reddedildi (CHECK)")

    # --- Silme davranışları
    # applied_changes kaydı olan sayfa silinemez (denetim izi korunur)
    expect_error(conn, "DELETE FROM pages WHERE id = 1")
    print("OK  değişiklik kaydı olan sayfa silinemedi (RESTRICT)")

    # ai_report silinirse applied_changes kalır, bağ NULL olur
    conn.execute("DELETE FROM ai_reports WHERE id = 1")
    ref = conn.execute("SELECT ai_report_id FROM applied_changes WHERE id = 1").fetchone()[0]
    assert ref is None
    print("OK  ai_report silinince applied_changes korundu (SET NULL)")

    # issue silinirse değişiklik kaydı kalır, issue_id NULL olur
    conn.execute("DELETE FROM issues WHERE id = 2")
    ref = conn.execute("SELECT issue_id FROM applied_changes WHERE id = 1").fetchone()[0]
    assert ref is None
    print("OK  issue silinince applied_changes korundu (SET NULL)")

    # Değişiklik kaydı yoksa sayfa silinince issues da silinir
    conn.execute("DELETE FROM applied_changes")
    conn.execute("DELETE FROM pages WHERE id = 1")
    left = conn.execute("SELECT COUNT(*) FROM issues WHERE page_id = 1").fetchone()[0]
    assert left == 0
    print("OK  sayfa silinince issues da silindi (CASCADE)")

    conn.close()

    # --- Şema sürümü
    check = get_connection(db_path)
    assert get_schema_version(check) == len(MIGRATIONS)
    check.close()
    print("OK  şema sürümü güncel, tekrar init_db() sürümü bozmadı")

    # --- Eski tarz veritabanı (yalnızca sites + crawl_runs, sürüm 0) yükseltilebiliyor mu?
    old_path = os.path.join(tempfile.mkdtemp(), "old.db")
    old = sqlite3.connect(old_path)
    old.executescript(
        """
        CREATE TABLE sites (id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE crawl_runs (id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, ended_at TIMESTAMP,
                   status TEXT NOT NULL, pages_crawled INTEGER DEFAULT 0, report_path TEXT,
                   FOREIGN KEY (site_id) REFERENCES sites (id));
        INSERT INTO sites (url) VALUES ('https://eski-site.com');
        INSERT INTO crawl_runs (site_id, status) VALUES (1, 'completed');
        """
    )
    old.commit()
    old.close()
    init_db(old_path)
    up = get_connection(old_path)
    assert up.execute("SELECT url FROM sites").fetchall() == [("https://eski-site.com",)]
    assert up.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()[0] == 1
    tables = {r[0] for r in up.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"pages", "issues", "ai_reports", "applied_changes", "site_profile"} <= tables
    assert get_schema_version(up) == len(MIGRATIONS)
    up.close()
    print("OK  eski veritabanı yükseltildi, mevcut veriler korundu")

    # --- Gerçekte yaşanan durum: sürümsüz ilk şemayla kurulmuş, pages'i eski bir veritabanı
    stale_path = os.path.join(tempfile.mkdtemp(), "stale.db")
    stale = sqlite3.connect(stale_path)
    stale.executescript(
        """
        CREATE TABLE sites (id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE crawl_runs (id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, ended_at TIMESTAMP,
                   status TEXT NOT NULL, pages_crawled INTEGER DEFAULT 0, report_path TEXT,
                   FOREIGN KEY (site_id) REFERENCES sites (id));
        CREATE TABLE pages (id INTEGER PRIMARY KEY, crawl_run_id INTEGER NOT NULL, url TEXT NOT NULL,
                   status_code INTEGER, title TEXT, meta_description TEXT, h1_count INTEGER,
                   h1_text TEXT, image_count INTEGER, images_missing_alt INTEGER, extra_data TEXT,
                   crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE (crawl_run_id, url),
                   FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs (id) ON DELETE CASCADE);
        CREATE TABLE issues (id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL, code TEXT NOT NULL,
                   category TEXT NOT NULL, severity TEXT NOT NULL, message TEXT NOT NULL,
                   status TEXT NOT NULL DEFAULT 'open', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   resolved_at TIMESTAMP, FOREIGN KEY (page_id) REFERENCES pages (id) ON DELETE CASCADE);
        CREATE TABLE ai_reports (id INTEGER PRIMARY KEY, crawl_run_id INTEGER NOT NULL, page_id INTEGER,
                   model TEXT NOT NULL, recommendations TEXT, report_content TEXT,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs (id) ON DELETE CASCADE,
                   FOREIGN KEY (page_id) REFERENCES pages (id) ON DELETE SET NULL);
        CREATE TABLE applied_changes (id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL,
                   ai_report_id INTEGER, change_type TEXT NOT NULL, old_value TEXT, new_value TEXT,
                   status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   applied_at TIMESTAMP, FOREIGN KEY (page_id) REFERENCES pages (id),
                   FOREIGN KEY (ai_report_id) REFERENCES ai_reports (id) ON DELETE SET NULL);
        INSERT INTO sites (url) VALUES ('https://eski.com');
        INSERT INTO crawl_runs (site_id, status) VALUES (1, 'completed');
        INSERT INTO pages (crawl_run_id, url, title) VALUES (1, 'https://eski.com/', 'Eski kayıt');
        PRAGMA user_version = 1;
        """
    )
    stale.commit()
    stale.close()
    init_db(stale_path)
    fixed = get_connection(stale_path)
    assert {"error_message", "content_text"} <= {r[1] for r in fixed.execute("PRAGMA table_info(pages)")}
    assert "issue_id" in {r[1] for r in fixed.execute("PRAGMA table_info(applied_changes)")}
    assert fixed.execute("SELECT title FROM pages").fetchall() == [("Eski kayıt",)]
    fixed.close()
    print("OK  sürümü 1 işaretli ama sütunları eksik veritabanı onarıldı, veri korundu")

    # --- verify_schema: sürüm güncel görünse bile eksik sütun varsa net hata verir
    if sqlite3.sqlite_version_info >= (3, 35, 0):  # DROP COLUMN bu sürümden itibaren var
        broken_path = os.path.join(tempfile.mkdtemp(), "broken.db")
        init_db(broken_path)
        b = sqlite3.connect(broken_path)
        b.execute("ALTER TABLE pages DROP COLUMN content_text")
        b.commit()
        b.close()
        try:
            init_db(broken_path)
        except RuntimeError as e:
            assert "pages.content_text" in str(e), e
        else:
            raise AssertionError("Eksik sütun fark edilmedi")
        print("OK  eksik sütun taramadan ÖNCE açık bir hata mesajıyla yakalanıyor")

    # --- site_profile: kısıtlar ve silme davranışı
    prof = get_connection(db_path)
    prof.execute("INSERT INTO sites (url) VALUES ('https://profil.com')")
    site_id = prof.execute("SELECT id FROM sites WHERE url = 'https://profil.com'").fetchone()[0]
    prof.execute("INSERT INTO site_profile (site_id, key, value) VALUES (?, 'brand_name', 'Marka')", (site_id,))
    expect_error(prof, "INSERT INTO site_profile (site_id, key, value) VALUES (?, 'brand_name', 'Yinelenen')", (site_id,))
    expect_error(prof, "INSERT INTO site_profile (site_id, key, value) VALUES (999, 'brand_name', 'x')")
    expect_error(prof, "INSERT INTO site_profile (site_id, key, value, source) VALUES (?, 'k', 'v', 'model')", (site_id,))
    print("OK  site_profile: aynı anahtar tekrar yazılamaz, var olmayan site ve geçersiz kaynak reddedildi")
    prof.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    assert prof.execute("SELECT COUNT(*) FROM site_profile WHERE site_id = ?", (site_id,)).fetchone()[0] == 0
    print("OK  site silinince profili de silindi (CASCADE)")
    prof.close()

    print("\nTüm testler geçti.")


if __name__ == "__main__":
    main()