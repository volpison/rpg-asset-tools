"""
Minimal fake of the parts of PySide6 that gui.py uses, so the window's LOGIC
can be exercised without Qt installed (CI sandboxes, etc.).
This does NOT validate Qt API usage - only our own code paths.
"""
import os
import sys
import types
import zlib

os.environ["RPGAT_NO_WATCHDOG"] = "1"      # tests must not write into the home folder


class _Meta(type):
    def __getattr__(cls, name):                 # enums: Qt.UserRole, QDialog.Accepted ...
        if name.startswith("__"):
            raise AttributeError(name)
        return _Enum(zlib.crc32(f"{cls.__name__}.{name}".encode()) & 0xFFFF)


class _Enum(int):
    pass


class _Noop:
    def __call__(self, *a, **k):
        return _W()

    def __getattr__(self, n):
        return _Noop()


class _W(metaclass=_Meta):
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return _Noop()


class _Bound:
    def __init__(self):
        self.slots = []

    def connect(self, f):
        self.slots.append(f)

    def emit(self, *a):
        for s in list(self.slots):
            s(*a)


class Signal:
    def __init__(self, *types):
        pass

    def __set_name__(self, owner, name):
        self.name = "_sig_" + name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        if self.name not in obj.__dict__:
            obj.__dict__[self.name] = _Bound()
        return obj.__dict__[self.name]


class QThread(_W):
    def __init__(self, parent=None):
        self.finished = _Bound()

    def start(self):                           # run inline, synchronously
        self.run()
        self.finished.emit()

    def isRunning(self):
        return False


class QTimer(_W):
    def __init__(self, *a):
        self.timeout = _Bound()
        self.fired = 0

    def start(self, *a):
        self.fired += 1

    @staticmethod
    def singleShot(ms, fn):
        if ms == 0:                 # "run after the current event" - inline is good enough here
            fn()


class QSettings(_W):
    def value(self, key, default=None, type=None):
        return default

    def setValue(self, *a):
        pass


class QTableWidgetItem(_W):
    def __init__(self, text=""):
        self._text, self._data, self._fg = text, {}, None

    def text(self):
        return self._text

    def setText(self, t):
        self._text = t

    def data(self, role):
        return self._data.get(int(role))

    def setData(self, role, v):
        self._data[int(role)] = v

    def flags(self):
        return _Enum(0xFFFF)

    def setFlags(self, f):
        pass

    def setForeground(self, b):
        self._fg = b

    def setSelected(self, v):
        if hasattr(self, "_table"):
            (self._table._sel.add if v else self._table._sel.discard)(self._row)

    def __lt__(self, other):
        return self._text < other._text


class _Idx:
    def __init__(self, r):
        self._r = r

    def row(self):
        return self._r


class QTableWidget(_W):
    def __init__(self, rows=0, cols=0, *a):
        self._items, self._rows, self._sel = {}, rows, set()

    def setRowCount(self, n):
        self._rows = n
        self._items = {k: v for k, v in self._items.items() if k[0] < n}
        self._sel = {r for r in self._sel if r < n}

    def rowCount(self):
        return self._rows

    def setItem(self, r, c, item):
        item._table, item._row = self, r
        self._items[(r, c)] = item

    def item(self, r, c):
        return self._items.get((r, c))

    def selectedIndexes(self):
        return [_Idx(r) for r in sorted(self._sel)]

    def selectRow(self, r):
        self._sel = {r}

    def select_rows(self, rows):                # test helper
        self._sel = set(rows)


class QComboBox(_W):
    def __init__(self, *a):
        self._items, self._cur = [], 0

    def addItem(self, text, data=None):
        self._items.append((text, data))

    def clear(self):
        self._items, self._cur = [], 0

    def currentIndex(self):
        return self._cur

    def setCurrentIndex(self, i):
        self._cur = i

    def currentData(self):
        return self._items[self._cur][1] if self._items else None

    def findData(self, d):
        for i, (_, x) in enumerate(self._items):
            if x == d:
                return i
        return -1


class QLineEdit(_W):
    def __init__(self, *a):
        self._t = ""

    def text(self):
        return self._t

    def setText(self, t):
        self._t = t


class QCheckBox(_W):
    def __init__(self, *a):
        self._c = False

    def isChecked(self):
        return self._c

    def setChecked(self, v):
        self._c = bool(v)


class QLabel(_W):
    def __init__(self, text="", *a):
        self._t = text

    def setText(self, t):
        self._t = t

    def text(self):
        return self._t


class QTextEdit(_W):
    def __init__(self, *a):
        self._t = ""

    def toPlainText(self):
        return self._t

    def setPlainText(self, t):
        self._t = t

    def clear(self):
        self._t = ""


class QPlainTextEdit(QTextEdit):
    def appendPlainText(self, t):
        self._t += t + "\n"


class QListWidget(_W):
    def __init__(self, *a):
        self.items = []

    def addItem(self, t):
        self.items.append(t)

    def clear(self):
        self.items = []


class QMessageBox(_W):
    next_accept = True                          # tests flip this
    shown = []

    def __init__(self, *a):
        self._buttons = []

    def addButton(self, *a):
        b = _W()
        self._buttons.append(b)
        return b

    def exec(self):
        QMessageBox.shown.append(self)

    def clickedButton(self):
        return self._buttons[0] if QMessageBox.next_accept else self._buttons[-1]

    @staticmethod
    def question(*a, **k):
        return QMessageBox.Yes if QMessageBox.next_accept else QMessageBox.No

    @staticmethod
    def warning(*a, **k):
        QMessageBox.shown.append(("warning", a))

    information = critical = warning


class QDialog(_W):
    result = "accept"

    def __init__(self, *a):
        pass

    def exec(self):
        return QDialog.Accepted if QDialog.result == "accept" else QDialog.Rejected


class _Btn(_W):
    def setEnabled(self, v):
        self.enabled = v

    def setText(self, t):
        self.text = t


class QDialogButtonBox(_W):
    def __init__(self, *a):
        self.accepted = _Bound()
        self.rejected = _Bound()

    def addButton(self, *a):
        return _Btn()


class QMainWindow(_W):
    def __init__(self, *a):
        pass

    def setCentralWidget(self, w):
        self._central = w

    def centralWidget(self):
        return self._central


class QFileDialog(_W):
    pass


class QAction(_W):
    def __init__(self, text="", parent=None):
        self.text_, self.enabled = text, True
        self.triggered = _Bound()

    def setEnabled(self, v):
        self.enabled = v

    def setText(self, t):
        self.text_ = t


class QMenu(_W):
    def addAction(self, *a):
        return QAction(a[0] if a else "")


class QShortcut(_W):
    def __init__(self, *a):
        self.activated = _Bound()


_SPECIAL = {c.__name__: c for c in (
    QThread, QTimer, QSettings, QTableWidgetItem, QTableWidget, QComboBox,
    QLineEdit, QCheckBox, QLabel, QTextEdit, QPlainTextEdit, QListWidget,
    QMessageBox, QDialog, QDialogButtonBox, QMainWindow, QFileDialog, QAction,
    QMenu, QShortcut)}
_SPECIAL["Signal"] = Signal


def install():
    for modname in ("PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"):
        mod = types.ModuleType(modname)

        def __getattr__(name, _mod=mod):
            if name.startswith("__"):
                raise AttributeError(name)
            cls = _SPECIAL.get(name) or type(name, (_W,), {})
            setattr(_mod, name, cls)
            return cls
        mod.__getattr__ = __getattr__
        sys.modules[modname] = mod
