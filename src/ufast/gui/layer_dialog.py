from PyQt6.QtWidgets import (QDockWidget, QWidget, QVBoxLayout,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QPushButton)
from PyQt6.QtCore import Qt, pyqtSignal
from typing import List
from ufast.drawing.geometry import CLayer


class LayerPanel(QDockWidget):
    """Panel for toggling layers on/off and marking rail layers (DockWidget)"""
    layer_changed = pyqtSignal()  # emitted when on/off or the rail flag changes
    convert_to_rail = pyqtSignal()  # emitted when the Convert to Rail button is clicked

    def __init__(self, parent=None):
        super().__init__("Layers", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.layers: List[CLayer] = []
        self._updating = False  # flag to block signals during programmatic changes

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 2, 2, 2)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Layer Name", "On", "Rail"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        self.btn_convert = QPushButton("Convert to Rail")
        self.btn_convert.clicked.connect(self.convert_to_rail.emit)
        layout.addWidget(self.btn_convert)

        self.setWidget(container)
        self.setMinimumWidth(200)

    def set_layers(self, layers: List[CLayer]):
        """Refresh the table from the given list of layers."""
        self._updating = True
        self.layers = layers
        self.table.setRowCount(len(layers))

        for i, layer in enumerate(layers):
            # Name (read-only)
            name_item = QTableWidgetItem(layer.layer_name)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(i, 0, name_item)

            # Visible (On/Off)
            vis_item = QTableWidgetItem()
            vis_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            vis_item.setCheckState(Qt.CheckState.Checked if layer.is_turned_on else Qt.CheckState.Unchecked)
            self.table.setItem(i, 1, vis_item)

            # Rail
            rail_item = QTableWidgetItem()
            rail_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            is_rail = layer.is_rail_layer or "RAIL" in layer.layer_name.upper()
            layer.is_rail_layer = is_rail
            rail_item.setCheckState(Qt.CheckState.Checked if is_rail else Qt.CheckState.Unchecked)
            self.table.setItem(i, 2, rail_item)

        self._updating = False

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._updating:
            return
        row = item.row()
        col = item.column()
        if row >= len(self.layers):
            return

        layer = self.layers[row]
        checked = item.checkState() == Qt.CheckState.Checked

        if col == 1:
            layer.is_turned_on = checked
        elif col == 2:
            layer.is_rail_layer = checked

        self.layer_changed.emit()

    def clear_layers(self):
        self._updating = True
        self.layers = []
        self.table.setRowCount(0)
        self._updating = False
