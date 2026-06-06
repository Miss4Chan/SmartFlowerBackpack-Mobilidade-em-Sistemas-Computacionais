# SmartFlower systemd Service

Installs the flower as a systemd service so it starts automatically on RPi boot with no manual intervention — plug in, power on, done.

The service calls `scripts/start_rpi.sh`, which starts the ACME MN-CSE → mDNS advertiser → hardware main loop. On exit or crash, systemd restarts it after 10 s.

---

## Prerequisites

Run these once on the RPi before installing the service:

```bash
# 1. Install system packages
sudo apt install -y curl

# 2. Install Python dependencies into the project venv
#    Installs shared deps (requirements.txt) AND hardware deps
#    (core/requirements.txt: RPi.GPIO, spidev, smbus2)
cd <path-to-repo>/code/flower    # wherever you cloned SmartFlowerBackpack
bash scripts/install.sh
```

> **If the venv already exists** and you see `ModuleNotFoundError: No module named 'RPi'`,
> the hardware packages were never installed into it. Fix with:
> ```bash
> cd <path-to-repo>/code/flower
> .venv/bin/pip install -r core/requirements.txt
> ```

---

## Configuration

### 1. Set the flower identity

```bash
cp <path-to-repo>/code/flower/.env.example \
   <path-to-repo>/code/flower/.env
```

Edit `.env` and set:
```
FLOWER_ID=1      # use 2 for the second flower
```

### 2. Check the username in the service file

Open `smartflower.service` and confirm `User=` matches the RPi account:

```bash
whoami   # prints your username
```

If it is not `admin`, update `User=` and the two `/home/admin/` paths in the file.

### 3. Set Telegram credentials (optional — for water alerts)

Add to `.env`:

```
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

---

## Install

```bash
cd <path-to-repo>/code/flower
sudo bash service/install.sh   # auto-detects repo path and user, enables on boot
sudo systemctl start smartflower
```

---

## Useful commands

```bash
# Live logs
journalctl -u smartflower.service -f

# Status
sudo systemctl status smartflower

# Restart
sudo systemctl restart smartflower

# Stop and disable auto-start
sudo systemctl stop smartflower
sudo systemctl disable smartflower
```

---

## Wipe and start fresh (corrupt database)

```bash
sudo systemctl stop smartflower
cd <path-to-repo>/code/flower
bash scripts/reset.sh
sudo systemctl start smartflower
```
