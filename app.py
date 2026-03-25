"""
S&P 500 Momentum Dashboard — Flask App
───────────────────────────────────────
• CSRF: context_processor + session 기반 직접 구현 (flask-wtf 불필요)
• Path Traversal 방어
• 중택 락 제거 (_update_results_unlocked)
• double-checked locking 수정
• bot.analyze() 에러 명시적 처리
• 히스토리 카운트 직접 계산 (폴백)
"""

import os
import re
import json
import secrets
import threading
import pathlib
import logging
from datetime import datetime

from flask import (
    Flask, render_template, jsonify, request,
    redirect, url_for, abort, session,
)
from dotenv import load_dotenv

load_dotenv()

import bot

# ──────────────────────────── Flask 앱 ─────────────────────────────
app = Flask(__name__)
app.jinja_env.autoescape = True
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))

log = logging.getLogger(__name__)

HISTORY_DIR = pathlib.Path("history")
HISTORY_DIR.mkdir(exist_ok=True)

HISTORY_FILENAME_PATTERN = r"^\d{4}-\d{2}-\d{2}_\d{6}$"
_HISTORY_NAME_RE = re.compile(HISTORY_FILENAME_PATTERN)
HISTORY_PAGE_SIZE = 50


# ═══════════════════════════════════════════════════════════════════
# CSRF — flask-wtf 없이 직접 구현
# ═══════════════════════════════════════════════════════════════════

@app.context_processor
def inject_csrf():
    """모든 템플릿에 csrf_token() 함수 주입."""
    def _csrf_token():
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_hex(32)
        return session["_csrf_token"]
    return {"csrf_token": _csrf_token}


def _validate_csrf():
    """POST 요청의 CSRF 토큰 검증."""
    token = (
        request.form.get("_csrf_token")
        or request.headers.get("X-CSRF-Token", "")
    )
    if not token or token != session.get("_csrf_token"):
        abort(403, description="CSRF 토큰 검증 실패")


# ═══════════════════════════════════════════════════════════════════
# 날짜 포맷 헬퍼
# ═══════════════════════════════════════════════════════════════════

def _format_history_label(filename_stem: str) -> str:
    try:
        if len(filename_stem) >= 17 and "_" in filename_stem:
            date_part, time_part = filename_stem.split("_", 1)
            if len(time_part) == 6:
                formatted_time = f"{time_part[:2]}:{time_part[2:4]}:{time_part[4:6]}"
                return f"{date_part} {formatted_time}"
        return filename_stem
    except Exception:
        return filename_stem


# ═══════════════════════════════════════════════════════════════════
# 히스토리 카운트 헬퍼
# ═══════════════════════════════════════════════════════════════════

def _count_entries(results: list) -> dict:
    green = wait = stop = 0
    for r in results:
        key = r.get("entry_key", "")
        if key == "green":
            green += 1
        elif key == "wait":
            wait += 1
        elif key == "stop":
            stop += 1
        else:
            entry = r.get("entry", "")
            if "🟢" in entry:
                green += 1
            elif "⏳" in entry:
                wait += 1
            elif "❌" in entry:
                stop += 1
    return {"green": green, "wait": wait, "stop": stop}


# ═══════════════════════════════════════════════════════════════════
# 스레드-안전 상태 관리
# ═══════════════════════════════════════════════════════════════════

