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
 

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    crawl_run_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    status_code INTEGER,                 
    error_message TEXT,                  
    title TEXT,
    meta_description TEXT,
    h1_count INTEGER,
    h1_text TEXT,                        
    image_count INTEGER,
    images_missing_alt INTEGER,
    content_text TEXT,                  
    extra_data TEXT,                     
    crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (crawl_run_id, url),
    FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs (id) ON DELETE CASCADE
);
 
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL,
    code TEXT NOT NULL,                  
    category TEXT NOT NULL,              
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
    page_id INTEGER,                     
    model TEXT NOT NULL,
    recommendations TEXT,                
    report_content TEXT,                 
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (page_id) REFERENCES pages (id) ON DELETE SET NULL
);
 
CREATE TABLE IF NOT EXISTS applied_changes (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL,
    issue_id INTEGER,                    
    ai_report_id INTEGER,                
    change_type TEXT NOT NULL,           
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

MIGRATIONS = [
    SCHEMA,  
]
 
 
def get_schema_version(connection):
    return connection.execute("PRAGMA user_version").fetchone()[0]
 
 
def init_db(db_path=DB_PATH):
    connection = get_connection(db_path)
    try:
        current = get_schema_version(connection)
        if current > len(MIGRATIONS):
            raise RuntimeError(
                f"Veritabanı sürümü ({current}) koddan yeni ({len(MIGRATIONS)}). Kodu güncelleyin."
            )
        for version in range(current + 1, len(MIGRATIONS) + 1):
            connection.executescript(MIGRATIONS[version - 1])
            connection.execute(f"PRAGMA user_version = {version}")  
            connection.commit()
            print(f"Şema sürüm {version} uygulandı.")
        if current == len(MIGRATIONS):
            print("Veritabanı güncel.")
    finally:
        connection.close()
 
 
if __name__ == "__main__":
    init_db()