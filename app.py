"""
Yerel arayüz (Flask). Yalnızca bu bilgisayardan erişilir; canlı siteye yalnızca "WordPress'e uygula" ile yazılır.

Çalıştırma:
    python app.py --brand "Marka Adı"     ->  http://127.0.0.1:5000
Tarama, öneri üretme, inceleme, site profili ve WordPress bağlantısı arayüzden yapılır.
"""
import argparse
import os
import secrets
from urllib.parse import urlparse

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, url_for

from ai import DEFAULT_MODEL, LIMITS, generate_suggestions, ollama_chat
from database import DB_PATH, get_connection, init_db
from editor import get_change, propose_merged, save_draft
from editor.apply_changes import PublishError, apply_change, export_csv, list_unapplied, mark_applied, reopen
from jobs import JobManager
from main import run as crawl_run
from review import approve, length_warning, list_pending, reject
from site_profile import check_profile, get_profile, set_value
from wordpress import WordPressPublisher

FIELD_NAMES = {"title": "Başlık", "meta_description": "Meta description"}
SEVERITIES = {"critical": "Kritik", "warning": "Uyarı", "info": "Bilgi"}
MESSAGES = {"saved": "Taslak kaydedildi."}


def _number(value, kind, low, high, label):
    """Form alanını sayıya çevirir; geçersizse ValueError (mesajı kullanıcıya gösterilir)."""
    try:
        number = kind(str(value).strip().replace(",", "."))
    except ValueError:
        raise ValueError(f"{label} bir sayı olmalı")
    if not low <= number <= high:
        raise ValueError(f"{label} {low} ile {high} arasında olmalı")
    return number


