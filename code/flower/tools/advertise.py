"""
Advertises the flower MN-CSE on the local network via mDNS.
The flower's CSE identity and AE originator are broadcast in the TXT record
so the butler can identify this node and accept its registration POST.
The SAREF self-description is stored in the flower's ACME as a <semanticDescriptor>
(type 24) and fetched by the butler via a oneM2M GET — not from this advertiser.
Runs until killed — start scripts keep it in the background.
"""

import os
import signal
import socket
import sys
import time
from zeroconf import ServiceInfo, Zeroconf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
from config import (
    CSE_ID, CSE_NAME, AE_ORIGINATOR, FLOWER_NAME,
    MDNS_SERVICE_TYPE, MDNS_SERVICE_NAME, MN_CSE_HOST,
)

SERVICE_TYPE = MDNS_SERVICE_TYPE
SERVICE_NAME = MDNS_SERVICE_NAME
CSE_PORT     = int(MN_CSE_HOST.rsplit(":", 1)[-1])


def get_ip() -> str:
    """
    Connect a UDP socket to a public address — this forces the OS to select
    the correct outbound interface and we read back the local address assigned.
    No packet is actually sent; port 1 is unreachable but the connect still works.
    """

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 1))
    ip = s.getsockname()[0]
    s.close()
    return ip


def main():
    """
    advertises flower on the local network via mDNS so the butler can dicover it without an harcoded IP
    1. get_ip detects the flowers local IP
    2. properties builds the mDNS TXT to record payload (what the butler reads on discovery to learn about flower)
    3. serviceInfo packages IP, port, TXT properties into mDNS service record
    4. zc.register_service broadcasts the record on the local network. from this point the butlers ServiceBrowser
    will fire add_service
    5. shutdown handler for SIGINT/SIGTERM that cleanly unregisters the mDNS record before exiting, so the
    buttler sees a proper remove_service event rather than a stale reocrd
    6. while True: time.sleep keeps process alive to maintain mDNS record. mDNS requires registrant to
    stay running and respond to queries 
    """

    ip = get_ip()
    print(f"[advertise] Local IP: {ip}")
    print(f"[advertise] CSE port: {CSE_PORT}")

    properties = {
        "role":        "publisher",
        "cse-id":      CSE_ID,
        "cse-name":    CSE_NAME,
        "ae-id":       AE_ORIGINATOR,
        "flower-name": FLOWER_NAME,
    }
    print(f"[advertise] mDNS TXT properties:")
    for k, v in properties.items():
        print(f"[advertise]   {k} = {v}")

    info = ServiceInfo(
        SERVICE_TYPE,
        SERVICE_NAME,
        addresses=[socket.inet_aton(ip)],
        port=CSE_PORT,
        properties=properties,
    )

    zc = Zeroconf(interfaces=[ip])
    zc.register_service(info)
    print(f"[advertise] Registered '{SERVICE_NAME}' on mDNS — butler can now discover this flower")

    def shutdown(sig, frame):
        print("[advertise] Shutting down mDNS...")
        zc.unregister_service(info)
        zc.close()
        print("[advertise] mDNS record removed.")
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("[advertise] Keeping mDNS record alive — press Ctrl+C to stop")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
