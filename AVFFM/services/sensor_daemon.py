import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time
import json
import shutil
from datetime import datetime

import psutil
import board
import busio

from adafruit_ads1x15.ads1115 import ADS1115
from adafruit_ads1x15.analog_in import AnalogIn
import adafruit_as7341
import adafruit_dht

from shared.state import write_state
from shared.config import PH_MIN, PH_MAX, TDS_MIN, TDS_MAX

CSV_FILE = "data/sensor_log.csv"

# =========================
# I2C setup
# =========================
i2c = busio.I2C(board.SCL, board.SDA)

# =========================
# ADS1115 (TDS + pH)
# =========================
ads = ADS1115(i2c)
tds_channel = AnalogIn(ads, 0)
ph_channel  = AnalogIn(ads, 1)

# Calibrated constants (from your setup)
TDS_CALIBRATION_FACTOR = 7.26
PH_NEUTRAL_VOLTAGE = 1.5
PH_SLOPE = 0.6867
PH_OFFSET = -3.67

def read_tds(voltage):
    return round((voltage / 5.0) * 1000 * TDS_CALIBRATION_FACTOR, 2)

def read_ph(voltage):
    return round(7.0 + ((voltage - PH_NEUTRAL_VOLTAGE) / PH_SLOPE) + PH_OFFSET, 2)

# =========================
# AS7341 (Wavelength)
# =========================
as7341 = adafruit_as7341.AS7341(i2c)

def get_percent(value, total):
    return round((value / total) * 100, 2) if total else 0

def read_wavelength():
    clear = as7341.channel_clear
    return {
        "415": get_percent(as7341.channel_415nm, clear),
        "445": get_percent(as7341.channel_445nm, clear),
        "480": get_percent(as7341.channel_480nm, clear),
        "515": get_percent(as7341.channel_515nm, clear),
        "555": get_percent(as7341.channel_555nm, clear),
        "590": get_percent(as7341.channel_590nm, clear),
        "630": get_percent(as7341.channel_630nm, clear),
        "680": get_percent(as7341.channel_680nm, clear),
    }

# =========================
# DHT11 (GPIO4)
# =========================
dht = adafruit_dht.DHT11(board.D4)
_last_dht_temp = 0.0
_last_dht_hum  = 0.0

def read_dht():
    global _last_dht_temp, _last_dht_hum
    try:
        t = dht.temperature
        h = dht.humidity
        _last_dht_temp = t
        _last_dht_hum = h
        return {"temp": t, "humidity": h}
    except Exception:
        return {"temp": _last_dht_temp, "humidity": _last_dht_hum}

# =========================
# Water Temp (DS18B20 via 1-Wire sysfs)
# =========================
W1_BASE = "/sys/bus/w1/devices"
_last_water_temp = None
_cached_w1_path = None

def _find_w1_slave_path():
    global _cached_w1_path
    if _cached_w1_path and os.path.exists(_cached_w1_path):
        return _cached_w1_path

    try:
        for name in os.listdir(W1_BASE):
            if name.startswith("28-"):
                p = os.path.join(W1_BASE, name, "w1_slave")
                if os.path.exists(p):
                    _cached_w1_path = p
                    return p
    except Exception:
        pass
    return None

def read_water_temp():
    """
    Returns float °C or None if not available.
    Uses fallback last value if read fails.
    """
    global _last_water_temp
    path = _find_w1_slave_path()
    if not path:
        return _last_water_temp

    try:
        with open(path, "r") as f:
            lines = f.read().strip().splitlines()
        if len(lines) < 2:
            return _last_water_temp

        # Example second line: "... t=26562"
        if "t=" not in lines[1]:
            return _last_water_temp

        t_milli = int(lines[1].split("t=")[-1].strip())
        temp_c = round(t_milli / 1000.0, 2)
        _last_water_temp = temp_c
        return temp_c
    except Exception:
        return _last_water_temp

# =========================
# System metrics (MATCH FRONTEND)
# =========================
def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return round(float(f.read()) / 1000.0, 2)
    except Exception:
        return None

