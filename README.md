# Custom Mobridge

An independent Windows desktop app for mObridge DSP amplifiers. It connects over the
same USB link as the official mObridge DSP app and offers the same tuning controls:
speaker mapping, gain, EQ, crossovers, time alignment, presets, live meters and
firmware updates.

SPDX-License-Identifier: GPL-3.0-or-later

## Supported devices

- Built for every mObridge model the official mObridge DSP app supports (DA1, DA2, DA3,
  DA-G2 MOST, A2B and UNICENS families, and the OEM-branded variants). Tested on the
  DA-G2.Pro A2B Ford; reports from other models are welcome.
- Custom features (Rear Chimes, extended output meters) appear only when the unit runs
  matching custom firmware from
  [mobridge-a2b-custom-firmware](https://github.com/leland331/mobridge-a2b-custom-firmware).
  On stock firmware the app behaves like the official one.

## Install

1. Download `CustomMobridge-Setup-<version>.exe` from
   [Releases](https://github.com/leland331/custom-mobridge-app/releases).
2. Run it. The installer is not code-signed, so Windows SmartScreen may show
   "Windows protected your PC" the first time. Click **More info**, then **Run anyway**.
3. The installer adds a Start menu shortcut and, optionally, a desktop icon. Uninstall
   from Windows Settings > Apps.

The USB link uses the FTDI driver that Windows installs automatically. If the device
does not appear, install the FTDI VCP driver from ftdichip.com.

## Use

1. Connect the amplifier by USB and power it on.
2. Start Custom Mobridge. It finds the device and reads its current settings.
3. Adjust settings. As in the official app, changes are sent to the device live.

Without a device the app opens an offline preview, so you can explore it safely.

Settings are kept in `%APPDATA%\mObridge\mObridge DSP\cfg.ini`, the same file the
official app uses.

## Build from source

Python 3.10 or newer:

    pip install -r packaging/requirements-build.txt
    python -m pytest -q tests
    python -m mobridge.gui

The installer is built by `.github/workflows/release.yml` on every `v*` tag.

## Disclaimer

Not affiliated with or endorsed by mObridge. Use at your own risk.

## License

Copyright (C) 2026 leland331. Licensed under the GNU General Public License v3.0 or
later; see [LICENSE](LICENSE).
