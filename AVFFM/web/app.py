import os
import sys
import json
import time
import subprocess
import requests

from datetime import datetime, date, timedelta
from flask import Flask, render_template, jsonify, request
from werkzeug.security import check_password_hash

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from shared.state import read_state


app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static"
)

# ============================================================
# Optional API key for actuator routes (UI -> Flask)
# If AVFFM_KEY is empty, protection is disabled (backward compatible).
# Browser must send header: X-AVFFM-KEY: <key>
# ============================================================
AVFFM_KEY = os.getenv("AVFFM_KEY", "").strip()

def _require_key() -> bool:
    if not AVFFM_KEY:
        return True
    return request.headers.get("X-AVFFM-KEY", "") == AVFFM_KEY

def _deny_if_no_key():
    if not _require_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None

# ============================================================
# Generic file helpers (ONE implementation, used everywhere)
# ============================================================
def _atomic_write_json(path: str, obj: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)

def _read_json(path: str) -> dict:
    try:
        with open(path, "r") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def _with_file_lock(lock_path: str, fn):
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    with open(lock_path, "a") as lockf:
        try:
            import fcntl
            fcntl.flock(lockf, fcntl.LOCK_EX)
        except Exception:
            pass

        try:
            return fn()
        finally:
            try:
                import fcntl
                fcntl.flock(lockf, fcntl.LOCK_UN)
            except Exception:
                pass

# ============================================================
# Password (maintenance restart) - HASHED
# ============================================================
ADMIN_HASH_FILE = "data/admin_pass.hash"

def _load_admin_hash():
    try:
        with open(ADMIN_HASH_FILE, "r") as f:
            return f.read().strip()
    except Exception:
        return ""

def _check_password(pw: str) -> bool:
    stored_hash = _load_admin_hash()
    if not stored_hash:
        return False
    return check_password_hash(stored_hash, (pw or "").strip())

# ============================================================
# Camera Stream URL (UI)
# ============================================================
STREAM_URL = os.getenv("AVFFM_STREAM_URL", "http://localhost:8090/stream.mjpg")

# ============================================================
# Weather (OpenWeatherMap)
# ============================================================
OWM_API_KEY = os.getenv("OWM_API_KEY", "").strip()
CITY_NAME = os.getenv("OWM_CITY", "Pune").strip()

_weather_cache = {}
_weather_cache_time = 0

def get_weather():
    global _weather_cache, _weather_cache_time

    if not OWM_API_KEY:
        return {}

    now = time.time()
    if _weather_cache and (now - _weather_cache_time) < 3600:
        return _weather_cache

    url = "http://api.openweathermap.org/data/2.5/weather"
    params = {"q": CITY_NAME, "appid": OWM_API_KEY, "units": "metric"}

    try:
        r = requests.get(url, params=params, timeout=8)
        data = r.json()

        w = {
            "temp": round(data["main"]["temp"], 1),
            "feels_like": round(data["main"]["feels_like"], 1),
            "humidity": data["main"]["humidity"],
            "pressure": data["main"]["pressure"],
            "visibility": data.get("visibility", 0),
            "wind_speed": data["wind"]["speed"],
            "wind_deg": data["wind"].get("deg", 0),
            "clouds": data["clouds"]["all"],
            "sunrise": data["sys"]["sunrise"],
            "sunset": data["sys"]["sunset"],
            "condition": data["weather"][0]["main"] if data.get("weather") else "NA",
            "icon": data["weather"][0]["icon"] if data.get("weather") else ""
        }

        _weather_cache = w
        _weather_cache_time = now
        return w
    except Exception:
        return _weather_cache if _weather_cache else {}

# ============================================================
# Dosing state (shared with doser_daemon)
# ============================================================
DOSING_STATE_FILE = "data/dosing_state.json"
DOSING_LOCK_FILE  = DOSING_STATE_FILE + ".lock"
DEFAULT_MAX_ACTIONS_PER_DAY = 7

def get_dosing_state_unlocked() -> dict:
    return _read_json(DOSING_STATE_FILE)

def save_dosing_state_unlocked(st: dict):
    _atomic_write_json(DOSING_STATE_FILE, st if isinstance(st, dict) else {})