class AppState:
    def __init__(self):
        self._lock = threading.RLock()
        self.processed_results: list = []
        self.green_count: int = 0
        self.wait_count: int = 0
        self.stop_count: int = 0
        self.last_update: str = "-"
        self.analyzed_at: str = "-"
        self.is_analyzing: bool = False
        self.last_error: str = ""
        self._history_loaded: bool = False

    def _update_results_unlocked(self, data: dict):
        """락을 이미 보유한 상태에서만 호출."""
        results = data.get("results", [])
        self.processed_results = results
        if "green" in data and "wait" in data and "stop" in data:
            self.green_count = data["green"]
            self.wait_count = data["wait"]
            self.stop_count = data["stop"]
        else:
            counts = _count_entries(results)
            self.green_count = counts["green"]
            self.wait_count = counts["wait"]
            self.stop_count = counts["stop"]
        self.analyzed_at = data.get("analyzed_at", "-")
        self.last_update = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_error = data.get("error", "")

    def update_results(self, data: dict):
        with self._lock:
            self._update_results_unlocked(data)

    def ensure_history_loaded(self):
        with self._lock:
            if self._history_loaded:
                return

        # 락 밖에서 I/O
        data = None
        loaded_file = None
        try:
            files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)
            if files:
                loaded_file = files[0].name
                with open(files[0], "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception as e:
            log.warning("히스토리 로드 오류: %s", e)

        # double-checked locking
        with self._lock:
            if self._history_loaded:
                return
            self._history_loaded = True
            if data:
                self._update_results_unlocked(data)
                log.info("히스토리 로드: %s", loaded_file)

    def start_analyzing(self) -> bool:
        with self._lock:
            if self.is_analyzing:
                return False
            self.is_analyzing = True
            self.last_error = ""
            return True

    def finish_analyzing(self, data: dict = None, error: str = ""):
        with self._lock:
            self.is_analyzing = False
            if data:
                self._update_results_unlocked(data)
            if error:
                self.last_error = error

    def get_snapshot(self) -> dict:
        with self._lock:
            return {
                "results": list(self.processed_results),
                "green_count": self.green_count,
                "wait_count": self.wait_count,
                "stop_count": self.stop_count,
                "last_update": self.last_update,
                "analyzed_at": self.analyzed_at,
                "is_analyzing": self.is_analyzing,
                "last_error": self.last_error,
            }


state = AppState()


# ═══════════════════════════════════════════════════════════════════
# 히스토리 헬퍼
# ═══════════════════════════════════════════════════════════════════

def get_available_dates(limit: int = HISTORY_PAGE_SIZE) -> list[dict]:
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:limit]
    result = []
    for f in files:
        stem = f.stem
        if _HISTORY_NAME_RE.match(stem):
            result.append({"key": stem, "label": _format_history_label(stem)})
    return result


def get_history_data(date_str: str) -> dict | None:
    path = HISTORY_DIR / f"{date_str}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════
# 백그라운드 분석
# ═══════════════════════════════════════════════════════════════════

def run_analysis_background():
    try:
        result = bot.analyze()
        if result and isinstance(result, dict):
            error_msg = result.get("error", "")
            if error_msg:
                state.finish_analyzing(data=result, error=error_msg)
            else:
                state.finish_analyzing(data=result)
        else:
            state.finish_analyzing(error="분석 결과 없음")
    except Exception as e:
        log.error("백그라운드 분석 오류: %s", e, exc_info=True)
        state.finish_analyzing(error=str(e))


# ═══════════════════════════════════════════════════════════════════
# 라우트
# ═══════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    state.ensure_history_loaded()
    snap = state.get_snapshot()
    return render_template(
        "dashboard.html",
        results=snap["results"],
        green_count=snap["green_count"],
        wait_count=snap["wait_count"],
        stop_count=snap["stop_count"],
        last_update=snap["last_update"],
        analyzed_at=snap["analyzed_at"],
        is_analyzing=snap["is_analyzing"],
        last_error=snap["last_error"],
        is_history=False,
    )


@app.route("/history")
def history_list():
    dates = get_available_dates()
    return render_template("history.html", dates=dates)


@app.route("/history/<date_str>")
def history_detail(date_str):
    if not _HISTORY_NAME_RE.fullmatch(date_str):
        abort(400, description="잘못된 날짜 형식입니다.")
    data = get_history_data(date_str)
    if not data:
        return redirect(url_for("history_list"))
    results = data.get("results", [])
    if "green" in data and "wait" in data and "stop" in data:
        counts = {"green": data["green"], "wait": data["wait"], "stop": data["stop"]}
    else:
        counts = _count_entries(results)
    return render_template(
        "dashboard.html",
        results=results,
        green_count=counts["green"],
        wait_count=counts["wait"],
        stop_count=counts["stop"],
        last_update=_format_history_label(date_str),
        analyzed_at=data.get("analyzed_at", date_str),
        is_analyzing=False,
        last_error="",
        is_history=True,
    )


@app.route("/status")
def status():
    snap = state.get_snapshot()
    return jsonify(
        {
            "is_analyzing": snap["is_analyzing"],
            "last_error": snap["last_error"],
            "last_update": snap["last_update"],
            "green_count": snap["green_count"],
            "wait_count": snap["wait_count"],
            "stop_count": snap["stop_count"],
            "total": len(snap["results"]),
        }
    )


@app.route("/refresh", methods=["POST"])
def refresh():
    _validate_csrf()
    if state.start_analyzing():
        t = threading.Thread(target=run_analysis_background, daemon=True)
        t.start()
        return jsonify({"status": "started"})
    return jsonify({"status": "already_running"})


# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
