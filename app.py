"""
Yerel inceleme arayüzü (Flask). Yalnızca bu bilgisayardan erişilir; canlı siteye hiçbir şey göndermez.

Çalıştırma:
    python app.py        ->  http://127.0.0.1:5000
"""
from flask import Flask, render_template

from database import DB_PATH, get_connection, init_db
from review import length_warning, list_pending

FIELD_NAMES = {"title": "Başlık", "meta_description": "Meta description"}


def create_app(db_path=DB_PATH):
    app = Flask(__name__)
    init_db(db_path)

    @app.route("/")
    def pending_list():
        conn = get_connection(db_path)
        try:
            items = list_pending(conn)
        finally:
            conn.close()
        for item in items:
            item["field_name"] = FIELD_NAMES.get(item["field"], item["field"])
            item["warning"] = length_warning(item["field"], item["new"])
        return render_template("list.html", items=items)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000)