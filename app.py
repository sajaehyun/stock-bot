"""
Flask Dashboard — 모멘텀 + 선행 신호 (S&P 500 / SOX 반도체)
"""

import os, re, json, pathlib, secrets, threading
from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, abort
from dotenv import load_dotenv
import bot

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))
app.jinja_env.autoescape = True

HISTORY_DIR      = pathlib.Path("history")
PRESIGNAL_DIR    = pathlib.Path("presignal")
HISTORY_DIR.mkdir(exist_ok=True)
PRESIGNAL_DIR.mkdir(exist_ok=True)
_HISTORY_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")
HISTORY_PAGE_SIZE = 50

# ── CSRF ───────────────────────────────────────────────────────────
def _generate_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]

@app.context_processor
def inject_csrf():
    return {"csrf_token": _generate_csrf_token}

def _validate_csrf():
    token = request.headers.get("X-CSRF-Token") or request.form.get("_csrf_token")
    if not token or token != session.get("_csrf_token"):
        abort(403)

# ── AppState ───────────────────────────────────────────────────────
class AppState:
    def __init__(self):
        self._lock = threading.RLock()
        self._results = None
        self._presignal_results = None
        self._analyzing = False
        self._analyzing_presignal = False
        self._error = None
        self._history_loaded = False

    def _update_results_unlocked(self, data):
        self._results = data
        self._error = data.get("error")

    def _update_presignal_unlocked(self, data):
        self._presignal_results = data
        if data.get("error"): self._error = data["error"]

    def update_results(self, data):
        with self._lock: self._update_results_unlocked(data)

    def update_presignal(self, data):
        with self._lock: self._update_presignal_unlocked(data)

    def ensure_history_loaded(self):
        with self._lock:
            if self._history_loaded: return
        d1 = _load_latest(HISTORY_DIR)
        d2 = _load_latest(PRESIGNAL_DIR)
        with self._lock:
            if self._history_loaded: return
            if d1 and self._results is None: self._update_results_unlocked(d1)
            if d2 and self._presignal_results is None: self._update_presignal_unlocked(d2)
            self._history_loaded = True

    def start_analyzing(self, mode="momentum"):
        with self._lock:
            if mode == "presignal": self._analyzing_presignal = True
            else: self._analyzing = True

    def finish_analyzing(self, data=None, error=None, mode="momentum"):
        with self._lock:
            if mode == "presignal":
                self._analyzing_presignal = False
                if data: self._update_presignal_unlocked(data)
            else:
                self._analyzing = False
                if data: self._update_results_unlocked(data)
            if error: self._error = error

    def get_snapshot(self):
        with self._lock:
            return {
                "results": self._results,
                "presignal_results": self._presignal_results,
                "analyzing": self._analyzing,
                "analyzing_presignal": self._analyzing_presignal,
                "error": self._error,
            }

state = AppState()

# ── 유틸 ───────────────────────────────────────────────────────────
def _fmt_label(name):
    try: return datetime.strptime(name, "%Y-%m-%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError: return name

def _load_latest(d):
    files = sorted(d.glob("*.json"), reverse=True)
    if not files: return None
    try:
        with open(files[0], "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return None

def _get_dates(d):
    files = sorted(d.glob("*.json"), reverse=True)
    return [{"name": fp.stem, "label": _fmt_label(fp.stem)} for fp in files[:HISTORY_PAGE_SIZE] if _HISTORY_NAME_RE.fullmatch(fp.stem)]

def _get_data(d, ds):
    if not _HISTORY_NAME_RE.fullmatch(ds): return None
    fp = d / f"{ds}.json"
    if not fp.exists(): return None
    try:
        with open(fp, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return None

def _count(results, field, value):
    return sum(1 for r in results if r.get(field) == value or
               (field not in r and value == "green" and "🟢" in r.get("entry","")) or
               (field not in r and value == "wait" and "⏳" in r.get("entry","")) or
               (field not in r and value == "stop" and "❌" in r.get("entry","")))

# ── 분석 스레드 ───────────────────────────────────────────────────
def _run_bg(mode="momentum", universe="sp500"):
    try:
        state.start_analyzing(mode=mode)
        if mode == "presignal":
            data = bot.analyze_presignal(universe=universe)
        else:
            data = bot.analyze()
        if not data:
            state.finish_analyzing(error="분석 결과 없음", mode=mode)
        elif data.get("error"):
            state.finish_analyzing(data=data, error=data["error"], mode=mode)
        else:
            state.finish_analyzing(data=data, mode=mode)
    except Exception as e:
        state.finish_analyzing(error=str(e), mode=mode)

# ── 라우트 ─────────────────────────────────────────────────────────
@app.route("/")
def index():
    state.ensure_history_loaded()
    snap = state.get_snapshot()
    return render_template("dashboard.html",
        data=snap["results"], presignal_data=snap["presignal_results"],
        analyzing=snap["analyzing"], analyzing_presignal=snap["analyzing_presignal"],
        error=snap["error"], is_history=False,
        universes=bot.UNIVERSE_MAP)

@app.route("/history")
def history_list():
    return render_template("history.html",
        momentum_dates=_get_dates(HISTORY_DIR),
        presignal_dates=_get_dates(PRESIGNAL_DIR))

@app.route("/history/momentum/<ds>")
def history_momentum(ds):
    if not _HISTORY_NAME_RE.fullmatch(ds): abort(400)
    data = _get_data(HISTORY_DIR, ds)
    if not data: abort(404)
    results = data.get("results", [])
    if "green" not in data:
        data["green"] = _count(results, "entry_key", "green")
        data["wait"]  = _count(results, "entry_key", "wait")
        data["stop"]  = _count(results, "entry_key", "stop")
    return render_template("dashboard.html", data=data, presignal_data=None,
        analyzing=False, analyzing_presignal=False, error=None, is_history=True, universes=bot.UNIVERSE_MAP)

@app.route("/history/presignal/<ds>")
def history_presignal(ds):
    if not _HISTORY_NAME_RE.fullmatch(ds): abort(400)
    data = _get_data(PRESIGNAL_DIR, ds)
    if not data: abort(404)
    return render_template("dashboard.html", data=None, presignal_data=data,
        analyzing=False, analyzing_presignal=False, error=None, is_history=True, universes=bot.UNIVERSE_MAP)

@app.route("/status")
def status():
    snap = state.get_snapshot()
    return jsonify({
        "analyzing": snap["analyzing"],
        "analyzing_presignal": snap["analyzing_presignal"],
        "has_data": snap["results"] is not None,
        "has_presignal": snap["presignal_results"] is not None,
        "error": snap["error"],
    })

@app.route("/refresh", methods=["POST"])
def refresh():
    _validate_csrf()
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "momentum")
    universe = body.get("universe", "sp500")
    snap = state.get_snapshot()
    if mode == "presignal" and snap["analyzing_presignal"]:
        return jsonify({"status": "already_running"})
    if mode == "momentum" and snap["analyzing"]:
        return jsonify({"status": "already_running"})
    t = threading.Thread(target=_run_bg, args=(mode, universe), daemon=True)
    t.start()
    return jsonify({"status": "started", "mode": mode, "universe": universe})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
