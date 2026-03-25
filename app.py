"""
Flask Dashboard — 모멘텀 + 선행 신호 + 백테스트 검증 (S&P 500 / SOX 반도체)
"""

import os, re, json, pathlib, secrets, threading, logging
from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, abort
from dotenv import load_dotenv
import bot
import backtest as bt
import longterm as lt
load_dotenv()

LOG = logging.getLogger("app")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))
app.jinja_env.autoescape = True

HISTORY_DIR      = pathlib.Path("history")
PRESIGNAL_DIR    = pathlib.Path("presignal")
CONVICTION_DIR   = pathlib.Path("conviction")
BACKTEST_DIR     = pathlib.Path("backtest_results")
HISTORY_DIR.mkdir(exist_ok=True)
PRESIGNAL_DIR.mkdir(exist_ok=True)
CONVICTION_DIR.mkdir(exist_ok=True)
BACKTEST_DIR.mkdir(exist_ok=True)
LONGTERM_DIR     = pathlib.Path("longterm")
LONGTERM_DIR.mkdir(exist_ok=True)
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
    token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not token or token != session.get("_csrf_token"):
        abort(403)


# ── AppState ───────────────────────────────────────────────────────
class AppState:
    def __init__(self):
        self._lock = threading.RLock()
        self._results = None
        self._presignal_results = None
        self._backtest_results = None
        self._analyzing = False
        self._analyzing_presignal = False
        self._analyzing_backtest = False
        self._conviction_results = None
        self._analyzing_conviction = False
        self._longterm_results = None
        self._analyzing_longterm = False
        self._error = None
        self._history_loaded = False

    def _update_results_unlocked(self, data):
        self._results = data
        self._error = data.get("error")

    def _update_presignal_unlocked(self, data):
        self._presignal_results = data
        if data.get("error"):
            self._error = data["error"]

    def update_results(self, data):
        with self._lock:
            self._update_results_unlocked(data)

    def update_presignal(self, data):
        with self._lock:
            self._update_presignal_unlocked(data)

    def ensure_history_loaded(self):
        with self._lock:
            if self._history_loaded:
                return
        d1 = _load_latest(HISTORY_DIR)
        d2 = _load_latest(PRESIGNAL_DIR)
        with self._lock:
            if self._history_loaded:
                return
            if d1 and self._results is None:
                self._update_results_unlocked(d1)
            if d2 and self._presignal_results is None:
                self._update_presignal_unlocked(d2)
            self._history_loaded = True

    def start_analyzing(self, mode="momentum"):
        with self._lock:
            if mode == "presignal":
                self._analyzing_presignal = True
            elif mode == "backtest":
                self._analyzing_backtest = True
            else:
                self._analyzing = True

    def finish_analyzing(self, data=None, error=None, mode="momentum"):
        with self._lock:
            if mode == "presignal":
                self._analyzing_presignal = False
                if data:
                    self._update_presignal_unlocked(data)
            elif mode == "backtest":
                self._analyzing_backtest = False
                if data:
                    self._backtest_results = data
            else:
                self._analyzing = False
                if data:
                    self._update_results_unlocked(data)
            if error:
                self._error = error

    def get_snapshot(self):
        with self._lock:
            return {
                "results": self._results,
                "presignal_results": self._presignal_results,
                "conviction_results": self._conviction_results,
                "analyzing_conviction": self._analyzing_conviction,
                "longterm_results": self._longterm_results,
                "analyzing_longterm": self._analyzing_longterm,

                "backtest_results": self._backtest_results,
                "analyzing": self._analyzing,
                "analyzing_presignal": self._analyzing_presignal,
                "analyzing_backtest": self._analyzing_backtest,
                "error": self._error,
            }


state = AppState()


