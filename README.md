# 🌱 Hydroponic World – AVFFM  
### Advanced Vertical Farm Fusion Module

Hydroponic World (AVFFM) is a **real-time hydroponic monitoring and automation system** built for Indian and Global farmers.

It combines IoT, automation, and data visualization to create a **low-cost, reliable, and scalable vertical farming system**.

---

# 🖥 AVFFM Dashboard Preview

## 📊 Sensor & Automation Overview

<p align="center">
  <img width="1906" height="1001" alt="AVFFM Dashboard Overview"
       src="https://github.com/user-attachments/assets/06111f1f-bae9-4055-8d71-2db7acffaf9e" />
</p>

---

## 📹 Camera Feed & System Metrics

<p align="center">
  <img width="1906" height="1001" alt="AVFFM Camera & System Metrics"
       src="https://github.com/user-attachments/assets/6d444358-44d9-4a6a-b286-45ffae812c9c" />
</p>

---

# 🌍 Impact for Indian & Global Farmers

## 🇮🇳 India

- Water-efficient farming (up to 90% less water)
- Suitable for terrace and urban farming
- Low-cost alternative to imported systems
- Enables small farmers to adopt vertical farming

## 🌎 Global

- Modular and scalable
- Edge-device friendly
- Suitable for greenhouse and indoor vertical farms
- Enables data-driven agriculture

---


# 🚜 Why This Project Exists

Traditional farming faces:

- 🌧️ Climate unpredictability  
- 💧 Water wastage  
- 🧪 Poor nutrient balance  
- 📉 Yield instability  
- 🏙️ Urban land shortage  

Hydroponic World solves these using:

- Precise nutrient dosing  
- Programmable growlight automation  
- Real-time monitoring dashboard  
- Vertical farming compatibility  
- Raspberry Pi-based edge architecture  

---

# 🏗 System Architecture

```
Sensors  →  sensor_daemon.py
        ↓
JSON State (data/*.json)
        ↓
Flask API (app.py)
        ↓
Web Dashboard (web/templates + static)
```

Automation Services:
- `doser_daemon.py`
- `growlight_daemon.py`
- `lcd_daemon.py`

---

# 📂 Project Structure

```
AVFFM/
│
├── data/               # JSON state storage and logs CSV 
├── hardware/           # 3D printable designs / hardware models
├── services/           # Backend automation logic
├── shared/             # Shared state + GPIO control logic
├── web/                # Flask app, templates, static files
│
├── sensor_daemon.py
├── doser_daemon.py
├── growlight_daemon.py
├── lcd_daemon.py
└── app.py              # Main Flask server
```

---

# ⚙️ Core Features

## 📊 Real-Time Monitoring

- pH
- TDS (ppm)
- Water temperature
- DHT temperature & humidity
- 8-band wavelength sensor (415nm–680nm)
- System health (CPU, RAM, Storage)

---

## 💧 Smart Nutrient Dosing

- Auto & Manual dosing support
- Daily safety limit
- Persistent state tracking
- Peristaltic pump calibration  
  `10ml = 30 seconds runtime`

### Daily Protection

```python
DEFAULT_MAX_ACTIONS_PER_DAY = 7
```

Prevents over-dosing or accidental flooding.

---

## 💡 Intelligent Growlight Control

The growlight system operates in three distinct control modes:

### 🔹 `MANUAL`
- Direct ON / OFF control from the dashboard  
- Immediate override of any automation logic  
- Used for testing, inspection, or emergency control  

### 🔹 `SCHEDULE`
- Automatically turns growlight ON and OFF based on predefined time window  
- Ensures consistent daily light cycles  
- Suitable for stable vegetative and flowering growth stages  

### 🔹 `SENSOR`
- Light behavior depends on environmental conditions  
- Can react to wavelength or system-triggered thresholds  
- Designed for adaptive, data-driven plant growth  

> Note: Brightness levels and schedule timings are system-defined for stability and are not user-configurable from the dashboard.

---

## 🔧 Maintenance Management

Tracks:

- Sensor cleaning interval
- Water change schedule
- Tank cleaning schedule
- Secure Raspberry Pi restart

---

# 🔐 Security Model

## 1️⃣ Local-First Design

Default:

```python
app.run(host="127.0.0.1", port=5000)
```

This means:
- Not exposed publicly
- Accessible only locally

To expose on LAN:

```
HOST=0.0.0.0
```

To expose globally:
Use **Cloudflare Tunnel (recommended)**.

---

## 2️⃣ Restart Password Protection

Restart route requires password verification.

Create file:

```
data/admin_pass.hash
```

Generate hash:

```python
from werkzeug.security import generate_password_hash
print(generate_password_hash("your_password_here"))
```

Paste output into:

```
data/admin_pass.hash
```

---

## 3️⃣ Atomic State Management

- File locks prevent race conditions  
- Atomic JSON writes prevent corruption  
- No database dependency  
- Services and UI are separated  

---

# 🐍 Installation Guide

## 1️⃣ Clone Repository

```
git clone https://github.com/YOUR_USERNAME/Hydroponic-World.git
cd Hydroponic-World/AVFFM
```

---

## 2️⃣ Create Virtual Environment

```
python3 -m venv .venv
source .venv/bin/activate
```

Windows:

```
.venv\Scripts\activate
```

---

## 3️⃣ Install Dependencies

```
pip install flask psutil RPi.GPIO
```

Or if `requirements.txt` exists:

```
pip install -r requirements.txt
```

---

## 4️⃣ Run Services (Separate Terminals)

```
python sensor_daemon.py
python doser_daemon.py
python growlight_daemon.py
python lcd_daemon.py
```

---

## 5️⃣ Start Flask Server

```
python app.py
```

Open:

```
http://127.0.0.1:5000
```


# 🏙 Vertical Farming Compatibility

Supports:

- Nutrient film technique systems
- Aeroponics tower
- Deep water culture systems
- Tower vertical farming
- Indoor grow racks

Because:

- Lighting is programmable
- Nutrients are controlled
- Environment is continuously monitored

---

# 🧠 Technical Philosophy

- Separation of UI, automation, and hardware layers
- Lock-based state management
- Crash-resistant architecture
- Raspberry Pi optimized
- No heavy infrastructure dependency

---

# 🚀 Future Roadmap

- AI-based nutrient prediction
- ML-driven growth optimization
- Mobile dashboard
- MQTT distributed farms
- Multi-farm management

---

# 👨‍💻 Built For

VIOS Hackathon  

Designed as a real-world hydroponic automation system.

Made with ❤️ by VIOS Team 73

---

# 📜 License

MIT License

---

# 🌱 AVFFM

**Empowering Smart, Sustainable, and Scalable Farming**
