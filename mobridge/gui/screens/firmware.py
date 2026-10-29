import os

from ... import fif
from ..qt import QtWidgets, Qt
from .base import Screen


MAX_SCAN_DEPTH = 4      # e.g. mobridge firmware / our firmwares / bug-fix / x.fif
MAX_SCAN_FILES = 500


def find_fifs(folder, max_depth=MAX_SCAN_DEPTH, limit=MAX_SCAN_FILES):
    """Sorted .fif paths in folder and its subfolders, up to max_depth levels
    below it and at most limit files (guards against picking a whole drive)."""
    found = []
    root_depth = folder.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(folder):
        if dirpath.rstrip(os.sep).count(os.sep) - root_depth >= max_depth:
            dirnames[:] = []
        dirnames.sort()
        for name in sorted(filenames):
            if name.lower().endswith('.fif'):
                found.append(os.path.join(dirpath, name))
                if len(found) >= limit:
                    return sorted(found)
    return sorted(found)


class FirmwareScreen(Screen):
    """UI_SPEC §5: .fif update + recovery, with a firmware library.

    Point the library at any folder ("Add firmware folder…"); it lists every .fif
    inside and, on a single click, shows that image's details and changelog:
    version, product id, MD5 and validity read from the header, plus a changelog
    from a sidecar .txt (same name) or the image's own build labels.

    Offline, the file checks (product id, MD5, bootblock guard) all run and the
    Flash button only enables for a valid image; the flash itself is gated LOCKED
    (the user's go-ahead + a recovery dry-run first).
    """
    title = 'Firmware'

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self.info = None
        lay = QtWidgets.QVBoxLayout(self)
        self.header(lay, 'Firmware Update', 'Flash a .fif image, or recover a device stuck in BOOTS mode.')

        # --- firmware library -------------------------------------------------
        librow = QtWidgets.QHBoxLayout()
        self.add_folder = QtWidgets.QPushButton('Add firmware folder…')
        self.folder_lbl = QtWidgets.QLabel('No folder added yet.')
        librow.addWidget(self.add_folder); librow.addWidget(self.folder_lbl, 1)
        lay.addLayout(librow)

        self.library = QtWidgets.QListWidget()
        self.library.setMaximumHeight(140)
        lay.addWidget(self.library)

        row = QtWidgets.QHBoxLayout()
        self.path = QtWidgets.QLineEdit(); self.path.setPlaceholderText('…or pick a single .fif image')
        browse = QtWidgets.QPushButton('Browse…')
        row.addWidget(self.path, 1); row.addWidget(browse)
        lay.addLayout(row)

        self.report = QtWidgets.QPlainTextEdit(); self.report.setReadOnly(True)
        self.report.setObjectName('fifReport')
        lay.addWidget(self.report, 1)

        self.recovery = QtWidgets.QCheckBox('Recovery mode (device in BOOTS / won’t boot)')
        lay.addWidget(self.recovery)

        self.progress = QtWidgets.QProgressBar(); self.progress.setRange(0, 100)
        lay.addWidget(self.progress)

        btns = QtWidgets.QHBoxLayout()
        self.check_btn = QtWidgets.QPushButton('Check image')
        self.flash_btn = QtWidgets.QPushButton('Flash'); self.flash_btn.setEnabled(False)
        btns.addStretch(1); btns.addWidget(self.check_btn); btns.addWidget(self.flash_btn)
        lay.addLayout(btns)

        self._folders = []
        self.add_folder.clicked.connect(self._add_folder)
        self.library.currentItemChanged.connect(self._library_pick)
        browse.clicked.connect(self._browse)
        self.check_btn.clicked.connect(self._check)
        self.flash_btn.clicked.connect(self._flash)

    # --- library ----------------------------------------------------------
    def _add_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Add a firmware folder')
        if not folder:
            return
        if folder not in self._folders:
            self._folders.append(folder)
        self.folder_lbl.setText('; '.join(self._folders))
        self._rescan()

    def _rescan(self):
        self.library.clear()
        for folder in self._folders:
            for fn in find_fifs(folder):
                it = QtWidgets.QListWidgetItem(os.path.relpath(fn, folder))
                it.setData(Qt.UserRole, fn)
                it.setToolTip(fn)
                self.library.addItem(it)
        if self.library.count() == 0:
            self.report.setPlainText('No .fif images found in the added folder(s).')

    def _library_pick(self, cur, _prev=None):
        if cur is None:
            return
        self.path.setText(cur.data(Qt.UserRole))
        self._check()

    def _changelog_for(self, path):
        """Changelog text for an image: a sidecar .txt (same basename, or a
        CHANGELOG/README in the folder), else the image's own build labels."""
        base = os.path.splitext(path)[0]
        for cand in (base + '.txt', base + '.md',
                     os.path.join(os.path.dirname(path), 'CHANGELOG.txt'),
                     os.path.join(os.path.dirname(path), 'README.txt')):
            if os.path.exists(cand):
                try:
                    with open(cand, encoding='utf-8', errors='replace') as f:
                        return 'Changelog (%s):\n%s' % (os.path.basename(cand), f.read().strip())
                except OSError:
                    pass
        if self.info is not None and self.info.preamble.strip():
            return 'Build labels (from the image):\n%s' % self.info.preamble.strip()
        return 'No changelog found (add a .txt next to the .fif with the same name).'

    def _browse(self):
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Select firmware', '', 'Firmware (*.fif)')
        if fn:
            self.path.setText(fn); self._check()

    def _check(self):
        self.flash_btn.setEnabled(False)
        try:
            self.info, _ = fif.load(self.path.text())
        except (OSError, fif.FifError) as e:
            self.report.setPlainText('Cannot read image:\n%s' % e); return
        # match against the product of the unit this app is showing (connected, or the
        # last one read); with none known only the file checks run
        pid = getattr(getattr(self.engine, 'root', None), 'product', 0) or None
        problems = fif.flash_problems(self.info, pid)
        text = fif.describe(self.info) + '\n\n' + self._changelog_for(self.path.text()) + '\n\n'
        if problems:
            text += 'NOT flashable:\n- ' + '\n- '.join(problems)
        elif pid is None:
            text += 'Image is valid.'
        else:
            text += 'Image is valid and matches this device.'
            self.flash_btn.setEnabled(True)
        self.report.setPlainText(text)

    def _flash(self):
        # Flashing is the riskiest write the app can do, so it stays locked behind the
        # same gate as every DSP write: it needs the user's explicit go-ahead AND a
        # recovery dry-run first (project safety rule). Validation ("Check image") runs
        # freely; the flash itself never executes in this build.
        mode = 'Firmware Recovery (BOOTS)' if self.recovery.isChecked() else 'Firmware Update'
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(mode)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText('Firmware updates are not available in this version yet. '
                    'Use the official mObridge DSP app to update the firmware.')
        box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        box.exec()