def create_app(db_path=DB_PATH, brand=None, model=DEFAULT_MODEL, chat=ollama_chat, publisher=None,
               reports_dir="reports", jobs=None):
    app = Flask(__name__)
    init_db(db_path)
    jobs = jobs or JobManager()
    wp = {"publisher": publisher,  # WordPress bağlantısı yalnızca bellekte tutulur (parola diske yazılmaz)
          "info": {"url": getattr(publisher, "site", None), "user": getattr(publisher, "auth", ("",))[0]}
          if publisher is not None else None}
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

    @app.after_request
    def secure_headers(response):
        # Bu arayüz hiçbir sitede çerçeve içinde açılamaz (onay düğmelerine tıklatma / clickjacking)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def get_db():
        if "db" not in g:
            g.db = get_connection(db_path)
        return g.db

    @app.teardown_appcontext
    def close_db(exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.context_processor
    def inject_common():
        db = get_db()
        count = lambda where: db.execute(f"SELECT COUNT(*) FROM applied_changes WHERE {where}").fetchone()[0]
        running = jobs.current if jobs.current is not None and jobs.current.state == "running" else None
        return {"csrf_token": token,
                "pending_count": count("status = 'pending'"),
                "unapplied_count": count("status IN ('approved', 'failed')"),
                "active_job": running}

    def load(change_id):
        try:
            return get_change(get_db(), change_id)
        except ValueError:
            abort(404)

    def render(change_id, status=200, **ctx):
        change = load(change_id)
        if not ctx.get("final_text"):
            ctx["final_text"] = change["suggestion"]
        page = get_db().execute("SELECT title, meta_description FROM pages WHERE id = ?",
                                (change["page_id"],)).fetchone() or (None, None)
        # Yalnızca http(s) adresleri çerçevede açılır (javascript: vb. adresler açılmaz)
        frame_url = change["url"] if change["url"].startswith(("http://", "https://")) else None
        return render_template("change.html", change=change,
                               field_name=FIELD_NAMES.get(change["field"], change["field"]),
                               limits=LIMITS.get(change["field"]), page_title=page[0], page_meta=page[1],
                               frame_url=frame_url, **ctx), status

    def runs(limit=20, completed_only=False):
        where = "WHERE r.status = 'completed'" if completed_only else ""
        return get_db().execute(
            f"""SELECT r.id, s.url, r.created_at, r.status, r.pages_crawled,
                       (SELECT COUNT(*) FROM issues i JOIN pages p ON p.id = i.page_id WHERE p.crawl_run_id = r.id)
                FROM crawl_runs r JOIN sites s ON s.id = r.site_id {where} ORDER BY r.id DESC LIMIT ?""",
            (limit,)).fetchall()

    def start_job(kind, title, fn):
        try:
            return jobs.start(kind, title, fn), None
        except RuntimeError as e:
            return None, str(e)

    # ------------------------------------------------------------------ panel
    @app.route("/")
    def dashboard():
        db = get_db()
        counts = dict(db.execute("SELECT status, COUNT(*) FROM applied_changes GROUP BY status").fetchall())
        latest = (runs(1, completed_only=True) or [None])[0]
        return render_template("dashboard.html", counts=counts, latest=latest, last_job=jobs.current,
                               wp_connected=wp["publisher"] is not None)

    # ------------------------------------------------------------------ tarama
    @app.route("/crawl")
    def crawl_page():
        last = get_db().execute("SELECT url FROM sites ORDER BY id DESC LIMIT 1").fetchone()
        return render_template("crawl.html", runs=runs(),
                               form={"url": last[0] if last else "", "max_pages": 100, "delay": 0.5})

    @app.route("/crawl", methods=["POST"])
    def crawl_start():
        form = {"url": request.form.get("url", "").strip(), "max_pages": request.form.get("max_pages", "100"),
                "delay": request.form.get("delay", "0.5")}
        try:
            if not form["url"]:
                raise ValueError("Site adresi boş olamaz")
            if not form["url"].startswith(("http://", "https://")):
                form["url"] = "https://" + form["url"]
            if not urlparse(form["url"]).netloc:
                raise ValueError("Geçerli bir site adresi gir")
            max_pages = _number(form["max_pages"], int, 1, 500, "En fazla sayfa")
            delay = _number(form["delay"], float, 0, 10, "Bekleme süresi")
        except ValueError as e:
            return render_template("crawl.html", runs=runs(), form=form, error=str(e)), 400
        url = form["url"]
        job, error = start_job(
            "crawl", f"Tarama: {url}",
            lambda: {k: v for k, v in crawl_run(url, max_pages=max_pages, db_path=db_path,
                                                reports_dir=reports_dir, delay=delay).items() if k != "report"})
        if job is None:
            return render_template("crawl.html", runs=runs(), form=form, error=error), 409
        return redirect(url_for("job_page", job_id=job.id))

    @app.route("/runs/<int:run_id>")
    def run_report(run_id):
        db = get_db()
        run = next((r for r in runs(10 ** 6) if r[0] == run_id), None)
        if run is None:
            abort(404)
        severity = request.args.get("severity")
        severity = severity if severity in SEVERITIES else None
        by_severity = dict(db.execute(
            "SELECT i.severity, COUNT(*) FROM issues i JOIN pages p ON p.id = i.page_id "
            "WHERE p.crawl_run_id = ? GROUP BY i.severity", (run_id,)).fetchall())
        top = db.execute(
            "SELECT i.code, i.severity, COUNT(*) AS n FROM issues i JOIN pages p ON p.id = i.page_id "
            "WHERE p.crawl_run_id = ? GROUP BY i.code, i.severity ORDER BY n DESC LIMIT 8", (run_id,)).fetchall()
        issues = {}
        for page_id, sev, message in db.execute(
                "SELECT i.page_id, i.severity, i.message FROM issues i JOIN pages p ON p.id = i.page_id "
                "WHERE p.crawl_run_id = ? ORDER BY i.id", (run_id,)):
            if severity is None or sev == severity:
                issues.setdefault(page_id, []).append((sev, message))
        pages = [p for p in db.execute(
            "SELECT id, url, status_code, error_message FROM pages WHERE crawl_run_id = ? ORDER BY id", (run_id,))
                 if severity is None or p[0] in issues]
        return render_template("run.html", run=run, by_severity=by_severity, top=top, pages=pages,
                               issues=issues, severity=severity, severities=SEVERITIES)

    # ------------------------------------------------------------------ öneri üretme
    @app.route("/suggest")
    def suggest_page():
        return render_template("suggest.html", runs=runs(completed_only=True), model=model,
                               selected=request.args.get("run_id", type=int), form={"limit": 5, "brand": brand or ""})

    @app.route("/suggest", methods=["POST"])
    def suggest_start():
        form = {"limit": request.form.get("limit", "5"), "brand": request.form.get("brand", "").strip(),
                "model": request.form.get("model", "").strip() or model}
        selected = request.form.get("run_id", type=int)
        try:
            limit = _number(form["limit"], int, 1, 200, "Sayfa sayısı")
            if selected is None or all(r[0] != selected for r in runs(10 ** 6, completed_only=True)):
                raise ValueError("Tamamlanmış bir tarama seç")
        except ValueError as e:
            return render_template("suggest.html", runs=runs(completed_only=True), model=form["model"],
                                   selected=selected, form=form, error=str(e)), 400
        discard_pending, discard_rejected = bool(request.form.get("discard_pending")), bool(request.form.get("discard_rejected"))
        job, error = start_job(
            "suggest", f"Öneri üretme (tarama #{selected})",
            lambda: generate_suggestions(db_path, selected, limit, form["model"], form["brand"] or None, chat=chat,
                                         discard_pending=discard_pending, discard_rejected=discard_rejected))
        if job is None:
            return render_template("suggest.html", runs=runs(completed_only=True), model=form["model"],
                                   selected=selected, form=form, error=error), 409
        return redirect(url_for("job_page", job_id=job.id))

    # ------------------------------------------------------------------ işler
    @app.route("/jobs/<job_id>")
    def job_page(job_id):
        job = jobs.get(job_id) or abort(404)
        return render_template("job.html", job=job)

    @app.route("/jobs/<job_id>/status.json")
    def job_status(job_id):
        job = jobs.get(job_id) or abort(404)
        return jsonify(job.snapshot())

    @app.route("/jobs/<job_id>/cancel", methods=["POST"])
    def job_cancel(job_id):
        jobs.cancel(job_id) or abort(404)
        return redirect(url_for("job_page", job_id=job_id))

    # ------------------------------------------------------------------ inceleme
    @app.route("/pending")
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

    # ------------------------------------------------------------------ onaylananlar / uygulama
    def render_approved(status=200, **ctx):
        items = list_unapplied(get_db())
        for item in items:
            item["field_name"] = FIELD_NAMES.get(item["field"], item["field"])
        return render_template("approved.html", items=items, can_apply=wp["publisher"] is not None, **ctx), status

    @app.route("/approved")
    def approved_list():
        return render_approved()

    @app.route("/approved.csv")
    def approved_csv():
        body = "\ufeff" + export_csv(list_unapplied(get_db()))  # BOM: Excel Türkçe karakterleri doğru okusun
        return Response(body, content_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=onaylanan_degisiklikler.csv"})

    @app.route("/change/<int:change_id>/mark-applied", methods=["POST"])
    def mark_applied_view(change_id):
        load(change_id)
        try:
            mark_applied(get_db(), change_id)
        except ValueError as e:
            abort(400, str(e))
        return redirect(url_for("approved_list"))

    @app.route("/change/<int:change_id>/reopen", methods=["POST"])
    def reopen_view(change_id):
        load(change_id)
        try:
            reopen(get_db(), change_id)
        except ValueError as e:
            abort(400, str(e))
        return redirect(url_for("change_page", change_id=change_id))

    @app.route("/change/<int:change_id>/apply", methods=["POST"])
    def apply_view(change_id):
        load(change_id)
        if wp["publisher"] is None:
            abort(404)  # siteye yazma ayarlanmamış (WordPress bağlı değil)
        try:
            result = apply_change(get_db(), change_id, wp["publisher"])
        except ValueError as e:
            abort(400, str(e))
        if result["status"] == "failed":
            return render_approved(502, error=f"#{change_id} siteye uygulanamadı: {result['error']}")
        return redirect(url_for("approved_list"))

    # ------------------------------------------------------------------ site profili
    @app.route("/profile", methods=["GET", "POST"])
    def profile_page():
        db = get_db()
        sites = db.execute("SELECT id, url FROM sites ORDER BY id").fetchall()
        site_id = request.values.get("site_id", type=int) or (sites[0][0] if sites else None)
        if site_id is not None and all(s[0] != site_id for s in sites):
            abort(404)
        message = None
        if request.method == "POST":
            set_value(db, site_id, "brand_name", request.form.get("brand_name", ""))
            for key in ("example_titles", "example_meta_descriptions", "banned_phrases"):
                set_value(db, site_id, key, request.form.get(key, "").splitlines())
            db.commit()
            message = "Profil kaydedildi."
        profile = get_profile(db, site_id) if site_id is not None else None
        warnings = check_profile(profile, LIMITS) if profile else []
        return render_template("profile.html", sites=sites, site_id=site_id, profile=profile,
                               warnings=warnings, message=message)

    # ------------------------------------------------------------------ WordPress bağlantısı
    def render_wordpress(status=200, **ctx):
        return render_template("wordpress.html", info=wp["info"], form=ctx.pop("form", {"plugin": "yoast"}),
                               **ctx), status

    @app.route("/wordpress")
    def wordpress_page():
        return render_wordpress()

    @app.route("/wordpress/connect", methods=["POST"])
    def wordpress_connect():
        form = {k: request.form.get(k, "").strip() for k in ("url", "user", "plugin")}
        password = request.form.get("password", "")
        if not (form["url"].startswith(("http://", "https://")) and form["user"] and password):
            return render_wordpress(400, form=form, error="Site adresi (http/https ile), kullanıcı adı ve uygulama parolası gerekli")
        try:
            candidate = WordPressPublisher(form["url"], form["user"], password, form["plugin"])
            name, exposed = candidate.check()
        except ValueError as e:
            return render_wordpress(400, form=form, error=str(e))
        except PublishError as e:
            return render_wordpress(502, form=form, error=str(e))
        wp["publisher"], wp["info"] = candidate, {"url": candidate.site, "user": form["user"]}
        notice = {True: None, False: "SEO alanları REST API'ye açık değil: wp_seo_rest.php dosyasını siteye ekle "
                                    "ya da eklenti seçimini kontrol et.",
                  None: "Sitede sayfa olmadığı için SEO alanlarının açık olup olmadığı kontrol edilemedi."}[exposed]
        return render_wordpress(message=f"Bağlandı: {name}", warning=notice)

    @app.route("/wordpress/disconnect", methods=["POST"])
    def wordpress_disconnect():
        wp["publisher"], wp["info"] = None, None
        return redirect(url_for("wordpress_page"))

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEO arayüzü (yalnızca yerel).")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--brand", default=None, help="Başlıkların sonuna ' | MARKA' eklenir")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--wp-url", default=None, help="İsteğe bağlı: WordPress'e açılışta bağlan (arayüzden de bağlanılabilir)")
    parser.add_argument("--wp-user", default=None)
    parser.add_argument("--wp-plugin", choices=["yoast", "rankmath"], default="yoast")
    args = parser.parse_args()

    publisher = None
    if args.wp_url:
        password = os.environ.get("WP_APP_PASSWORD")  # parola komut satırına yazılmaz (geçmişte kalır)
        if not args.wp_user or not password:
            parser.error("--wp-url için --wp-user ve WP_APP_PASSWORD ortam değişkeni gerekli")
        publisher = WordPressPublisher(args.wp_url, args.wp_user, password, args.wp_plugin)
    create_app(args.db, brand=args.brand, model=args.model, publisher=publisher).run(host="127.0.0.1", port=5000)
