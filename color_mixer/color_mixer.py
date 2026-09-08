from PyQt5.QtCore import QRect, QSettings, QSize, Qt, QTimer
import traceback
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Krita

from . import mixing
from .color_data import STANDARD_COLORS


def _swatch_icon(hexv, size=18):
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(hexv))
    return QIcon(pixmap)


def _to_qcolor(value, view=None):
    if value is None:
        return None
    if isinstance(value, QColor):
        return value
    canvas = None
    if view is not None:
        try:
            canvas = view.canvas()
        except Exception:
            canvas = None
    for arg in (canvas, None):
        try:
            result = value.colorForCanvas(arg)
            if isinstance(result, QColor):
                return result
        except Exception:
            pass
    for method_name in ("color", "toQColor"):
        try:
            result = getattr(value, method_name)()
            if isinstance(result, QColor):
                return result
            try:
                qc = result.toQColor()
                if isinstance(qc, QColor):
                    return qc
            except Exception:
                pass
        except Exception:
            pass
    try:
        comps = value.componentsOrdered()
        if isinstance(comps, (list, tuple)) and len(comps) >= 3:
            maximum = max(abs(c) for c in comps[:3])
            if maximum > 1.0:
                scale = 1.0
            elif maximum > 0.0:
                scale = 255.0
            else:
                scale = 0.0
            r = int(round(min(max(comps[0] * scale, 0.0), 255.0)))
            g = int(round(min(max(comps[1] * scale, 0.0), 255.0)))
            b = int(round(min(max(comps[2] * scale, 0.0), 255.0)))
            a = 255
            if len(comps) >= 4:
                a = int(round(min(max(comps[3] * scale, 0.0), 255.0)))
            color = QColor(r, g, b, a)
            if color.isValid():
                return color
    except Exception:
        pass
    return None


class StandardColorsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Lista estándar de colores")
        self.resize(360, 480)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrar por nombre o valor hexadecimal...")
        self.search.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.ExtendedSelection)
        self.list_widget.setIconSize(QSize(18, 18))
        for name, hexv in STANDARD_COLORS:
            item = QListWidgetItem(_swatch_icon(hexv), "%s  (%s)" % (name, hexv))
            item.setData(Qt.UserRole, (name, hexv))
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.list_widget.itemDoubleClicked.connect(lambda item: self.accept())

    def _apply_filter(self, text):
        text = text.strip().lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            name, hexv = item.data(Qt.UserRole)
            item.setHidden(text not in name.lower() and text not in hexv.lower())

    def selected_colors(self):
        return [item.data(Qt.UserRole) for item in self.list_widget.selectedItems()]


class MixBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(44)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._segments = []

    def set_mix(self, segments):
        self._segments = segments
        self.setToolTip("")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.fillRect(rect, QColor(60, 60, 60))
        total = sum(weight for _, weight, _ in self._segments)
        x = rect.left()
        for color, weight, name in self._segments:
            if total <= 0.0 or weight <= 0.0:
                continue
            width = int(round(rect.width() * weight / total))
            if width <= 0:
                continue
            segment = QRect(x, rect.top(), width, rect.height())
            painter.fillRect(segment, color)
            percent = weight * 100.0 / total
            if width >= 30:
                if color.lightness() < 128:
                    painter.setPen(QColor(255, 255, 255))
                else:
                    painter.setPen(QColor(0, 0, 0))
                painter.drawText(segment, Qt.AlignCenter, "%.0f%%" % percent)
            x += width
        painter.setPen(QColor(110, 110, 110))
        painter.drawRect(rect)
        painter.end()

    def mouseMoveEvent(self, event):
        total = sum(weight for _, weight, _ in self._segments)
        if total <= 0.0:
            return
        x = float(event.pos().x())
        acc = 0.0
        for color, weight, name in self._segments:
            width = self.rect().width() * weight / total
            if acc <= x <= acc + width:
                self.setToolTip("%s — %.1f%%" % (name, weight * 100.0 / total))
                return
            acc += width
        self.setToolTip("")


class ColorMixerPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self._palette = []
        self._target = QColor(128, 128, 128)
        self._last_foreground = None
        self._analyze_timer = QTimer(self)
        self._analyze_timer.setSingleShot(True)
        self._analyze_timer.setInterval(80)
        self._analyze_timer.timeout.connect(self._run_analysis)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(300)
        self._poll_timer.timeout.connect(self._poll_foreground)
        self._poll_timer.start()
        self._build_ui()
        self._load_krita_palettes()
        self._restore_state()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(True)
        outer.addWidget(self.splitter)

        palette_widget = QWidget()
        palette_layout = QVBoxLayout(palette_widget)
        palette_layout.setContentsMargins(0, 0, 0, 6)

        palette_layout.addWidget(QLabel("<b>Paleta (pinturas)</b>"))
        self.palette_list = QListWidget()
        self.palette_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.palette_list.setIconSize(QSize(18, 18))
        self.palette_list.setMinimumHeight(48)
        palette_layout.addWidget(self.palette_list, 1)

        row1 = QHBoxLayout()
        self.btn_add = QPushButton("Añadir color...")
        self.btn_add.clicked.connect(self._add_color)
        self.btn_std = QPushButton("Lista estándar...")
        self.btn_std.clicked.connect(self._add_standard)
        row1.addWidget(self.btn_add)
        row1.addWidget(self.btn_std)
        palette_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_del = QPushButton("Quitar")
        self.btn_del.clicked.connect(self._remove_selected)
        self.btn_clear = QPushButton("Limpiar")
        self.btn_clear.clicked.connect(self._clear_palette)
        row2.addWidget(self.btn_del)
        row2.addWidget(self.btn_clear)
        palette_layout.addLayout(row2)

        palette_layout.addWidget(QLabel("<b>Importar paleta de Krita:</b>"))
        self.cmb_palettes = QComboBox()
        self.cmb_palettes.currentIndexChanged.connect(self._import_palette)
        palette_layout.addWidget(self.cmb_palettes)
        self.splitter.addWidget(palette_widget)

        result_widget = QWidget()
        result_layout = QVBoxLayout(result_widget)
        result_layout.setContentsMargins(0, 6, 0, 0)

        result_layout.addWidget(QLabel("<b>Color objetivo</b>"))
        row3 = QHBoxLayout()
        self.sw_target = QFrame()
        self.sw_target.setFixedSize(32, 32)
        self.lbl_target = QLabel("#000000")
        row3.addWidget(self.sw_target)
        row3.addWidget(self.lbl_target)
        row3.addStretch(1)
        result_layout.addLayout(row3)

        result_layout.addWidget(QLabel("<b>Mezcla calculada</b>"))
        row5 = QHBoxLayout()
        self.sw_mixed = QFrame()
        self.sw_mixed.setFixedSize(36, 36)
        self.lbl_result = QLabel("Añade colores a la paleta y elige un color objetivo.")
        self.lbl_result.setWordWrap(True)
        row5.addWidget(self.sw_mixed)
        row5.addWidget(self.lbl_result, 1)
        result_layout.addLayout(row5)

        self.mix_bar = MixBar()
        result_layout.addWidget(self.mix_bar)

        self.lbl_legend = QLabel("")
        self.lbl_legend.setWordWrap(True)
        self.lbl_legend.setTextFormat(Qt.RichText)
        result_layout.addWidget(self.lbl_legend)
        self.splitter.addWidget(result_widget)

        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([220, 320])

        self._update_target_ui()

    def _add_color(self):
        color = QColorDialog.getColor(self._target, self, "Añadir color a la paleta")
        if not color.isValid():
            return
        self._palette.append((color.name().upper(), color))
        self._palette_changed()

    def _add_standard(self):
        dialog = StandardColorsDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        selected = dialog.selected_colors()
        for name, hexv in selected:
            self._palette.append((name, QColor(hexv)))
        if selected:
            self._palette_changed()

    def _remove_selected(self):
        rows = sorted(
            [self.palette_list.row(item) for item in self.palette_list.selectedItems()],
            reverse=True)
        for row in rows:
            if 0 <= row < len(self._palette):
                del self._palette[row]
        self._palette_changed()

    def _clear_palette(self):
        self._palette = []
        self._palette_changed()

    def _palette_changed(self):
        self._refresh_palette_list()
        self._save_state()
        self._schedule_analysis()

    def _refresh_palette_list(self):
        self.palette_list.clear()
        for name, color in self._palette:
            item = QListWidgetItem(_swatch_icon(color.name()), name)
            item.setToolTip(name)
            item.setData(Qt.UserRole, color)
            self.palette_list.addItem(item)

    def _load_krita_palettes(self):
        self.cmb_palettes.blockSignals(True)
        self.cmb_palettes.clear()
        self.cmb_palettes.addItem("— Paleta personalizada —", None)
        try:
            resources = Krita.instance().resources("palette")
            for name in sorted(resources.keys()):
                self.cmb_palettes.addItem(name, resources[name])
        except Exception:
            pass
        self.cmb_palettes.blockSignals(False)

    def _resource_colors(self, resource):
        try:
            entries = resource.colors()
        except Exception:
            return []
        view = None
        try:
            window = Krita.instance().activeWindow()
            if window is not None:
                view = window.activeView()
        except Exception:
            view = None
        colors = []
        for entry in entries:
            color = _to_qcolor(entry, view)
            if color is not None and color.alpha() > 0:
                colors.append(color)
        return colors

    def _import_palette(self, index):
        if index <= 0:
            return
        resource = self.cmb_palettes.itemData(index)
        colors = self._resource_colors(resource)
        if not colors:
            QMessageBox.information(self, "Paleta", "No se pudieron leer los colores de esta paleta.")
            return
        self._palette = [(color.name().upper(), color) for color in colors]
        self._palette_changed()
        self.cmb_palettes.blockSignals(True)
        self.cmb_palettes.setCurrentIndex(0)
        self.cmb_palettes.blockSignals(False)

    def _get_foreground_color(self):
        details = []
        try:
            app = Krita.instance()
            try:
                details.append("Krita %s" % app.version())
            except Exception:
                pass
            window = app.activeWindow()
            if window is None:
                return None, ["activeWindow() → None"]
            view = window.activeView()
            if view is None:
                return None, ["activeView() → None (¿hay un documento abierto?)"]
            raw = view.foregroundColor()
            if isinstance(raw, QColor):
                return raw, details
            color = _to_qcolor(raw, view)
            if color is None:
                details.append("foregroundColor() → tipo %s" % type(raw).__name__)
                for probe in ("colorModel", "colorDepth", "components", "componentsOrdered"):
                    try:
                        details.append("%s() → %r" % (probe, getattr(raw, probe)()))
                    except Exception as exc:
                        details.append("%s() error: %r" % (probe, exc))
            return color, details
        except Exception:
            return None, ["ERROR:\n" + traceback.format_exc()]

    def _read_foreground(self):
        color, _ = self._get_foreground_color()
        return color

    def _poll_foreground(self):
        color = self._read_foreground()
        if color is None:
            return
        if self._last_foreground is not None and color == self._last_foreground:
            return
        self._last_foreground = QColor(color)
        self.set_target(color)

    def set_target(self, color, schedule=True):
        self._target = QColor(color)
        self._update_target_ui()
        self._save_state()
        if schedule:
            self._schedule_analysis()

    def _update_target_ui(self):
        self.sw_target.setStyleSheet(
            "background-color: %s; border: 1px solid #666;" % self._target.name())
        self.lbl_target.setText(self._target.name().upper())

    def _schedule_analysis(self):
        self._analyze_timer.start()

    def _run_analysis(self):
        if not self._palette:
            self.lbl_result.setText("Añade colores a la paleta y elige un color objetivo.")
            self.sw_mixed.setStyleSheet("")
            self.mix_bar.set_mix([])
            self.lbl_legend.setText("")
            return
        target = (self._target.red(), self._target.green(), self._target.blue())
        palette = [(color.red(), color.green(), color.blue()) for _, color in self._palette]
        try:
            result = mixing.analyze(target, palette)
        except Exception:
            return
        self._render_result(result)

    def _render_result(self, result):
        mixed = result["mixed"]
        hexm = "#%02X%02X%02X" % (
            int(round(mixed[0])), int(round(mixed[1])), int(round(mixed[2])))
        status = "EXACTA" if result["exact"] else "APROXIMADA"
        target_hex = self._target.name().upper()
        if result["exact"]:
            text = "<b>%s</b> · ΔE = %.2f<br/>Logrado: %s" % (status, result["de"], hexm)
        else:
            text = ("<b>%s</b> · ΔE = %.2f<br/>"
                    "Objetivo: %s → <b>Logrado: %s</b>"
                    % (status, result["de"], target_hex, hexm))
            if hexm == target_hex:
                text += "<br/>Logrado (decimal): %.1f, %.1f, %.1f" % (
                    mixed[0], mixed[1], mixed[2])
        self.lbl_result.setText(text)
        self.sw_mixed.setStyleSheet(
            "background-color: %s; border: 1px solid #666;" % hexm)
        segments = []
        legend_lines = []
        for (name, color), weight in zip(self._palette, result["weights"]):
            percent = weight * 100.0
            if percent >= 0.05:
                segments.append((QColor(color), weight, name))
                legend_lines.append(
                    '<span style="background-color:%s;color:%s;">██</span> %s — %.1f%%'
                    % (color.name(), color.name(), name, percent))
        self.mix_bar.set_mix(segments)
        self.lbl_legend.setText("<br/>".join(legend_lines))

    def _save_state(self):
        settings = QSettings("color_mixer_plugin", "ColorMixer")
        settings.setValue(
            "palette", ["%s|%s" % (name, color.name()) for name, color in self._palette])
        settings.setValue("target", self._target.name())

    def _restore_state(self):
        settings = QSettings("color_mixer_plugin", "ColorMixer")
        saved = settings.value("palette", [])
        if saved:
            self._palette = []
            for entry in saved:
                try:
                    name, hexv = str(entry).split("|", 1)
                except ValueError:
                    continue
                color = QColor(hexv)
                if color.isValid():
                    self._palette.append((name, color))
            self._refresh_palette_list()
        target = settings.value("target", None)
        if target:
            color = QColor(str(target))
            if color.isValid():
                self.set_target(color, schedule=False)
        self._schedule_analysis()


class ColorMixerDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mezclador de colores")
        self.panel = ColorMixerPanel(self)
        self.setWidget(self.panel)

    def canvasChanged(self, canvas):
        pass


def register_color_mixer_docker():
    instance = Krita.instance()
    factory = DockWidgetFactory(
        "color_mixer_docker",
        DockWidgetFactoryBase.DockRight,
        ColorMixerDocker,
    )
    instance.addDockWidgetFactory(factory)