def update_dosing_state_transaction(mutator_fn):
    def _txn():
        st = get_dosing_state_unlocked()
        if not isinstance(st, dict):
            st = {}
        st2 = mutator_fn(st)
        if isinstance(st2, dict):
            st = st2
        save_dosing_state_unlocked(st)
        return st
    return _with_file_lock(DOSING_LOCK_FILE, _txn)

def get_dosing_state() -> dict:
    return _with_file_lock(DOSING_LOCK_FILE, get_dosing_state_unlocked)

def _normalize_dosing_state(st: dict) -> dict:
    if not isinstance(st, dict):
        st = {}

    today = date.today().isoformat()
    if st.get("day") != today:
        st["day"] = today
        st["actions_today"] = 0

    st.setdefault("actions_today", 0)
    st.setdefault("max_actions_per_day", DEFAULT_MAX_ACTIONS_PER_DAY)
    st.setdefault("status", "IDLE")
    st.setdefault("action", "NO_ACTION")
    st.setdefault("note", "")
    st.setdefault("dose_seconds", 0)
    st.setdefault("last_done", "--")
    return st

# ============================================================
# Maintenance system (water / sensors / tank / restart)
# ============================================================
MAINT_STATE_FILE = "data/maintenance_state.json"
MAINT_LOCK_FILE  = MAINT_STATE_FILE + ".lock"

MAINT_ITEMS = {
    "sensor_clean": {"interval_days": 7,  "label": "Clean sensors"},
    "water_change": {"interval_days": 15, "label": "Water change"},
    "tank_clean":   {"interval_days": 30, "label": "Clean tank"},
}

def _ensure_maintenance_state(st: dict) -> dict:
    if not isinstance(st, dict):
        st = {}

    today = date.today().isoformat()

    for key, meta in MAINT_ITEMS.items():
        if key not in st or not isinstance(st.get(key), dict):
            st[key] = {"interval_days": meta["interval_days"], "last_reset": today}

        st[key]["interval_days"] = int(st[key].get("interval_days", meta["interval_days"]) or meta["interval_days"])
        if st[key]["interval_days"] <= 0:
            st[key]["interval_days"] = meta["interval_days"]

        st[key].setdefault("last_reset", today)
        try:
            _ = datetime.strptime(st[key]["last_reset"], "%Y-%m-%d").date()
        except Exception:
            st[key]["last_reset"] = today

    lr = st.get("last_restart", "") or ""
    if lr:
        try:
            datetime.fromisoformat(str(lr))
            st["last_restart"] = str(lr)
        except Exception:
            st["last_restart"] = ""
    else:
        st["last_restart"] = ""

    return st

def load_maintenance_state_unlocked() -> dict:
    return _ensure_maintenance_state(_read_json(MAINT_STATE_FILE))

def save_maintenance_state_unlocked(st: dict):
    _atomic_write_json(MAINT_STATE_FILE, _ensure_maintenance_state(st))

def update_maintenance_state_transaction(mutator_fn):
    def _txn():
        st = load_maintenance_state_unlocked()
        st2 = mutator_fn(st)
        if isinstance(st2, dict):
            st = st2
        save_maintenance_state_unlocked(st)
        return st
    return _with_file_lock(MAINT_LOCK_FILE, _txn)

def load_maintenance_state() -> dict:
    return _with_file_lock(MAINT_LOCK_FILE, load_maintenance_state_unlocked)

def reset_maintenance_item(item_key: str) -> dict:
    if item_key not in MAINT_ITEMS:
        return load_maintenance_state()

    def _mut(st):
        st = _ensure_maintenance_state(st)
        st[item_key]["last_reset"] = date.today().isoformat()
        return st

    return update_maintenance_state_transaction(_mut)

def calculate_maintenance() -> dict:
    st = load_maintenance_state()
    now = datetime.now()
    today = date.today()

    out = {}
    for key, meta in MAINT_ITEMS.items():
        obj = st.get(key, {})
        last = datetime.strptime(obj["last_reset"], "%Y-%m-%d").date()
        interval = int(obj["interval_days"])

        due = last + timedelta(days=interval)
        days_left = (due - today).days
        overdue = days_left < 0

        due_dt = datetime.combine(due, datetime.min.time())
        remaining_seconds = int((due_dt - now).total_seconds())

        out[key] = {
            "label": meta["label"],
            "interval_days": interval,
            "last_reset": obj["last_reset"],
            "due_date": due.isoformat(),
            "days_left": days_left,
            "overdue": overdue,
            "remaining_seconds": remaining_seconds,
        }

    last_restart = st.get("last_restart", "") or ""
    out["last_restart"] = last_restart
    out["restart"] = {"label": "Restart Pi3", "last_restart": last_restart}
    return out

