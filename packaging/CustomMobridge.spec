# PyInstaller spec: one-folder build of the Custom Mobridge desktop app.
# Run from the repository root:  pyinstaller packaging/CustomMobridge.spec
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = os.path.abspath(os.path.join(SPECPATH, '..'))
os.environ['PYTHONPATH'] = root + os.pathsep + os.environ.get('PYTHONPATH', '')
sys.path.insert(0, root)
icon = os.path.join(SPECPATH, 'app_icon.ico')

datas = collect_data_files('mobridge', excludes=['**/__pycache__/*'])
hiddenimports = collect_submodules('mobridge') + collect_submodules('lupa') + collect_submodules('zeroconf')
binaries = collect_dynamic_libs('lupa')

a = Analysis(
    [os.path.join(SPECPATH, 'launch.py')],
    pathex=[root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=['tkinter', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CustomMobridge',
    console=False,
    icon=icon if os.path.exists(icon) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='CustomMobridge')
