# Single source of truth for simulator behaviour and flower identity.
# All identity values derive from FLOWER_ID so multiple sim instances
# can run side-by-side without colliding on the same CSE or mDNS name.
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

# FLOWER_ID is the only value read from the environment.
# Every identity field below is derived from it so each flower instance
# gets a unique CSE ID, CSE name, AE originator, and mDNS service name.
_fid = os.getenv("FLOWER_ID", "1")

_acme_port    = 8080 + int(_fid)
MN_CSE_HOST   = f"http://localhost:{_acme_port}"  # ACME MN-CSE on the flower host
CSE_ID        = f"id-mn-flower-{_fid}"     # unique node ID on the oneM2M network
CSE_NAME      = f"cse-mn-flower-{_fid}"    # human-readable name used in URLs and dashboard
AE_NAME       = "SmartFlower"              # Application Entity owning all containers
AE_ORIGINATOR = f"Csmartflower{_fid}"      # credential token used by the AE to talk to its CSE
FLOWER_NAME   = f"flower-{_fid}"           # name advertised over mDNS
RVI           = "3"                        # oneM2M Release Version Indicator

CONTAINERS = ["soil_moisture", "water_level", "pump_status", "heartbeat"]
WATER_CRITICAL_THRESHOLD = 10

# Simulation timing
TICK_INTERVAL = 2    # seconds between readings
REFILL_WAIT   = 30   # seconds before simulated refill

# Drying parameters
MOISTURE_DROP       = 22.0   # % lost per idle tick
MOISTURE_THRESHOLD  = 30.0   # pump fires below this
MOISTURE_AFTER_PUMP = 90.0   # soil target after full pump cycle
MOISTURE_START      = 100.0

# Water bottle parameters
WATER_START     = 100.0
WATER_PER_PUMP  = 10.0    # total % used per pump activation
WATER_EMPTY     = 0.0
MAX_PUMP_CYCLES = 5

# Pump behaviour
PUMP_DURATION   = 4       # ticks the pump stays ON per activation

# mDNS
MDNS_SERVICE_TYPE = "_onem2m._tcp.local."
MDNS_SERVICE_NAME = f"flower-{_fid}-cse._onem2m._tcp.local."

# Butler originator — used by the flower's ACP to grant subscribe access
# without the butler needing to impersonate the flower's own AE.
BUTLER_AE_ORIGINATOR = "Csmartbutler"
