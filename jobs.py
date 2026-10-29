"""
Arka plan işleri (tarama, öneri üretme). Tek seferde yalnızca BİR iş çalışır (SQLite ve Ollama tek kullanıcılı).

İş, mevcut işlevleri (main.run, ai.generate_suggestions) olduğu gibi çağırır; onların print() çıktısı işin
günlüğüne yazılır. İptal: günlüğe bir sonraki yazımda JobCancelled fırlatılır (işlev bir sonraki sayfada durur).
Not: print yönlendirmesi süreç geneli (sys.stdout) olduğu için bu yalnızca tek kullanıcılı yerel arayüze uygundur.
"""
import contextlib
import io
import threading
import time
import uuid

MAX_LINES = 500
KEEP_JOBS = 20


class JobCancelled(Exception):
    pass


class Job:
    def __init__(self, kind, title):
        self.id = uuid.uuid4().hex[:8]
        self.kind, self.title = kind, title
        self.state = "running"          # running | done | failed | cancelled
        self.lines, self.result, self.error = [], None, None
        self.cancel_requested = False
        self.started, self.ended = time.time(), None

    def snapshot(self, tail=200):
        end = self.ended or time.time()
        return {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state,
                "lines": self.lines[-tail:], "result": self.result, "error": self.error,
                "elapsed": int(end - self.started)}


class _Writer(io.TextIOBase):
    def __init__(self, job):
        self.job, self.buf = job, ""

    def writable(self):
        return True

    def write(self, text):
        if self.job.cancel_requested:
            raise JobCancelled()
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.job.lines.append(line)
        del self.job.lines[:-MAX_LINES]
        return len(text)


class JobManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs, self.current = {}, None

    def start(self, kind, title, fn):
        """fn() çağrılır; döndürdüğü sözlük işin sonucudur. Başka iş çalışıyorsa RuntimeError."""
        with self.lock:
            if self.current is not None and self.current.state == "running":
                raise RuntimeError(f"Başka bir iş çalışıyor: {self.current.title}")
            job = Job(kind, title)
            self.jobs[job.id] = job
            self.current = job
            for old in list(self.jobs)[:-KEEP_JOBS]:
                del self.jobs[old]
        threading.Thread(target=self._run, args=(job, fn), daemon=True).start()
        return job

    def _run(self, job, fn):
        try:
            with contextlib.redirect_stdout(_Writer(job)):
                job.result = fn()
            job.state = "done"
        except JobCancelled:
            job.state = "cancelled"
        except Exception as e:
            job.state = "failed"
            job.error = f"{type(e).__name__}: {e}"
        finally:
            job.ended = time.time()

    def get(self, job_id):
        return self.jobs.get(job_id)

    def cancel(self, job_id):
        job = self.get(job_id)
        if job is not None and job.state == "running":
            job.cancel_requested = True
        return job
