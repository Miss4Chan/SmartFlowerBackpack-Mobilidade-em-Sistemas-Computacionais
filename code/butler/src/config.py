import os
import socket
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

# Butler MN-CSE identity
BUTLER_CSE_HOST      = "http://localhost:8082"
BUTLER_CSE_ID        = "id-mn-butler"
BUTLER_CSE_NAME      = "cse-mn-butler"
BUTLER_AE_NAME       = "SmartButler"
BUTLER_AE_ORIGINATOR = "Csmartbutler"
RVI                  = "3"

ALERT_REPEAT_INTERVAL_S = 1800   # re-send Telegram every 30 min while alert is active

# Butler CSE port (must match acme.ini httpPort)
BUTLER_CSE_PORT = 8082

# Dashboard (SSE + web)
NOTIFIER_PORT      = 5000
# HTTP endpoint where flowers POST their registration details
REGISTRATION_PORT  = 5001

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# mDNS
MDNS_SERVICE_TYPE   = "_onem2m._tcp.local."
BUTLER_SERVICE_NAME = "butler-cse._onem2m._tcp.local."


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError as e:
        print(
            f"[config] ERROR: cannot determine local IP address.\n"
            f"         Is a network interface up? ({e})\n"
            f"         Connect to a network and restart the butler."
        )
        raise SystemExit(1) from e


BUTLER_HOST = _get_local_ip()
