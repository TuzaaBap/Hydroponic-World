import sys, os, time, json
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, timedelta, date

from shared.config import PH_MIN, PH_MAX, TDS_MIN, TDS_MAX
from shared.state import read_state

# =========================
# Motor GPIO (BCM pins)
# =========================
PH_UP_PIN   = 7
PH_DOWN_PIN = 8
TDS_UP_PIN  = 25
ACTIVE_LOW = True

# =========================
# Dosing settings
# =========================
DOSE_INTERVAL_HOURS = 4
DOSE_SECONDS = 30  # 30 sec ~ 10ml

# Daily safety: max total dosing actions per day (NOT per motor)
MAX_ACTIONS_PER_DAY = 7

DOSING_STATE_FILE = "data/dosing_state.json"
DOSING_LOCK_FILE  = DOSING_STATE_FILE + ".lock"

# Enabled by default. Disable with:
#   ENABLE_DOSING=0 python services/doser_daemon.py
ENABLE_DOSING = os.getenv("ENABLE_DOSING", "1") == "1"


# =========================
# GPIO helpers
# =========================
def gpio_setup():
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    for pin in (PH_UP_PIN, PH_DOWN_PIN, TDS_UP_PIN):
        GPIO.setup(pin, GPIO.OUT)
        motor_off(GPIO, pin)

    return GPIO


def motor_on(GPIO, pin):
    GPIO.output(pin, GPIO.LOW if ACTIVE_LOW else GPIO.HIGH)


def motor_off(GPIO, pin):
    GPIO.output(pin, GPIO.HIGH if ACTIVE_LOW else GPIO.LOW)