# ── 유틸 ───────────────────────────────────────────────────────────
def _fmt_label(name):
    try:
        return datetime.strptime(name, "%Y-%m-%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return name


def _load_latest(d):
    files = sorted(d.glob("*.json"), reverse=True)
    if not files:
        return None
    try:
        with open(files[0], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _get_dates(d, prefix=""):
    files = sorted(d.glob("*.json"), reverse=True)
    result = []
    for fp in files[:HISTORY_PAGE_SIZE]:
        stem = fp.stem
        if prefix:
            stem_clean = stem.replace(prefix, "")
        else:
            stem_clean = stem
        if _HISTORY_NAME_RE.fullmatch(stem_clean) or _HISTORY_NAME_RE.fullmatch(stem):
            result.append({"name": fp.name, "label": _fmt_label(stem_clean)})
    return result


def _get_data(d, ds):
    if not _HISTORY_NAME_RE.fullmatch(ds):
        return None
    fp = d / f"{ds}.json"
    if not fp.exists():
        return None
    try:
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _count(results, field, value):
    return sum(
        1 for r in results
        if r.get(field) == value
        or (field not in r and value == "green" and "🟢" in r.get("entry", ""))
        or (field not in r and value == "wait" and "⏳" in r.get("entry", ""))
        or (field not in r and value == "stop" and "❌" in r.get("entry", ""))
    )


# ── 분석 스레드 ───────────────────────────────────────────────────
def _run_bg(mode="momentum", universe="sp500"):
    try:
        state.start_analyzing(mode=mode)
        if mode == "presignal":
            data = bot.analyze_presignal(universe=universe)
        elif mode == "conviction": # New: handle conviction analysis
            data = bot.analyze_conviction(universe=universe)
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


def _run_backtest_bg(modes, days):
    try:
        state.start_analyzing(mode="backtest")
        result = bt.run_full_backtest(modes=modes, hold_days=days)
        state.finish_analyzing(data=result, mode="backtest")
    except Exception as e:
        LOG.error("백테스트 실행 오류: %s", e)
        state.finish_analyzing(error=str(e), mode="backtest")


# ── 라우트 ─────────────────────────────────────────────────────────
@app.route("/")
def index():
    state.ensure_history_loaded()
    snap = state.get_snapshot()
    return render_template(
        "dashboard.html",
        data=snap["results"],
        presignal_data=snap["presignal_results"],
        conviction_data=snap.get("conviction_results"), # New: pass conviction data
        analyzing=snap["analyzing"],
        analyzing_presignal=snap["analyzing_presignal"],
        analyzing_conviction=snap.get("analyzing_conviction", False), # New: pass conviction analysis state
        analyzing_longterm=snap.get("analyzing_longterm", False),
        error=snap["error"],
        is_history=False,
        universes=bot.UNIVERSE_MAP,
    )


@app.route("/history")
def history_list():
    conviction_dates = []
    if CONVICTION_DIR.exists():
        for f in sorted(CONVICTION_DIR.glob("*.json"), reverse=True)[:HISTORY_PAGE_SIZE]:
            conviction_dates.append({"name": f.name, "label": _fmt_label(f.stem)})

    backtest_dates = []
    if BACKTEST_DIR.exists():
        for f in sorted(BACKTEST_DIR.glob("backtest_*.json"), reverse=True)[:HISTORY_PAGE_SIZE]:
            stem = f.stem.replace("backtest_", "")
            backtest_dates.append({"name": f.name, "label": _fmt_label(stem)})

    longterm_dates = []
    if LONGTERM_DIR.exists():
        for f in sorted(LONGTERM_DIR.glob("*.json"), reverse=True)[:HISTORY_PAGE_SIZE]:
            longterm_dates.append({"name": f.name, "label": _fmt_label(f.stem)})

    return render_template(
        "history.html",
        momentum_dates=_get_dates(HISTORY_DIR),
        presignal_dates=_get_dates(PRESIGNAL_DIR),
        conviction_dates=conviction_dates,
        backtest_dates=backtest_dates,
        longterm_dates=longterm_dates,
    )

@app.route("/history/momentum/<ds>")
def history_momentum(ds):
    if not _HISTORY_NAME_RE.fullmatch(ds):
        abort(400)
    data = _get_data(HISTORY_DIR, ds)
    if not data:
        abort(404)
    results = data.get("results", [])
    if "green" not in data:
        data["green"] = _count(results, "entry_key", "green")
        data["wait"] = _count(results, "entry_key", "wait")
        data["stop"] = _count(results, "entry_key", "stop")
    return render_template(
        "dashboard.html",
        data=data,
        presignal_data=None,
        conviction_data=None, # New: ensure conviction data is None for momentum history
        analyzing=False,
        analyzing_presignal=False,
        analyzing_conviction=False, # New: ensure conviction analysis state is False
        error=None,
        is_history=True,
        universes=bot.UNIVERSE_MAP,
    )


@app.route("/history/presignal/<ds>")
def history_presignal(ds):
    if not _HISTORY_NAME_RE.fullmatch(ds):
        abort(400)
    data = _get_data(PRESIGNAL_DIR, ds)
    if not data:
        abort(404)
    return render_template(
        "dashboard.html",
        data=None,
        presignal_data=data,
        conviction_data=None, # New: ensure conviction data is None for presignal history
        analyzing=False,
        analyzing_presignal=False,
        analyzing_conviction=False, # New: ensure conviction analysis state is False
        error=None,
        is_history=True,
        universes=bot.UNIVERSE_MAP,
    )


@app.route("/history/conviction/<ds>")
def history_conviction(ds):
    if not _HISTORY_NAME_RE.fullmatch(ds):
        abort(400)
    fp = CONVICTION_DIR / f"{ds}.json"
    if not fp.exists():
        abort(404)
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        abort(500)
    return render_template(
        "dashboard.html",
        data=None,
        presignal_data=None, # New: ensure presignal data is None for conviction history
        conviction_data=data, # New: pass conviction data
        analyzing=False,
        analyzing_presignal=False,
        analyzing_conviction=False, # New: ensure conviction analysis state is False
        error=None,
        is_history=True,
        universes=bot.UNIVERSE_MAP,
    )


@app.route("/status")
def status():
    snap = state.get_snapshot()
    return jsonify({
        "analyzing": snap["analyzing"],
        "analyzing_presignal": snap["analyzing_presignal"],
        "analyzing_conviction": snap["analyzing_conviction"], # New: include conviction analysis state
        "analyzing_backtest": snap["analyzing_backtest"],
        "has_data": snap["results"] is not None,
        "has_presignal": snap["presignal_results"] is not None,
        "has_conviction": snap["conviction_results"] is not None, # New: include conviction data presence
        "has_backtest": snap["backtest_results"] is not None,
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
    if mode == "conviction" and snap.get("analyzing_conviction"): # Note: adding check for consistency
        return jsonify({"status": "already_running"})
    t = threading.Thread(target=_run_bg, args=(mode, universe), daemon=True)
    t.start()
    return jsonify({"status": "started", "mode": mode, "universe": universe})


# ── 백테스트 라우트 ────────────────────────────────────────────────
@app.route("/backtest")
def backtest_page():
    bt_files = sorted(BACKTEST_DIR.glob("backtest_*.json"), reverse=True)

    # file 파라미터가 있으면 해당 파일 로드
    req_file = request.args.get("file", "")
    latest = None

    if req_file and (BACKTEST_DIR / req_file).exists():
        try:
            with open(BACKTEST_DIR / req_file, "r", encoding="utf-8") as f:
                latest = json.load(f)
        except Exception:
            pass
    elif bt_files:
        try:
            with open(bt_files[0], "r", encoding="utf-8") as f:
                latest = json.load(f)
        except Exception:
            pass

    history_list_data = []
    for bf in bt_files[:20]:
        stem = bf.stem.replace("backtest_", "")
        history_list_data.append({"name": bf.name, "label": _fmt_label(stem)})

    return render_template(
        "backtest.html",
        latest=latest,
        history_list=history_list_data,
    )


@app.route("/backtest/run", methods=["POST"])
def backtest_run():
    _validate_csrf()

    snap = state.get_snapshot()
    if snap["analyzing_backtest"]:
        return jsonify({"error": "이미 실행 중"}), 409

    modes = request.form.getlist("modes") or ["momentum", "presignal", "conviction"]
    days_str = request.form.get("days", "3,5,10,20")
    try:
        days = [int(d.strip()) for d in days_str.split(",") if d.strip()]
    except ValueError:
        days = [3, 5, 10, 20]

    t = threading.Thread(target=_run_backtest_bg, args=(modes, days), daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/backtest/status")
def backtest_status():
    snap = state.get_snapshot()
    return jsonify({
        "analyzing": snap["analyzing_backtest"],
        "has_results": snap["backtest_results"] is not None,
    })
# ── 중장기 분석 라우트 ─────────────────────────────────────────────
@app.route("/longterm")
def longterm_page():
    files = sorted(LONGTERM_DIR.glob("*.json"), reverse=True)
    data = None
    req_file = request.args.get("file", "")
    if req_file and (LONGTERM_DIR / req_file).exists():
        try:
            with open(LONGTERM_DIR / req_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    elif files:
        try:
            with open(files[0], "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    history = [{"name": f.name, "label": f.stem.replace("_", " ")} for f in files[:20]]
    return render_template("longterm.html", data=data, history_list=history)

@app.route("/longterm/run", methods=["POST"])
def longterm_run():
    _validate_csrf()
    with state._lock:
        if state._analyzing_longterm:
            return jsonify({"ok": False, "msg": "이미 분석 중"}), 409
        state._analyzing_longterm = True

    def _run():
        try:
            result = lt.analyze_longterm("sp500+sox")
            with state._lock:
                state._longterm_results = result
        except Exception as e:
            LOG.error(f"중장기 분석 에러: {e}")
        finally:
            with state._lock:
                state._analyzing_longterm = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/longterm/status")
def longterm_status():
    with state._lock:
        return jsonify({
            "analyzing": state._analyzing_longterm,
            "has_data": state._longterm_results is not None,
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