# ============================================================
# Manual dosing (GPIO direct)
# ============================================================
PH_UP_PIN   = 7
PH_DOWN_PIN = 8
TDS_UP_PIN  = 25
ACTIVE_LOW = True
MANUAL_DOSE_SECONDS = int(os.getenv("MANUAL_DOSE_SECONDS", "30"))

def _motor_on(GPIO, pin):
    GPIO.output(pin, GPIO.LOW if ACTIVE_LOW else GPIO.HIGH)

def _motor_off(GPIO, pin):
    GPIO.output(pin, GPIO.HIGH if ACTIVE_LOW else GPIO.LOW)

def manual_dose(pin, seconds=MANUAL_DOSE_SECONDS):
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    GPIO.setup(pin, GPIO.OUT)
    _motor_off(GPIO, pin)

    try:
        _motor_on(GPIO, pin)
        time.sleep(seconds)
    finally:
        try:
            _motor_off(GPIO, pin)
        except Exception:
            pass
        try:
            GPIO.cleanup(pin)
        except Exception:
            try:
                GPIO.cleanup()
            except Exception:
                pass

def _manual_action_common(action: str, pin: int):
    now_iso = datetime.now().isoformat(timespec="seconds")

    def start_mut(st):
        st = _normalize_dosing_state(st)
        actions_today = int(st.get("actions_today", 0))
        max_day = int(st.get("max_actions_per_day", DEFAULT_MAX_ACTIONS_PER_DAY))

        if actions_today >= max_day:
            st.update({
                "now": now_iso,
                "status": "DAILY_LIMIT",
                "action": action,
                "dose_seconds": 0,
                "note": "Daily action limit reached. Manual dose blocked.",
            })
            return st

        st.update({
            "now": now_iso,
            "status": "MANUAL_DOSING",
            "action": action,
            "dose_seconds": MANUAL_DOSE_SECONDS,
            "note": f"Manual dosing: {action} ({MANUAL_DOSE_SECONDS}s)",
        })
        return st

    st = update_dosing_state_transaction(start_mut)
    st = _normalize_dosing_state(st)

    if st.get("status") == "DAILY_LIMIT":
        return False, st

    manual_dose(pin, MANUAL_DOSE_SECONDS)

    done_iso = datetime.now().isoformat(timespec="seconds")

    def done_mut(st2):
        st2 = _normalize_dosing_state(st2)
        st2["actions_today"] = int(st2.get("actions_today", 0)) + 1
        st2.update({
            "now": done_iso,
            "status": "DONE",
            "action": action,
            "dose_seconds": MANUAL_DOSE_SECONDS,
            "last_done": done_iso,
            "note": f"Manual: {action} ({MANUAL_DOSE_SECONDS}s)",
        })
        return st2

    st = update_dosing_state_transaction(done_mut)
    st = _normalize_dosing_state(st)
    return True, st

# ============================================================
# Growlight state (shared with growlight daemon)
# ============================================================
GROWLIGHT_STATE_FILE = "data/growlight_state.json"
GROWLIGHT_LOCK_FILE  = GROWLIGHT_STATE_FILE + ".lock"