# =========================
# JSON + lock helpers
# =========================
def _atomic_write_json(path: str, obj: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


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


def _load_dosing_state_unlocked() -> dict:
    try:
        with open(DOSING_STATE_FILE, "r") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def load_dosing_state() -> dict:
    return _with_file_lock(DOSING_LOCK_FILE, _load_dosing_state_unlocked)


def update_dosing_state_transaction(mutator_fn):
    """
    Safe read-modify-write with ONE lock (no nested lock).
    """
    def _txn():
        st = _load_dosing_state_unlocked()
        st2 = mutator_fn(st if isinstance(st, dict) else {})
        if isinstance(st2, dict):
            st = st2
        _atomic_write_json(DOSING_STATE_FILE, st)
        return st

    return _with_file_lock(DOSING_LOCK_FILE, _txn)


# =========================
# Logic helpers
# =========================
def _today_str():
    return date.today().isoformat()


def get_next_tick(from_dt: datetime) -> datetime:
    """
    Next 4-hour boundary based on local time:
      00:00, 04:00, 08:00, 12:00, 16:00, 20:00
    """
    interval = DOSE_INTERVAL_HOURS
    hour = from_dt.hour
    next_hour = ((hour // interval) + 1) * interval

    if next_hour >= 24:
        next_dt = (from_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                   + timedelta(days=1))
    else:
        next_dt = from_dt.replace(hour=next_hour, minute=0, second=0, microsecond=0)

    if next_dt <= from_dt:
        next_dt = next_dt + timedelta(hours=interval)

    return next_dt


def decide_action(ph, tds):
    """
    Priority: pH first (UP/DOWN), then TDS_UP.
    Only one action per cycle.
    """
    if ph is None or tds is None:
        return "NO_ACTION"

    if ph < PH_MIN:
        return "PH_UP"
    if ph > PH_MAX:
        return "PH_DOWN"

    if tds < TDS_MIN:
        return "TDS_UP"

    return "NO_ACTION"


def run_action(GPIO, action):
    if action == "PH_UP":
        pin = PH_UP_PIN
    elif action == "PH_DOWN":
        pin = PH_DOWN_PIN
    elif action == "TDS_UP":
        pin = TDS_UP_PIN
    else:
        return

    motor_on(GPIO, pin)
    time.sleep(DOSE_SECONDS)
    motor_off(GPIO, pin)


# =========================
# Main daemon
# =========================
def main():
    print("🟣 Doser daemon started")
    print(f"ENABLE_DOSING={ENABLE_DOSING} (disable with ENABLE_DOSING=0)")
    print(f"MAX_ACTIONS_PER_DAY={MAX_ACTIONS_PER_DAY}")

    GPIO = gpio_setup()

    try:
        while True:
            now = datetime.now()
            next_tick = get_next_tick(now)
            secs_to_tick = max(0, int((next_tick - now).total_seconds()))

            # ---- PRE-TICK: write scheduling info without wiping manual fields ----
            def pre_tick_mutator(st):
                if st.get("day") != _today_str():
                    st["day"] = _today_str()
                    st["actions_today"] = 0

                st["enabled"] = ENABLE_DOSING
                st["now"] = now.isoformat(timespec="seconds")
                st["next_tick"] = next_tick.isoformat(timespec="seconds")
                st["seconds_to_next_tick"] = secs_to_tick

                # ALWAYS overwrite
                st["max_actions_per_day"] = int(MAX_ACTIONS_PER_DAY)

                # preserve manual fields if present
                st.setdefault("actions_today", 0)
                st.setdefault("status", "--")
                st.setdefault("action", "--")
                st.setdefault("last_done", "--")
                st.setdefault("note", "")
                return st

            update_dosing_state_transaction(pre_tick_mutator)

            # sleep until tick
            time.sleep(max(1, secs_to_tick))

            # ---- TICK moment ----
            tick_time = datetime.now()

            def tick_mutator(st):
                if st.get("day") != _today_str():
                    st["day"] = _today_str()
                    st["actions_today"] = 0

                st["enabled"] = ENABLE_DOSING
                st["last_tick"] = tick_time.isoformat(timespec="seconds")
                st["max_actions_per_day"] = int(MAX_ACTIONS_PER_DAY)

                actions_today = int(st.get("actions_today", 0))

                # ✅ CLEAR OLD DAILY_LIMIT IF NOW UNDER LIMIT
                if st.get("status") == "DAILY_LIMIT" and actions_today < MAX_ACTIONS_PER_DAY:
                    st["status"] = "--"
                    st["note"] = ""

                # daily limit gate (blocks auto)
                if actions_today >= MAX_ACTIONS_PER_DAY:
                    st["status"] = "DAILY_LIMIT"
                    st["note"] = "Daily limit reached. Auto dosing paused until tomorrow."
                    return st

                # read sensors
                state = read_state()
                sensors = state.get("sensors", {}) if state else {}
                ph = sensors.get("ph", None)
                tds = sensors.get("tds", None)

                action = decide_action(ph, tds)

                st["ph"] = ph
                st["tds"] = tds
                st["action"] = action
                st["dose_seconds"] = DOSE_SECONDS if action != "NO_ACTION" else 0

                if not ENABLE_DOSING:
                    st["status"] = "DISABLED"
                    st["note"] = ""
                    return st

                if action == "NO_ACTION":
                    st["status"] = "SKIPPED"
                    st["note"] = ""
                    return st

                st["status"] = "DOSING"
                st["note"] = ""
                return st

            st = update_dosing_state_transaction(tick_mutator)

            if st.get("status") != "DOSING":
                continue

            # ---- run motor outside lock ----
            action = st.get("action")
            run_action(GPIO, action)

            # ---- mark DONE + increment counter ----
            done_time = datetime.now().isoformat(timespec="seconds")

            def done_mutator(st2):
                if st2.get("day") != _today_str():
                    st2["day"] = _today_str()
                    st2["actions_today"] = 0

                st2["max_actions_per_day"] = int(MAX_ACTIONS_PER_DAY)
                st2["actions_today"] = int(st2.get("actions_today", 0)) + 1
                st2["status"] = "DONE"
                st2["last_done"] = done_time
                st2["note"] = f"Auto: {action} ({DOSE_SECONDS}s)"
                st2["now"] = done_time
                return st2

            update_dosing_state_transaction(done_mutator)

    except KeyboardInterrupt:
        print("\nStopping doser daemon...")
    finally:
        try:
            for pin in (PH_UP_PIN, PH_DOWN_PIN, TDS_UP_PIN):
                motor_off(GPIO, pin)
        except Exception:
            pass
        try:
            GPIO.cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    main()