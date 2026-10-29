"""Pixel-faithful reproduction of the official mObridge DSP v25.8.0 window
(connected state): menu bar, logo header, tab bar, brushed stage, and the five
official tabs plus the Device screen. Owned by the "App UI fidelity" thread.

Geometry is measured from the reference connected walkthrough video, captured on
Windows at 150 % scaling, so every coordinate in this package is written in
*reference pixels* of that capture and converted to Qt logical pixels with the
helpers in :mod:`skin` (``X``/``Y``/``D``). Run ``tools/render_official.py``
to regenerate the reference renders.

Parity: nothing here sends anything. Every value control is read-only; values
whose wire scale is not proven are drawn in the official *disabled* style.
"""
from .window import OfficialMainWindow, run  # noqa: F401