def _default_growlight_state() -> dict:
    return {
        "enabled": False,
        "status": "OFF",
        "mode": "SCHEDULE",
        "brightness": 0.40,
        "schedule": {"on": "06:00", "off": "22:00"},
        "target_680_pct": 55.0,
        "manual_on": False,
        "note": "",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

def _normalize_growlight_state(st: dict) -> dict:
    base = _default_growlight_state()
    if isinstance(st, dict):
        base.update(st)

    base["enabled"] = bool(base.get("enabled", False))

    mode = str(base.get("mode") or "SCHEDULE").upper().strip()
    if mode not in ("SCHEDULE", "SENSOR", "MANUAL"):
        mode = "SCHEDULE"
    base["mode"] = mode

    try:
        b = float(base.get("brightness", 0.40))
    except Exception:
        b = 0.40
    base["brightness"] = max(0.0, min(1.0, b))

    sch = base.get("schedule") if isinstance(base.get("schedule"), dict) else {}
    on_t  = str(sch.get("on") or "06:00").strip()
    off_t = str(sch.get("off") or "22:00").strip()
    try:
        datetime.strptime(on_t, "%H:%M")
    except Exception:
        on_t = "06:00"
    try:
        datetime.strptime(off_t, "%H:%M")
    except Exception:
        off_t = "22:00"
    base["schedule"] = {"on": on_t, "off": off_t}

    try:
        t = float(base.get("target_680_pct", 55.0))
    except Exception:
        t = 55.0
    base["target_680_pct"] = max(0.0, min(100.0, t))

    base["manual_on"] = bool(base.get("manual_on", False))
    base["status"] = "ON" if base["enabled"] else "OFF"

    ua = base.get("updated_at", "")
    try:
        if ua:
            datetime.fromisoformat(str(ua))
        else:
            raise ValueError
    except Exception:
        base["updated_at"] = datetime.now().isoformat(timespec="seconds")

    base["note"] = str(base.get("note") or "")
    return base

def get_growlight_state_unlocked() -> dict:
    st = _read_json(GROWLIGHT_STATE_FILE)
    if not st:
        st = _default_growlight_state()
        _atomic_write_json(GROWLIGHT_STATE_FILE, st)
    return _normalize_growlight_state(st)

def save_growlight_state_unlocked(st: dict):
    _atomic_write_json(GROWLIGHT_STATE_FILE, _normalize_growlight_state(st))

def update_growlight_state_transaction(mutator_fn):
    def _txn():
        st = get_growlight_state_unlocked()
        st2 = mutator_fn(st)
        if isinstance(st2, dict):
            st = st2
        st = _normalize_growlight_state(st)
        st["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_growlight_state_unlocked(st)
        return st
    return _with_file_lock(GROWLIGHT_LOCK_FILE, _txn)

def get_growlight_state() -> dict:
    return _with_file_lock(GROWLIGHT_LOCK_FILE, get_growlight_state_unlocked)

# ============================================================
# Routes
# ============================================================
@app.route("/")
def index():
    return render_template("index.html", stream_url=STREAM_URL)

@app.route("/data")
def data():
    try:
        state = read_state()
        sensors = state.get("sensors", {}) if state else {}
        weather = get_weather()

        dosing = _normalize_dosing_state(get_dosing_state())
        maintenance = calculate_maintenance()

        return jsonify({
            "sensors": sensors,
            "weather": weather,
            "dosing": dosing,
            "maintenance": maintenance,
            "growlight": get_growlight_state()
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# -------------------------
# Manual dosing routes (protected if AVFFM_KEY set)
# -------------------------
@app.route("/dose/ph_up", methods=["POST"])
def dose_ph_up():
    deny = _deny_if_no_key()
    if deny:
        return deny
    ok, st = _manual_action_common("PH_UP", PH_UP_PIN)
    return jsonify({"ok": ok, "action": "PH_UP", "seconds": MANUAL_DOSE_SECONDS,
                    "status": st.get("status"), "note": st.get("note", "")})

@app.route("/dose/ph_down", methods=["POST"])
def dose_ph_down():
    deny = _deny_if_no_key()
    if deny:
        return deny
    ok, st = _manual_action_common("PH_DOWN", PH_DOWN_PIN)
    return jsonify({"ok": ok, "action": "PH_DOWN", "seconds": MANUAL_DOSE_SECONDS,
                    "status": st.get("status"), "note": st.get("note", "")})

@app.route("/dose/tds_up", methods=["POST"])
def dose_tds_up():
    deny = _deny_if_no_key()
    if deny:
        return deny
    ok, st = _manual_action_common("TDS_UP", TDS_UP_PIN)
    return jsonify({"ok": ok, "action": "TDS_UP", "seconds": MANUAL_DOSE_SECONDS,
                    "status": st.get("status"), "note": st.get("note", "")})

# -------------------------
# Growlight routes (protected if AVFFM_KEY set)
# -------------------------
@app.route("/growlight/state", methods=["GET"])
def growlight_state():
    return jsonify({"ok": True, "growlight": get_growlight_state()})

@app.route("/growlight/mode", methods=["POST"])
def growlight_mode():
    deny = _deny_if_no_key()
    if deny:
        return deny

    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode") or "").upper().strip()
    if mode not in ("SCHEDULE", "SENSOR", "MANUAL"):
        return jsonify({"ok": False, "error": "invalid_mode"}), 400

    st = update_growlight_state_transaction(lambda s: {**s, "mode": mode, "note": f"Mode set to {mode}"})
    return jsonify({"ok": True, "growlight": st})

@app.route("/growlight/manual", methods=["POST"])
def growlight_manual():
    deny = _deny_if_no_key()
    if deny:
        return deny

    payload = request.get_json(silent=True) or {}
    on = bool(payload.get("on"))

    def _mut(s):
        s["mode"] = "MANUAL"
        s["enabled"] = bool(on)
        s["manual_on"] = bool(on)
        s["note"] = "Manual ON" if on else "Manual OFF"
        return s

    st = update_growlight_state_transaction(_mut)
    return jsonify({"ok": True, "growlight": st})

@app.route("/growlight/brightness", methods=["POST"])
def growlight_brightness():
    deny = _deny_if_no_key()
    if deny:
        return deny

    payload = request.get_json(silent=True) or {}
    try:
        b = float(payload.get("brightness"))
    except Exception:
        return jsonify({"ok": False, "error": "invalid_brightness"}), 400
    b = max(0.0, min(1.0, b))

    st = update_growlight_state_transaction(lambda s: {**s, "brightness": b, "note": f"Brightness {int(b*100)}%"})
    return jsonify({"ok": True, "growlight": st})

@app.route("/growlight/schedule", methods=["POST"])
def growlight_schedule():
    deny = _deny_if_no_key()
    if deny:
        return deny

    payload = request.get_json(silent=True) or {}
    on_t  = str(payload.get("on")  or "").strip()
    off_t = str(payload.get("off") or "").strip()

    try:
        datetime.strptime(on_t, "%H:%M")
        datetime.strptime(off_t, "%H:%M")
    except Exception:
        return jsonify({"ok": False, "error": "invalid_time_format"}), 400

    def _mut(s):
        s["schedule"] = {"on": on_t, "off": off_t}
        s["note"] = f"Schedule {on_t}-{off_t}"
        return s

    st = update_growlight_state_transaction(_mut)
    return jsonify({"ok": True, "growlight": st})

@app.route("/growlight/target", methods=["POST"])
def growlight_target():
    deny = _deny_if_no_key()
    if deny:
        return deny

    payload = request.get_json(silent=True) or {}
    try:
        t = float(payload.get("target_680_pct"))
    except Exception:
        return jsonify({"ok": False, "error": "invalid_target"}), 400

    t = max(0.0, min(100.0, t))
    st = update_growlight_state_transaction(lambda s: {**s, "target_680_pct": t, "note": f"Target 680 = {t}%"})
    return jsonify({"ok": True, "growlight": st})

# -------------------------
# Maintenance routes (protected if AVFFM_KEY set)
# -------------------------
@app.route("/maintenance/reset/<item>", methods=["POST"])
def maintenance_reset(item):
    deny = _deny_if_no_key()
    if deny:
        return deny

    if item not in MAINT_ITEMS:
        return jsonify({"ok": False, "error": "unknown_item"}), 400
    reset_maintenance_item(item)
    return jsonify({"ok": True, "maintenance": calculate_maintenance()})

@app.route("/maintenance/restart", methods=["POST"])
def maintenance_restart():
    deny = _deny_if_no_key()
    if deny:
        return deny

    # Friendly error if admin hash missing
    if not _load_admin_hash():
        return jsonify({"ok": False, "error": "Admin password not configured. Create data/admin_pass.hash"}), 400

    payload = request.get_json(silent=True) or {}
    pw = (payload.get("password") or "").strip()

    if not _check_password(pw):
        return jsonify({"ok": False, "error": "Wrong password"}), 403

    now_iso = datetime.now().isoformat(timespec="seconds")

    def _mut(st):
        st = _ensure_maintenance_state(st)
        st["last_restart"] = now_iso
        return st

    st = update_maintenance_state_transaction(_mut)

    subprocess.Popen(
        ["bash", "-lc", "sleep 1; sudo /sbin/reboot"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    return jsonify({
        "ok": True,
        "last_restart": st.get("last_restart", now_iso),
        "maintenance": calculate_maintenance()
    })

# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    print("🟢 AVFFM Flask starting on 127.0.0.1 by default (set HOST=0.0.0.0 for LAN)")
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=False)