def get_system_info():
    cpu_usage = psutil.cpu_percent(interval=None)
    cpu_temp = get_cpu_temp()

    ram = psutil.virtual_memory()
    disk = shutil.disk_usage("/")

    return {
        "cpu_usage": round(cpu_usage, 1),
        "cpu_temp": cpu_temp,

        # frontend cards need these:
        "ram_usage": round(ram.percent, 1),
        "ram_used": f"{round(ram.used/1024/1024, 0)} MB",

        "storage_usage": round((disk.used/disk.total)*100, 1),
        "storage_used": f"{round(disk.used/1024/1024/1024, 2)} GB",

        # keep detailed too (optional)
        "ram": {"total": round(ram.total/(1024**2), 2), "used": round(ram.used/(1024**2), 2), "percent": ram.percent},
        "storage": {"total": round(disk.total/(1024**3), 2), "used": round(disk.used/(1024**3), 2), "percent": round((disk.used/disk.total)*100, 2)}
    }

# =========================
# CSV logger
# =========================
def ensure_csv_header():
    header = [
        "timestamp",
        "ph","tds",
        "air_temp","air_humidity",
        "water_temp",
        "wl_415","wl_445","wl_480","wl_515","wl_555","wl_590","wl_630","wl_680",
        "cpu_usage","cpu_temp","ram_usage","ram_used","storage_usage","storage_used"
    ]
    try:
        with open(CSV_FILE, "r") as f:
            if f.readline().strip():
                return
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(CSV_FILE) or ".", exist_ok=True)
    with open(CSV_FILE, "w") as f:
        f.write(",".join(header) + "\n")

def append_csv(state):
    s = state.get("sensors", {})
    dhtv = s.get("dht", {}) or {}
    wlv  = s.get("wavelength", {}) or {}
    sysv = s.get("system", {}) or {}

    row = [
        state.get("timestamp",""),
        str(s.get("ph","")), str(s.get("tds","")),
        str(dhtv.get("temp","")), str(dhtv.get("humidity","")),
        str(s.get("water_temp","")),
        str(wlv.get("415","")), str(wlv.get("445","")), str(wlv.get("480","")), str(wlv.get("515","")),
        str(wlv.get("555","")), str(wlv.get("590","")), str(wlv.get("630","")), str(wlv.get("680","")),
        str(sysv.get("cpu_usage","")), str(sysv.get("cpu_temp","")),
        str(sysv.get("ram_usage","")), str(sysv.get("ram_used","")),
        str(sysv.get("storage_usage","")), str(sysv.get("storage_used","")),
    ]
    with open(CSV_FILE, "a") as f:
        f.write(",".join(row) + "\n")

# =========================
# Main loop
# =========================
def main():
    print("🟢 Sensor Daemon started (DIRECT SENSOR READ) -> latest_state.json + CSV")
    ensure_csv_header()

    last_csv = 0
    while True:
        try:
            tds_voltage = tds_channel.voltage
            ph_voltage  = ph_channel.voltage

            tds = read_tds(tds_voltage)
            ph  = read_ph(ph_voltage)

            wl = read_wavelength()
            dht_data = read_dht()
            water_temp = read_water_temp()

            sysinfo = get_system_info()

            ph_ok = (PH_MIN <= ph <= PH_MAX)
            tds_ok = (TDS_MIN <= tds <= TDS_MAX)

            state = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "sensors": {
                    "tds": tds,
                    "ph": ph,
                    "wavelength": wl,
                    "dht": dht_data,
                    "water_temp": water_temp,
                    "system": sysinfo,
                    "status": {"ph_ok": ph_ok, "tds_ok": tds_ok}
                }
            }

            write_state(state)

            now = time.time()
            if now - last_csv >= 30:
                append_csv(state)
                last_csv = now

        except Exception as e:
            write_state({"timestamp": datetime.now().isoformat(timespec="seconds"), "error": str(e), "sensors": {}})
            print("🔴 sensor_daemon error:", e)

        time.sleep(2)

if __name__ == "__main__":
    main()