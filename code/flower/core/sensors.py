import spidev
import smbus2
from config import (
    SPI_BUS, SPI_DEVICE, SPI_SPEED_HZ, SOIL_CHANNEL,
    I2C_BUS, WATER_ADDR_LOW, WATER_ADDR_HIGH,
    SOIL_WET_RAW, SOIL_DRY_RAW,
)

_spi: spidev.SpiDev | None = None   # spidev using SPI protocol talks to the MCP3008 (ADConverter)
_bus: smbus2.SMBus | None  = None   # simbus using I2C protocol talks to the water level sensor

def init() -> None:
    """
    init() modifies the module-level variables _spi and _bus, 
    creates the spi object in memory, opens the SPI connection, 
    setting the clock speed of the SPI bus to run at (1.35Mhz) for the MCP3008,
    opens the I2C bus1, to communicate with the water level sensor

    SPI consists of multiple pins
    - GPIO 10 - CTS sends data
    - GPIO 9 - RXD recieves data
    - GPIO 11 - RTS clock (CLK) syncronises data transfer
    - GPIO 8 - transmit/TDX whitch SPI device to talk to

    I2C consists of 2 pins
    - GPIO 2 - CTS sends data
    - GPIO 3 - RTS clock (CLK) syncronises data transfer
    """

    global _spi, _bus               # declares modification of module level variables for (_spi and _bus)
    print(f"[sensors] Opening SPI bus={SPI_BUS} device={SPI_DEVICE} speed={SPI_SPEED_HZ}Hz")
    _spi = spidev.SpiDev()          # creates SPI object in memory
    _spi.open(SPI_BUS, SPI_DEVICE)  # opens SPI connection
    _spi.max_speed_hz = SPI_SPEED_HZ # sets SPI bus clock  speed to 1.35Mhz for the MCP3008
    print(f"[sensors] Opening I2C bus={I2C_BUS}")
    _bus = smbus2.SMBus(I2C_BUS)    # Opens I2C bus allowing communication to water level sensor
    print("[sensors] Hardware interfaces ready")

def close() -> None:
    """
    close() closes connections to _spi and _bus if they are opened, releasing their
    designated sensors (MCP3008 and water level sensor)
    """

    print("[sensors] Closing hardware interfaces...")
    if _spi:
        _spi.close()    # if _spi was opened, close connection, releasing MCP3008 sensor 
    if _bus:
        _bus.close()    # if _bus was opened, close connection, releasing water level sosnsor
    print("[sensors] Closed")

def read_soil_moisture() -> float:
    """
    read_soil_moisture() sends data and recieves it back about sensor data.
    sends and recieves data to MCP3008 via SPI simultaniously, assembles bits into int (0 - 1023)
    converts raw ADC into percentage using values from config.py and returns the final value,
    rounded to 1 decimal

    TODO: the MCP raw values are divided into 2bytes. The current implementation is incorrectly
    reading one of the bytes, making the value 550 -> 100% when it should be 50%
    """

    adc = _spi.xfer2([1, (8 + SOIL_CHANNEL) << 4, 0])   # sends and recieves 3 bytes to MCP over SPI ()
    raw = ((adc[1] & 3) << 8) + adc[2]                  # raw values of the MCP3008 (0-1023)
    pct = (SOIL_DRY_RAW - raw) / (SOIL_DRY_RAW - SOIL_WET_RAW) * 100    # converts raw number into percentage
    result = max(0.0, min(100.0, round(float(pct), 1))) # rounds to the closes decimal value
    print(f"[sensors] soil: raw={raw}  calibration=[wet={SOIL_WET_RAW}, dry={SOIL_DRY_RAW}]  → {result}%")
    return result   # returns moisture percentage to whoever calls the funtion


def read_water_level() -> float:
    """
    read_water_level() reads and converts water level
    The water level sensor has 20 electrode pads (8 low + 12 high).
    Each pad returns a byte; values > 100 mean the pad is touching water.
    We build a 20-bit integer where bit i=1 if pad i is wet.
    Then count consecutive set bits from bit 0 upward — this is the fill level
    (water fills from the bottom, so pads are contiguous from index 0).
    """
    
    low_data  = _bus.read_i2c_block_data(WATER_ADDR_LOW,  0, 8)     # Reads 8 bytes from lower half of the the snesor (adrress 0x77)
    high_data = _bus.read_i2c_block_data(WATER_ADDR_HIGH, 0, 12)    # Reads 12 bytes from upper half of snesor (0x78)

    touch_val = 0                   # start a 20-bit integer at 0, each bit will represent a pad
    for i in range(8):              # loops lower pads
        if low_data[i] > 100:       # byte value above 100 means pad is touching water
            touch_val |= 1 << i     # sets bit to 1 
    for i in range(12):             # loops through 12 upper pads
        if high_data[i] > 100:      # same check byte value ab 100 means pad touching water
            touch_val |= 1 << (8 + i)   # sets bit to 1

    sections = 0                    # counts the consecutive pads starting at the bottom
    tmp = touch_val                 # works on a copy presenved for the print
    while tmp & 0x01:               # checks is bottom pad is wet
        sections += 1               # counts one more wet section
        tmp >>= 1                   # shigts bits right to check next pad

    result = float(sections * 5)    # each section is 5% -> 20 sections is 100%
    bit_str = format(touch_val, "020b")
    print(f"[sensors] water: bits={bit_str}  sections={sections}/20  → {result}%")
    return result
