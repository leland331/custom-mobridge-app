"""Build packaging/app_icon.ico from the app's PNG icon (needs Pillow)."""
import os
from PIL import Image

here = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(here, '..', 'mobridge', 'gui', 'assets', 'app_icon.png')
img = Image.open(src).convert('RGBA')
img.save(os.path.join(here, 'app_icon.ico'), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
