import sqlite3

DB_PATH = "seo_agent.db"


def get_connection(db_path=DB_PATH):
    """Her bağlantıda foreign key kontrolünü açar (SQLite'ta varsayılan KAPALI)."""
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    status TEXT NOT NULL,
    pages_crawled INTEGER DEFAULT 0,
    report_path TEXT,
    FOREIGN KEY (site_id) REFERENCES sites (id)
);

-- Sayfadan çıkarılan HAM veriler. SEO kuralının sonucu burada değil, issues'ta tutulur.
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    crawl_run_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    status_code INTEGER,                 -- 200, 404, ... (istek hiç tamamlanmadıysa NULL)
    error_message TEXT,                  -- ulaşılamayan sayfalarda hata nedeni (timeout vb.)
    title TEXT,
    meta_description TEXT,
    h1_count INTEGER,
    h1_text TEXT,                        -- ilk H1'in metni
    image_count INTEGER,
    images_missing_alt INTEGER,
    content_text TEXT,                   -- sayfanın görünen metni (AI önerileri için, kısaltılmış)
    extra_data TEXT,                     -- ileride eklenecek ham veriler için JSON
    crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (crawl_run_id, url),
    FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs (id) ON DELETE CASCADE
);

-- Kural sonuçları: her bulgu bir satır. Yeni kural eklemek şema değişikliği gerektirmez.
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL,
    code TEXT NOT NULL,                  -- örn. TITLE_MISSING, H1_MULTIPLE
    category TEXT NOT NULL,              -- örn. on-page, technical, content
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'warning', 'info')),
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'fixed', 'ignored')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    FOREIGN KEY (page_id) REFERENCES pages (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_reports (
    id INTEGER PRIMARY KEY,
    crawl_run_id INTEGER NOT NULL,
    page_id INTEGER,                     -- NULL ise rapor tüm taramayı kapsar
    model TEXT NOT NULL,
    recommendations TEXT,                -- öneriler (metin veya JSON)
    report_content TEXT,                 -- tam rapor metni
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (page_id) REFERENCES pages (id) ON DELETE SET NULL
);

-- Onay ve uygulama kaydı (denetim izi): silme zinciriyle yok olmasın diye CASCADE yok.
CREATE TABLE IF NOT EXISTS applied_changes (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL,
    issue_id INTEGER,                    -- hangi sorunu çözmek için yapıldı (doğrulama için)
    ai_report_id INTEGER,                -- NULL ise değişiklik manuel
    change_type TEXT NOT NULL,           -- örn. title, meta_description
    old_value TEXT,
    new_value TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'applied', 'failed', 'reverted')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    applied_at TIMESTAMP,
    FOREIGN KEY (page_id) REFERENCES pages (id),
    FOREIGN KEY (issue_id) REFERENCES issues (id) ON DELETE SET NULL,
    FOREIGN KEY (ai_report_id) REFERENCES ai_reports (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_pages_crawl_run ON pages (crawl_run_id);
CREATE INDEX IF NOT EXISTS idx_issues_page ON issues (page_id);
CREATE INDEX IF NOT EXISTS idx_issues_code ON issues (code);
CREATE INDEX IF NOT EXISTS idx_ai_reports_run ON ai_reports (crawl_run_id);
CREATE INDEX IF NOT EXISTS idx_changes_page ON applied_changes (page_id);
"""


def _add_column_if_missing(connection, table, column, definition):
    """ALTER TABLE ADD COLUMN SQLite'ta 'IF NOT EXISTS' desteklemez; önce kontrol ederiz."""
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migration_2(connection):
    """
    Sürüm takibi yokken oluşturulmuş veritabanlarında pages / applied_changes eski kalmış
    olabilir (CREATE TABLE IF NOT EXISTS var olan tabloyu değiştirmez). Eksik sütunları ekler.
    Yeni kurulumlarda sütunlar zaten vardır, bu adım hiçbir şey yapmaz.
    """
    _add_column_if_missing(connection, "pages", "error_message", "TEXT")
    _add_column_if_missing(connection, "pages", "content_text", "TEXT")
    _add_column_if_missing(
        connection, "applied_changes", "issue_id",
        "INTEGER REFERENCES issues (id) ON DELETE SET NULL",
    )


# Sürüm 3: site_profile — sitenin sahibinin girdiği kalıcı bilgiler (marka adı, beğenilen örnek
# metinler, kullanılmak istenmeyen ifadeler). Site başına anahtar-değer; liste değerleri JSON metnidir.
SITE_PROFILE_MIGRATION = """
CREATE TABLE IF NOT EXISTS site_profile (
    site_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user', 'auto')),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (site_id, key),
    FOREIGN KEY (site_id) REFERENCES sites (id) ON DELETE CASCADE
);
"""


# Şema sürümleri. Liste sırası = sürüm numarası (MIGRATIONS[0] -> sürüm 1).
# Şemayı değiştirmek için ESKİ girişlere dokunma; listenin sonuna yeni bir giriş ekle.
# Giriş bir SQL metni ya da connection alan bir fonksiyon olabilir. Yeni kurulum için
# SCHEMA'yı da güncel tut; eski veritabanlarını migration yükseltir.
MIGRATIONS = [
    SCHEMA,       # sürüm 1: ilk tam şema
    _migration_2,  # sürüm 2: eski veritabanlarında eksik sütunları tamamla
    SITE_PROFILE_MIGRATION,  # sürüm 3: site_profile tablosu
]


def get_schema_version(connection):
    return connection.execute("PRAGMA user_version").fetchone()[0]


def _columns_by_table(connection):
    tables = [r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {t: {r[1] for r in connection.execute(f"PRAGMA table_info({t})")} for t in tables}


def verify_schema(connection):
    """
    Veritabanını, migration'lardan sıfırdan kurulan referans şemayla karşılaştırır.
    Eksik tablo/sütun varsa uzun bir tarama başlamadan açık bir hata verir.
    """
    reference = sqlite3.connect(":memory:")
    try:
        _apply_migrations(reference, 0, quiet=True)
        expected = _columns_by_table(reference)
    finally:
        reference.close()
    actual = _columns_by_table(connection)

    problems = []
    for table, columns in expected.items():
        if table not in actual:
            problems.append(f"tablo eksik: {table}")
        else:
            for column in sorted(columns - actual[table]):
                problems.append(f"sütun eksik: {table}.{column}")
    if problems:
        raise RuntimeError("Veritabanı şeması beklenenle uyuşmuyor: " + ", ".join(problems))


def _apply_migrations(connection, current, quiet=False):
    for version in range(current + 1, len(MIGRATIONS) + 1):
        migration = MIGRATIONS[version - 1]
        if callable(migration):
            migration(connection)
        else:
            connection.executescript(migration)
        connection.execute(f"PRAGMA user_version = {version}")  # PRAGMA parametre almaz
        connection.commit()
        if not quiet:
            print(f"Şema sürüm {version} uygulandı.")


def init_db(db_path=DB_PATH):
    """Veritabanını oluşturur / eksik şema sürümlerini uygular ve sonucu doğrular."""
    connection = get_connection(db_path)
    try:
        current = get_schema_version(connection)
        if current > len(MIGRATIONS):
            raise RuntimeError(
                f"Veritabanı sürümü ({current}) koddan yeni ({len(MIGRATIONS)}). Kodu güncelleyin."
            )
        _apply_migrations(connection, current)
        if current == len(MIGRATIONS):
            print("Veritabanı güncel.")
        verify_schema(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    init_db()