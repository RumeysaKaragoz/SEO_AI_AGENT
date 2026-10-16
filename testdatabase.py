import os
import sqlite3
import tempfile

from database import get_connection, init_db


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
        "INSERT INTO pages (crawl_run_id, url, status_code) VALUES (1, 'https://example.com/b', 404)"
    )
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
        "INSERT INTO applied_changes (page_id, ai_report_id, change_type, old_value, new_value) "
        "VALUES (1, 1, 'title', 'Kısa', 'Daha açıklayıcı bir başlık')"
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

    # Değişiklik kaydı yoksa sayfa silinince issues da silinir
    conn.execute("DELETE FROM applied_changes")
    conn.execute("DELETE FROM pages WHERE id = 1")
    left = conn.execute("SELECT COUNT(*) FROM issues WHERE page_id = 1").fetchone()[0]
    assert left == 0
    print("OK  sayfa silinince issues da silindi (CASCADE)")

    conn.close()
    print("\nTüm testler geçti.")


if __name__ == "__main__":
    main()