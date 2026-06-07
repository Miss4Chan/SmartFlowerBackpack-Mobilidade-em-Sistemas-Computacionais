# SmartFlowerBackpack

A peer-to-peer IoT plant monitoring and watering system built on two Raspberry Pi devices using the [ACME CSE](https://github.com/ankraft/ACME-oneM2M-CSE) oneM2M middleware and mDNS for zero-configuration discovery. No cloud required.

Without the use of Raspberry Pi's, the Flowers can also be simulated using the **simulator** in **code/flower/sim**

- The **flower** node reads soil moisture and water level, controls a water pump, and publishes sensor data to its local oneM2M CSE.
- The **butler** node discovers flowers on the LAN, mirrors their data, checks alert thresholds, and serves a live dashboard with Telegram alerts.

---

## Project Structure

```
SmartFlowerBackpack/
├── code/
│   ├── flower/          # Flower node — RPi hardware + simulator
│   └── butler/          # Butler node — discovery, notifier, dashboard
└── docs/
    ├── FLOWER.md        # Flower setup and module reference
    ├── BUTLER.md        # Butler setup and module reference
    ├── overview.drawio              # System architecture
    ├── discovery-sequence.drawio   # Bootstrap and discovery sequence
    ├── flower-logic-flow.drawio    # Flower runtime logic
    ├── notification-flow.drawio    # Sensor data → dashboard / Telegram
    └── hardware-diagram.drawio     # RPi wiring diagram
```

---

## Setup

Extended documentation and build setup can be found for both **flower** and **butler** in their respective .md files listed below.

- **Flower** — see [`docs/FLOWER.md`](docs/FLOWER.md)
- **Butler** — see [`docs/BUTLER.md`](docs/BUTLER.md)

Setup order does not matter (start the butler before the flowers is good advice but not a hard requirement).
