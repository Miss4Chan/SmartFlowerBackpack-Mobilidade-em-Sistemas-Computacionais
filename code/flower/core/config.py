import os
from dotenv import load_dotenv

# loads and reads .env that is one level up from the config.py. Crawls up from ./flower/core to ./flower to get .env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

_fid = os.getenv("FLOWER_ID", "1")  # holds the value of flower_id

FLOWER_NAME   = f"flower-{_fid}"    # names the flower w/ _fid value -> "flower-1"

# oneM2M
_acme_port    = 8080 + int(_fid)                    # sets acme port to be 8080 + _fid so flower 1 is on 8081
MN_CSE_HOST   = f"http://localhost:{_acme_port}"    # stores base url of ACME isntance. port is derived from acme_port so flower1 talks to port 8081, flower2 port 8082, etc
CSE_ID        = f"id-mn-flower-{_fid}"              # stores base mn-id derived from _fid
CSE_NAME      = f"cse-mn-flower-{_fid}"             # stores base mn-cse derived from _fid
AE_NAME       = "SmartFlower"                       # stores AE name
AE_ORIGINATOR = f"Csmartflower{_fid}"               # identity token of the flower when making requests to its acme sce
RVI           = "3"                                 # Release Version indicator, tells amce sce what is the version of the oneM2M standard. oneM2M release 3
CONTAINERS    = ["soil_moisture", "water_level", "pump_status", "heartbeat"] # list of oneM2M conatiners names flower creates on its CSE during setup 

# pump and HW-307 relay GPIO
PUMP_PIN = 22   # GPIO 22 (BCM)

# SPI — MCP3008 -> consists of multiple pins
SPI_BUS      = 0        # GPIO 10 - MOSI sends data default to 0
SPI_DEVICE   = 0        # GPIO 9 - MISO sends data default to 0
SPI_SPEED_HZ = 1350000  # GPIO 11 - SCLK (serial clock) runs at 1.35Mhz
SOIL_CHANNEL = 7        # GPIO 8 - CE0 transmits to MCP3008 channel 7

# I2C — water level sensor -> consists of 2 pins 
# GPIO 2 — SDA (Serial Data)
# GPIO 3 — SCL (Serial Clock)
I2C_BUS         = 1     # physical I2C bus on the RPI to use (bus 1 in this case). SDA on GPIO 2, SCL on GPIO 3
WATER_ADDR_LOW  = 0x77  # memory address for 8 bottom pads
WATER_ADDR_HIGH = 0x78  # memory address for 12 top pads

# Soil Sensor calibration (raw ADC values)
# TODO: these are calibration values. SOIL_WET ranges from 0-1023
SOIL_WET_RAW = 550
SOIL_DRY_RAW = 0

# Thresholds (%)
MOISTURE_THRESHOLD       = 20   # soil moisture sensor threshold
WATER_WARNING_THRESHOLD  = 30   # water level sensor threshold (30 = 3cm or 6 pads)
WATER_CRITICAL_THRESHOLD = 10   # water level sensor threshold (10 = 1cm or 2 pads)

# Timing (seconds)
MAIN_LOOP_INTERVAL  = 2    # how often sensors are read and pump decision is made
SIDE_LOOP_INTERVAL  = 10   # how often water level is checked independently
SETUP_RETRY_DELAY   = 10    # number of seconds main.py waits before retrying the setup if it fails on boot
HEARTBEAT_INTERVAL  = 120   # how often (in seconds) side loops posts a timestamp to the heartbeat container on the CSE
PUMP_MAX_ON_TIME    = 5    # maximum seconds the pump stays ON per activation

# Butler originator — used by the flower's ACP to grant subscribe access
# without the butler needing to impersonate the flower's own AE.
BUTLER_AE_ORIGINATOR = "Csmartbutler"

# mDNS
MDNS_SERVICE_TYPE = "_onem2m._tcp.local."
MDNS_SERVICE_NAME = f"flower-{_fid}-cse._onem2m._tcp.local."
