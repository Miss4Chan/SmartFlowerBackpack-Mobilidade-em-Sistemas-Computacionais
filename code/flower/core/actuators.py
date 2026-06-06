import RPi.GPIO as GPIO
from config import PUMP_PIN

def init() -> None:
    """
    actuators.py controls the pump and HW-307 relay through GPIO 22 (pin 15)
    init() sets BCM mode, configures pin GPIO 22 as output, and starts with the pump off
    """

    GPIO.setmode(GPIO.BCM)                              # sets GPIO to BCM (internal GPIO line number)
    GPIO.setup(PUMP_PIN, GPIO.OUT, initial=GPIO.HIGH)   # configures GPIO 22 as output and starts the relay OFF

def on() -> None:
    """
    on() sets the GPIO 22 to set LOW (0), closing off the relay contacts,  
    thus turning ON the pump
    """

    GPIO.output(PUMP_PIN, GPIO.LOW) # turns GPIO 22 to LOW, turning ON the pump 

def off() -> None:
    """
    off() sets the GPIO 22 to set HIGH (1), opening the relay contacts,  
    thus turning OFF the pump
    """

    GPIO.output(PUMP_PIN, GPIO.HIGH)    # turns GPIO 22 to HIGH, turning OFF the pump 

def cleanup() -> None:
    """
    cleanup() calls off() to ensure pump is off, then telss GPIO 22 to release all
    GPIO pins back to the OS (safety measure).
    """

    off()           # calls off to turn GPIO 22 to HIHG, turning OFF the pump 
    GPIO.cleanup()  # releases GPIO 22 back to the OS (safety measure in case of ex:crash)
