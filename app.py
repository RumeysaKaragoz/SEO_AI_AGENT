"""
Yerel inceleme arayüzü (Flask). Yalnızca bu bilgisayardan erişilir; canlı siteye hiçbir şey göndermez.

Çalıştırma:
    python app.py --brand "Marka Adı"     ->  http://127.0.0.1:5000
"""
import argparse
import secrets
from editor.editor import get_change, propose_merged, save_draft
from flask import Flask, abort, g, redirect, render_template, request, url_for

from ai import DEFAULT_MODEL, LIMITS, ollama_chat
from database import DB_PATH, get_connection, init_db
from editor import get_change, propose_merged, save_draft
from review import approve, length_warning, list_pending, reject

FIELD_NAMES = {"title": "Başlık", "meta_description": "Meta description"}
MESSAGES = {"saved": "Taslak kaydedildi."}


def create_app(db_path=DB_PATH, brand=None, model=DEFAULT_MODEL, chat=ollama_chat):
    app = Flask(__name__)
    init_db(db_path)
    # Form gönderimlerini doğrulayan, her başlatmada yenilenen anahtar: başka bir sitenin
    # bu yerel adrese gizlice onay/ret isteği göndermesini engeller.
    token = secrets.token_hex(16)
    app.config["CSRF_TOKEN"] = token

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            sent = request.form.get("csrf_token", "")
            if not secrets.compare_digest(sent.encode(), token.encode()):
                abort(400, "Geçersiz istek (csrf_token)")

    @app.context_processor
    def inject_token():
        return {"csrf_token": token}

    def get_db():
        if "db" not in g:
            g.db = get_connection(db_path)
        return g.db

    @app.teardown_appcontext
    def close_db(exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    def load(change_id):
        try:
            return get_change(get_db(), change_id)
        except ValueError:
            abort(404)

    def render(change_id, status=200, **ctx):
        change = load(change_id)
        if not ctx.get("final_text"):
            ctx["final_text"] = change["suggestion"]
        return render_template("change.html", change=change,
                               field_name=FIELD_NAMES.get(change["field"], change["field"]),
                               limits=LIMITS.get(change["field"]), **ctx), status

    @app.route("/")
    def pending_list():
        items = list_pending(get_db())
        for item in items:
            item["field_name"] = FIELD_NAMES.get(item["field"], item["field"])
            item["warning"] = length_warning(item["field"], item["new"])
        return render_template("list.html", items=items)

    @app.route("/change/<int:change_id>")
    def change_page(change_id):
        return render(change_id, message=MESSAGES.get(request.args.get("msg")))

    @app.route("/change/<int:change_id>/merge", methods=["POST"])
    def merge(change_id):
        user_text = request.form.get("user_text", "")
        final_text = request.form.get("final_text", "")
        try:
            result, notes = propose_merged(get_db(), change_id, user_text, model=model, brand=brand, chat=chat)
        except ValueError as e:
            return render(change_id, 400, error=str(e), user_text=user_text, final_text=final_text)
        except RuntimeError as e:  # Ollama kapalı / yavaş / model kurulu değil
            return render(change_id, 502, error=str(e), user_text=user_text, final_text=final_text)
        if result is None:
            return render(change_id, error="Model geçerli bir ortak metin üretemedi.", notes=notes,
                          user_text=user_text, final_text=final_text)
        return render(change_id, user_text=user_text, final_text=result["merged"], merged=result)

    @app.route("/change/<int:change_id>/save", methods=["POST"])
    def save(change_id):
        text = request.form.get("final_text", "")
        try:
            warning = save_draft(get_db(), change_id, text)
        except ValueError as e:
            return render(change_id, 400, error=str(e), final_text=text,
                          user_text=request.form.get("user_text", ""))
        return render(change_id, message=MESSAGES["saved"], warning=warning,
                      user_text=request.form.get("user_text", ""))

    @app.route("/change/<int:change_id>/approve", methods=["POST"])
    def approve_view(change_id):
        change = load(change_id)
        text = " ".join(request.form.get("final_text", "").split())
        warning = length_warning(change["field"], text) if text else None
        if warning and not request.form.get("confirm"):
            return render(change_id, final_text=text, warning=warning, need_confirm=True,
                          user_text=request.form.get("user_text", ""))
        try:
            approve(get_db(), change_id, text)
        except ValueError as e:
            return render(change_id, 400, error=str(e), final_text=text,
                          user_text=request.form.get("user_text", ""))
        return redirect(url_for("pending_list"))

    @app.route("/change/<int:change_id>/reject", methods=["POST"])
    def reject_view(change_id):
        load(change_id)
        try:
            reject(get_db(), change_id)
        except ValueError as e:
            return render(change_id, 400, error=str(e))
        return redirect(url_for("pending_list"))

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEO inceleme arayüzü (yalnızca yerel).")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--brand", default=None, help="Başlıkların sonuna ' | MARKA' eklenir")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    create_app(args.db, brand=args.brand, model=args.model).run(host="127.0.0.1", port=5000)