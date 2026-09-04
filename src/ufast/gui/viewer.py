import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
    QGraphicsEllipseItem, QFileDialog, QGraphicsPathItem,
    QProgressDialog, QDockWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QSlider, QGroupBox, QGridLayout,
    QGraphicsRectItem, QGraphicsSimpleTextItem, QCheckBox,
    QStackedWidget, QPushButton, QMessageBox, QComboBox,
    QSpinBox, QFormLayout, QScrollArea
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QPen, QColor, QBrush, QPainter, QTransform,
    QMouseEvent, QWheelEvent, QAction, QPainterPath, QFont
)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

from ufast.core.data_set import SimulatorDataSet
from ufast.drawing.geometry import CLine, CQuadCurve, CText
from ufast.core.components import OHT

from ufast.common.config_loader import ConfigLoader
from ufast.gui.layer_dialog import LayerPanel

try:
    from ufast.integration.production_view import ProductionDashboard
    _PROD_VIEW_AVAILABLE = True
except Exception as _e:  # noqa: BLE001
    _PROD_VIEW_AVAILABLE = False
    print(f"[viewer] ⚠️ ProductionDashboard import failed: {_e}")


# EQ 상태 → (채움색, 테두리색) — 라이브 갱신·스냅샷 재생·범례가 공유하는 단일 소스.
# 한 곳만 고치면 세 곳이 함께 바뀐다. (viz/replay_recorder.py 의 Rerun 색과 동일 체계)
EQ_STATUS_STYLES = {
    "BUSY":    (QColor(50, 205, 50, 220),   QColor(0, 180, 0)),      # OHT 접근/가공 중
    "INBOUND": (QColor(135, 206, 250, 220), QColor(70, 150, 210)),   # 공정 대기/입고
    "WAITING": (QColor(255, 165, 0, 220),   QColor(200, 100, 0)),    # LOT 대기 (OHT 없음)
    "IDLE":    (QColor(160, 160, 160, 80),  QColor(130, 130, 130)),
}


def eq_status_key(status: str) -> str:
    if status in ("OHT_COMING", "PROCESSING"):
        return "BUSY"
    if status in ("PROCESS_WAITING", "LOT_INBOUND"):
        return "INBOUND"
    if status == "WAITING":
        return "WAITING"
    return "IDLE"


class OHTItem(QGraphicsEllipseItem):
    def __init__(self, oht: OHT):
        # OHT 크기: 1000mm x 1000mm (반경 500mm)
        super().__init__(-500, -500, 1000, 1000)
        self.oht = oht
        self.setPen(QPen(Qt.GlobalColor.black, 10))
        self.setZValue(10)  # 레일 위에 표시
        self.update_color()

    def update_color(self):
        config = ConfigLoader.get_instance()
        if self.oht.status == "IDLE":
            self.setBrush(QBrush(QColor(config.get("appearance", "oht_color_idle", "#0000FF"))))
        elif self.oht.status == "LOADED":
            self.setBrush(QBrush(QColor(config.get("appearance", "oht_color_loaded", "#FF0000"))))
        elif self.oht.status == "ASSIGNED":
            self.setBrush(QBrush(QColor(config.get("appearance", "oht_color_assigned", "#00FF00"))))
        elif self.oht.status == "REPOSITIONING":
            self.setBrush(QBrush(QColor(config.get("appearance", "oht_color_idle", "#0000FF"))))
        else:
            self.setBrush(QBrush(QColor(config.get("appearance", "oht_color_moving", "#FFA500"))))

    def update_position(self, current_time):
        self.update_color()

        ds = SimulatorDataSet.get_instance()
        idx = ds.section_id_to_index.get(self.oht.current_section_id)

        if idx is None:
            self.setVisible(False)
            return

        self.setVisible(True)

        section = ds.sections[idx]
        if not section.figures:
            return
        fig = section.figures[0]

        speed = 1000.0  # mm/s
        elapsed = current_time - self.oht.time_enter_current_section
        dist = elapsed * speed

        ratio = dist / section.length if section.length > 0 else 0
        ratio = max(0.0, min(1.0, ratio))

        if isinstance(fig, CQuadCurve):
            # 2차 베지어 곡선 보간: B(t) = (1-t)²P0 + 2(1-t)tP1 + t²P2
            t = ratio
            mt = 1.0 - t
            x = mt * mt * fig.start_x + 2 * mt * t * fig.ctrl_x + t * t * fig.end_x
            y = mt * mt * fig.start_y + 2 * mt * t * fig.ctrl_y + t * t * fig.end_y
        else:
            x = fig.start_x + (fig.end_x - fig.start_x) * ratio
            y = fig.start_y + (fig.end_y - fig.start_y) * ratio

        self.setPos(x, y)

    # 통합: 스냅샷 값으로 위치/색상을 계산 (라이브 OHT 객체를 변경하지 않음)
    def apply_snapshot(self, section_id, enter_time, status, current_time):
        # 색상은 status 로 직접 결정
        config = ConfigLoader.get_instance()
        color_map = {
            "IDLE": config.get("appearance", "oht_color_idle", "#0000FF"),
            "LOADED": config.get("appearance", "oht_color_loaded", "#FF0000"),
            "ASSIGNED": config.get("appearance", "oht_color_assigned", "#00FF00"),
            "REPOSITIONING": config.get("appearance", "oht_color_idle", "#0000FF"),
        }
        self.setBrush(QBrush(QColor(
            color_map.get(status, config.get("appearance", "oht_color_moving", "#FFA500"))
        )))

        ds = SimulatorDataSet.get_instance()
        idx = ds.section_id_to_index.get(section_id)
        if idx is None:
            self.setVisible(False)
            return
        self.setVisible(True)
        section = ds.sections[idx]
        if not section.figures:
            return
        fig = section.figures[0]

        speed = 1000.0  # mm/s
        elapsed = current_time - enter_time
        dist = elapsed * speed
        ratio = dist / section.length if section.length > 0 else 0
        ratio = max(0.0, min(1.0, ratio))

        if isinstance(fig, CQuadCurve):
            t = ratio
            mt = 1.0 - t
            x = mt * mt * fig.start_x + 2 * mt * t * fig.ctrl_x + t * t * fig.end_x
            y = mt * mt * fig.start_y + 2 * mt * t * fig.ctrl_y + t * t * fig.end_y
        else:
            x = fig.start_x + (fig.end_x - fig.start_x) * ratio
            y = fig.start_y + (fig.end_y - fig.start_y) * ratio
        self.setPos(x, y)


class CADGraphicsView(QGraphicsView):
    mouse_scene_pos_changed = pyqtSignal(float, float)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setViewport(QOpenGLWidget())
        # 대형 .rail(SMAT2022 등)은 정적 레일 아이템이 1만 개 이상 생성된다.
        # FullViewportUpdate는 OHT/EQ 한 개가 바뀌어도 전체 장면을 다시 그려 화면 끊김을 유발하므로
        # 변경된 bounding rect 중심으로만 갱신한다. 시각 기능은 유지하고 렌더링 비용만 낮춘다.
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState, True)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)
        self.setMouseTracking(True)

        config = ConfigLoader.get_instance()
        bg_color = config.get("appearance", "background_color", "#000000")
        self.setBackgroundBrush(QBrush(QColor(bg_color)))

        # Y축 반전 (CAD 좌표계 대응)
        self.scale(1, -1)

        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

        self._is_panning = False
        self._last_mouse_pos = QPointF()

    def wheelEvent(self, event: QWheelEvent):
        zoom_factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(zoom_factor, zoom_factor)
        else:
            self.scale(1 / zoom_factor, 1 / zoom_factor)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True
            self._last_mouse_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        scene_pos = self.mapToScene(event.position().toPoint())
        self.mouse_scene_pos_changed.emit(scene_pos.x(), -scene_pos.y())

        if self._is_panning:
            delta = event.position() - self._last_mouse_pos
            self._last_mouse_pos = event.position()

            hs = self.horizontalScrollBar()
            vs = self.verticalScrollBar()
            hs.setValue(hs.value() - int(delta.x()))
            vs.setValue(vs.value() - int(delta.y()))
            event.accept()
        else:
            super().mouseMoveEvent(event)


class SimulationViewer(QMainWindow):
    simulation_start_signal = pyqtSignal()
    simulation_stop_signal = pyqtSignal()
    save_log_signal = pyqtSignal()
    layout_file_loaded_signal = pyqtSignal(str)  # DXF
    rail_file_loaded_signal = pyqtSignal(str)
    rail_file_save_signal = pyqtSignal(str)
    fromto_file_loaded_signal = pyqtSignal(str)
    speed_changed_signal = pyqtSignal(float)
    clear_signal = pyqtSignal()
    # 통합: 모드 선택 / 타임라인 재생 제어
    mode_changed_signal = pyqtSignal(str)            # "logistics" | "production"
    timeline_scrubbed_signal = pyqtSignal(float)     # 0.0~1.0 진행바 비율
    playback_toggled_signal = pyqtSignal(bool)       # True=재생, False=일시정지
    go_live_signal = pyqtSignal()                    # 과거 탐색 → 실시간 복귀
    # 패널 상주 옵션: 전략 설정 다이얼로그 열기 / 외부 생산 데이터셋 추가
    configure_strategies_signal = pyqtSignal()
    production_add_dataset_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AMHS Simulation Viewer (PyQt6)")
        self.resize(1200, 800)
        self.create_menu_bar()
        self.statusBar()
        # 통합: 현재 실행 정보(데이터셋 / From-To 차트)를 상태바 우측에 상시 표시.
        # showMessage() 로 갱신되는 임시 메시지와 달리 이 라벨은 항상 보인다.
        self.run_info_label = QLabel("")
        self.run_info_label.setStyleSheet(
            "color:#cfe3ff; padding:0 10px; font-weight:bold;"
        )
        self.statusBar().addPermanentWidget(self.run_info_label)

        self.scene = QGraphicsScene()
        self.view = CADGraphicsView(self.scene)
        self.view.mouse_scene_pos_changed.connect(self.update_status_coords)

        # 통합: 중앙을 스택으로 구성 (0=물류 그래픽뷰, 1=생산 대시보드)
        self.central_stack = QStackedWidget()
        self.central_stack.addWidget(self.view)              # index 0
        if _PROD_VIEW_AVAILABLE:
            self.production_dashboard = ProductionDashboard()
            self.central_stack.addWidget(self.production_dashboard)  # index 1
        else:
            self.production_dashboard = None
        self.setCentralWidget(self.central_stack)
        self.current_mode = "logistics"

        # Layer Panel
        self.layer_panel = LayerPanel(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.layer_panel)
        self.toggle_layer_action.toggled.connect(self.layer_panel.setVisible)
        self.layer_panel.visibilityChanged.connect(self.toggle_layer_action.setChecked)
        # 기본 숨김 — DXF 로딩(레일 변환 작업) 시 앱 쪽에서 다시 표시한다.
        self.layer_panel.hide()

        # Simulation Panel
        self._create_control_panel()
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.control_dock)
        self.toggle_control_action.toggled.connect(self.control_dock.setVisible)
        self.control_dock.visibilityChanged.connect(self.toggle_control_action.setChecked)

        # Results Panel — 통계·범례. 시뮬레이션 실행 시에만 표시
        self._create_results_panel()
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.results_dock)
        self.toggle_results_action.toggled.connect(self.results_dock.setVisible)
        self.results_dock.visibilityChanged.connect(self.toggle_results_action.setChecked)
        self.results_dock.hide()

        # 통합: 타임라인(과거 재생) 패널 — 하단 도크
        self._create_timeline_panel()
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.timeline_dock)
        self.toggle_timeline_action.toggled.connect(self.timeline_dock.setVisible)
        self.timeline_dock.visibilityChanged.connect(self.toggle_timeline_action.setChecked)

        self.oht_items = {}
        self._playback_mode = False   # 통합: 타임라인 재생 중 라이브 애니메이션 정지 플래그
        self._reviewing = False       # 통합: 과거 탐색(리뷰) 중 여부

        # ✅ EQ 아이템 관리 (사각형/텍스트 분리)
        self.eq_items = {}
        self.eq_text_items = {}
        # 대형 layout에서 매 프레임 1,000개 이상 EQ의 brush/pen/text를 재설정하면
        # GUI thread가 막힌다. 마지막 렌더 상태를 보관해 바뀐 EQ만 갱신한다.
        self._eq_render_state = {}
        self._eq_update_counter = 0

        # ✅ EQ 표시 토글 상태
        self.show_eq = True
        self.show_eq_text = True

        self.data_set = SimulatorDataSet.get_instance()

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(16)

    def _create_control_panel(self):
        self.control_dock = QDockWidget("Simulation", self)
        self.control_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        container = QWidget()
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(8)

        # ── Run / Stop ──
        run_row = QHBoxLayout()
        self.run_button = QPushButton("▶ Run")
        self.run_button.clicked.connect(self.emit_start_simulation)
        self.stop_button = QPushButton("■ Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.emit_stop_simulation)
        run_row.addWidget(self.run_button)
        run_row.addWidget(self.stop_button)
        main_layout.addLayout(run_row)

        # ── 통합: 시뮬레이터 모드 선택 ──
        mode_group = QGroupBox("Simulator Mode")
        mode_layout = QVBoxLayout(mode_group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Logistics (AMHS)", "logistics")
        self.mode_combo.addItem("Production (PySCFabSim)", "production")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_combo_changed)
        mode_layout.addWidget(self.mode_combo)
        main_layout.addWidget(mode_group)

        # ── Input Files (물류) — 로딩된 레이아웃/From-To 파일 표시 ──
        self.input_files_group = QGroupBox("Input Files")
        fform = QFormLayout(self.input_files_group)
        fform.setContentsMargins(6, 6, 6, 6)
        self.layout_file_label = QLabel("(not loaded)")
        self.layout_file_label.setStyleSheet("color:#999;")
        self.layout_file_label.setWordWrap(True)
        fform.addRow("Layout:", self.layout_file_label)
        self.fromto_file_label = QLabel("(not loaded)")
        self.fromto_file_label.setStyleSheet("color:#999;")
        self.fromto_file_label.setWordWrap(True)
        fform.addRow("From-To:", self.fromto_file_label)
        self.production_data_label = QLabel("(not added)")
        self.production_data_label.setStyleSheet("color:#999;")
        self.production_data_label.setWordWrap(True)
        fform.addRow("Production:", self.production_data_label)
        main_layout.addWidget(self.input_files_group)

        # ── Run Options (물류) — Run 시점 다이얼로그 대신 패널에 상주 ──
        self.logistics_options_group = QGroupBox("Run Options")
        lform = QFormLayout(self.logistics_options_group)
        lform.setContentsMargins(6, 6, 6, 6)

        # 실행 방식: live=개체 동작 검증(실시간 애니메이션) /
        #           replay=집계 분석(전속력 기록 → Rerun 뷰어에서 재생)
        self.run_mode_combo = QComboBox()
        self.run_mode_combo.addItem("Live (animate while running)", "live")
        self.run_mode_combo.addItem("Replay (record → Rerun viewer)", "replay")
        lform.addRow("Run mode", self.run_mode_combo)

        self.oht_spin = QSpinBox()
        self.oht_spin.setRange(1, 1000)
        self.oht_spin.setValue(10)
        lform.addRow("Number of OHTs", self.oht_spin)

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(60, 31_536_000)
        self.duration_spin.setValue(3600)
        lform.addRow("Run time (s)", self.duration_spin)

        self.kpi_combo = QComboBox()
        self.kpi_combo.addItem("Logistics KPI focus", "logistics")
        self.kpi_combo.addItem("Production KPI focus", "production")
        self.kpi_combo.addItem("Both production + logistics KPI", "both")
        self.kpi_combo.setCurrentIndex(2)
        lform.addRow("Result-saving basis", self.kpi_combo)

        # 주의: PySCFabSim 생산 모드가 아니라, 물류 시뮬레이션 중 EQ 가공
        # 체류시간(기본 처리시간)을 반영할지 여부다. From-To 만으로 동작한다.
        self.sim_mode_combo = QComboBox()
        self.sim_mode_combo.addItem("Transport only", "from_to_only")
        self.sim_mode_combo.addItem("Transport + EQ processing", "production_logistics")
        self.sim_mode_combo.setCurrentIndex(1)
        lform.addRow("EQ processing", self.sim_mode_combo)

        self.processing_spin = QSpinBox()
        self.processing_spin.setRange(1, 86400)
        self.processing_spin.setValue(60)
        self.processing_label = QLabel("Processing time (s)")
        lform.addRow(self.processing_label, self.processing_spin)

        self.strategy_button = QPushButton("Custom strategies… (none)")
        self.strategy_button.clicked.connect(self.configure_strategies_signal.emit)
        lform.addRow(self.strategy_button)

        self.kpi_combo.currentIndexChanged.connect(self._refresh_logistics_option_state)
        self.sim_mode_combo.currentIndexChanged.connect(self._refresh_logistics_option_state)
        main_layout.addWidget(self.logistics_options_group)

        # ── Run Options (생산) ──
        self.production_options_group = QGroupBox("Run Options")
        pform = QFormLayout(self.production_options_group)
        pform.setContentsMargins(6, 6, 6, 6)

        # 생산 데이터셋은 자동 발견하지 않는다 — 사용자가 File → Production
        # Data → Add Dataset Folder… 로 명시적으로 추가해야 목록에 뜬다.
        self.dataset_combo = QComboBox()
        pform.addRow("Dataset", self.dataset_combo)

        self.add_dataset_button = QPushButton("Add dataset folder…")
        self.add_dataset_button.clicked.connect(self.production_add_dataset_signal.emit)
        pform.addRow(self.add_dataset_button)

        self.days_spin = QSpinBox()
        self.days_spin.setRange(1, 1000)
        self.days_spin.setValue(2)
        pform.addRow("Simulation days", self.days_spin)

        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 999_999)
        self.seed_spin.setValue(0)
        pform.addRow("Random seed", self.seed_spin)

        self.dispatcher_combo = QComboBox()
        for d in ["fifo", "random", "cr", "edd", "setup_avoidance"]:
            self.dispatcher_combo.addItem(d, d)
        pform.addRow("Dispatcher", self.dispatcher_combo)

        self.alg_combo = QComboBox()
        self.alg_combo.addItem("Lot-for-Machine (l4m)", "l4m")
        self.alg_combo.addItem("Machine-for-Lot (m4l)", "m4l")
        pform.addRow("Algorithm", self.alg_combo)

        self.fast_check = QCheckBox("Results only, fast (no replay)")
        self.fast_check.setChecked(True)
        pform.addRow(self.fast_check)

        self.production_options_group.setVisible(False)
        main_layout.addWidget(self.production_options_group)

        # From-To 만 입력된 상태(설비 정보 없음)에서는 생산 모드 선택 불가
        self._update_production_mode_item()
        self._update_production_data_label()
        self.dataset_combo.currentIndexChanged.connect(self._update_production_data_label)

        # ── Speed Control ──
        speed_group = QGroupBox("Speed Control")
        speed_layout = QVBoxLayout(speed_group)

        slider_row = QHBoxLayout()
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setMinimum(1)
        self.speed_slider.setMaximum(200)  # 0.1x ~ 20.0x
        self.speed_slider.setValue(10)     # 기본 1.0x
        self.speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.speed_slider.setTickInterval(10)
        self.speed_label = QLabel("1.0x")
        self.speed_label.setMinimumWidth(45)

        slider_row.addWidget(QLabel("Speed:"))
        slider_row.addWidget(self.speed_slider, 1)
        slider_row.addWidget(self.speed_label)
        speed_layout.addLayout(slider_row)

        self.speed_slider.valueChanged.connect(self._on_speed_changed)
        main_layout.addWidget(speed_group)

        # ✅ ── EQ Display (요청사항) ──
        eq_group = QGroupBox("EQ Display")
        eq_layout = QVBoxLayout(eq_group)

        self.cb_show_eq = QCheckBox("Show EQ")
        self.cb_show_eq.setChecked(True)
        self.cb_show_eq.stateChanged.connect(self._on_toggle_eq)

        self.cb_show_eq_text = QCheckBox("Show EQ Names")
        self.cb_show_eq_text.setChecked(True)
        self.cb_show_eq_text.stateChanged.connect(self._on_toggle_eq_text)

        eq_layout.addWidget(self.cb_show_eq)
        eq_layout.addWidget(self.cb_show_eq_text)
        main_layout.addWidget(eq_group)

        main_layout.addStretch()

        # 옵션 그룹이 늘어나 세로로 길어졌으므로 스크롤 가능하게 감싼다.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(container)
        self.control_dock.setWidget(scroll)
        self._refresh_logistics_option_state()

    def _create_results_panel(self):
        """통계·범례 도크 — 시뮬레이션 실행 시에만 표시되는 결과 패널."""
        self.results_dock = QDockWidget("Results", self)
        self.results_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        container = QWidget()
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(8)

        # ── Statistics ──
        stat_group = QGroupBox("Statistics")
        stat_layout = QGridLayout(stat_group)
        stat_layout.setContentsMargins(6, 6, 6, 6)

        labels = [
            ("Time:", "stat_time"),
            ("Lots (Done/Total):", "stat_lots"),
            ("OHTs:", "stat_ohts"),
            ("IDLE:", "stat_idle"),
            ("REPOSITIONING:", "stat_repositioning"),
            ("IDLE+REPO:", "stat_idle_total"),
            ("ASSIGNED:", "stat_assigned"),
            ("LOADED:", "stat_loaded"),
            ("OTHER:", "stat_other"),
            ("Throughput:", "stat_throughput"),
            ("─ Logistics KPI ─", "stat_sep_log"),
            ("Transport Time (TT):", "stat_tt"),
            ("Delivery Time (DT):", "stat_dt"),
            ("Call Wait:", "stat_call_wait"),
            ("─ Production KPI ─", "stat_sep_prod"),
            ("Run-down Time:", "stat_rundown"),
            ("WIP:", "stat_wip"),
        ]
        self._stat_labels = {}
        for row, (text, key) in enumerate(labels):
            lbl = QLabel(text)
            if key.startswith("stat_sep_"):
                # 섹션 구분 헤더 — 값 칸 없이 두 칸을 합쳐 표시
                lbl.setStyleSheet("color:#7fd0ff; font-weight:bold;")
                stat_layout.addWidget(lbl, row, 0, 1, 2)
                continue
            val = QLabel("-")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            stat_layout.addWidget(lbl, row, 0)
            stat_layout.addWidget(val, row, 1)
            self._stat_labels[key] = val

        main_layout.addWidget(stat_group)

        # ── OHT Legend ──
        legend_group = QGroupBox("OHT Colors")
        legend_layout = QGridLayout(legend_group)
        legend_layout.setContentsMargins(6, 6, 6, 6)

        config = ConfigLoader.get_instance()
        legend_items = [
            # REPOSITIONING 은 IDLE 과 같은 색을 쓴다 (OHTItem.update_color 참조)
            ("IDLE / REPOSITIONING", config.get("appearance", "oht_color_idle", "#0000FF")),
            ("ASSIGNED", config.get("appearance", "oht_color_assigned", "#00FF00")),
            ("LOADED", config.get("appearance", "oht_color_loaded", "#FF0000")),
            ("MOVING (other)", config.get("appearance", "oht_color_moving", "#FFA500")),
        ]
        for i, (name, color) in enumerate(legend_items):
            swatch = QLabel()
            swatch.setFixedSize(16, 16)
            swatch.setStyleSheet(f"background-color: {color}; border: 1px solid #888;")
            legend_layout.addWidget(swatch, i, 0)
            legend_layout.addWidget(QLabel(name), i, 1)

        main_layout.addWidget(legend_group)

        # ── EQ Status Legend ──
        eq_legend_group = QGroupBox("EQ Status")
        eq_legend_layout = QGridLayout(eq_legend_group)
        eq_legend_layout.setContentsMargins(6, 6, 6, 6)

        # 색은 EQ_STATUS_STYLES 단일 소스에서 가져온다
        eq_legend_items = [
            ("IDLE (No Lot)",           EQ_STATUS_STYLES["IDLE"][0].name()),
            ("WAITING (Lot, No OHT)",   EQ_STATUS_STYLES["WAITING"][0].name()),
            ("Process Waiting",         EQ_STATUS_STYLES["INBOUND"][0].name()),
            ("OHT Coming / Processing", EQ_STATUS_STYLES["BUSY"][0].name()),
        ]
        for i, (name, color) in enumerate(eq_legend_items):
            swatch = QLabel()
            swatch.setFixedSize(16, 16)
            swatch.setStyleSheet(f"background-color: {color}; border: 1px solid #888;")
            eq_legend_layout.addWidget(swatch, i, 0)
            eq_legend_layout.addWidget(QLabel(name), i, 1)

        main_layout.addWidget(eq_legend_group)
        main_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(container)
        self.results_dock.setWidget(scroll)

    def _on_speed_changed(self, value):
        speed = value / 10.0
        self.speed_label.setText(f"{speed:.1f}x")
        self.speed_changed_signal.emit(speed)

    # ── 패널 상주 Run Options ─────────────────────────────────
    def _update_production_mode_item(self):
        """생산 모드 선택 가능 여부 갱신.

        From-To 는 설비 정보가 없으므로 생산(PySCFabSim) 모드의 입력이 될 수
        없다 — 유효한 생산 데이터셋(내장 datasets/ 또는 외부 폴더)이 하나도
        없으면 Production 항목을 비활성화한다.
        """
        item = self.mode_combo.model().item(1)
        if item is None:
            return
        if not _PROD_VIEW_AVAILABLE:
            item.setEnabled(False)
            self.mode_combo.setItemText(1, "Production (unavailable)")
        elif self.dataset_combo.count() == 0:
            item.setEnabled(False)
            self.mode_combo.setItemText(1, "Production (no dataset loaded)")
        else:
            item.setEnabled(True)
            self.mode_combo.setItemText(1, "Production (PySCFabSim)")

    def _update_production_data_label(self, _idx=0):
        """Input Files 의 Production 데이터셋 라벨을 콤보 선택과 동기화."""
        if self.dataset_combo.count() == 0 or self.dataset_combo.currentData() is None:
            self.production_data_label.setText("(not added)")
            self.production_data_label.setStyleSheet("color:#999;")
        else:
            self.production_data_label.setText(self.dataset_combo.currentText())
            self.production_data_label.setStyleSheet("font-weight:bold;")

    def _refresh_logistics_option_state(self, _idx=0):
        """KPI 기준/시뮬레이터 모드 조합에 따른 옵션 활성화 규칙."""
        kpi = self.kpi_combo.currentData()
        if kpi == "logistics":
            i = self.sim_mode_combo.findData("from_to_only")
            if i >= 0 and self.sim_mode_combo.currentIndex() != i:
                self.sim_mode_combo.blockSignals(True)
                self.sim_mode_combo.setCurrentIndex(i)
                self.sim_mode_combo.blockSignals(False)
            self.sim_mode_combo.setEnabled(False)
        else:
            self.sim_mode_combo.setEnabled(True)
        show = (
            self.sim_mode_combo.currentData() == "production_logistics"
            and kpi != "logistics"
        )
        self.processing_label.setVisible(show)
        self.processing_spin.setVisible(show)

    def get_logistics_options(self) -> dict:
        return {
            "run_mode":                str(self.run_mode_combo.currentData()),
            "num_oht":                 int(self.oht_spin.value()),
            "duration":                float(self.duration_spin.value()),
            "kpi_save_mode":           str(self.kpi_combo.currentData()),
            "simulator_mode":          str(self.sim_mode_combo.currentData()),
            "default_processing_time": float(self.processing_spin.value()),
        }

    def get_production_options(self):
        """생산 실행 옵션. 데이터셋이 없으면 None."""
        if self.dataset_combo.count() == 0 or self.dataset_combo.currentData() is None:
            return None
        return {
            "dataset":           str(self.dataset_combo.currentData()),
            "days":              int(self.days_spin.value()),
            "seed":              int(self.seed_spin.value()),
            "dispatcher":        str(self.dispatcher_combo.currentData()),
            "alg":               str(self.alg_combo.currentData()),
            "fast_mode":         bool(self.fast_check.isChecked()),
        }

    def add_production_dataset(self, path: str):
        """외부 데이터셋 폴더를 콤보에 추가하고 선택한다 (검증은 앱 쪽 책임)."""
        import os
        idx = self.dataset_combo.findData(path)
        if idx < 0:
            self.dataset_combo.addItem(f"[External] {os.path.basename(path)}", path)
            idx = self.dataset_combo.count() - 1
        self.dataset_combo.setCurrentIndex(idx)
        # 데이터셋이 생겼으므로 생산 모드 선택 가능 여부 재평가
        self._update_production_mode_item()

    def set_layout_file_label(self, name: str):
        self.layout_file_label.setText(name)
        self.layout_file_label.setStyleSheet("font-weight:bold;")

    def set_fromto_file_label(self, name: str, count: int):
        # 파일명과 레코드 수를 명시적 두 줄로 — 도크 폭에서 잘리지 않게 한다
        self.fromto_file_label.setText(f"{name}\n{count:,} records")
        self.fromto_file_label.setStyleSheet("font-weight:bold;")

    def reset_input_file_labels(self):
        for lbl in (self.layout_file_label, self.fromto_file_label):
            lbl.setText("(not loaded)")
            lbl.setStyleSheet("color:#999;")

    def set_strategy_summary(self, active_count: int):
        suffix = f"({active_count} active)" if active_count else "(none)"
        self.strategy_button.setText(f"Custom strategies… {suffix}")

    def set_options_locked(self, locked: bool):
        """실행 중에는 Run 옵션·모드 변경을 잠근다."""
        self.run_button.setEnabled(not locked)
        self.stop_button.setEnabled(locked)
        self.mode_combo.setEnabled(not locked)
        self.logistics_options_group.setEnabled(not locked)
        self.production_options_group.setEnabled(not locked)

    # ── 통합: 모드 전환 ───────────────────────────────────────
    def _on_mode_combo_changed(self, _idx):
        mode = self.mode_combo.currentData()
        self.set_mode(mode)
        self.mode_changed_signal.emit(mode)

    def set_run_info(self, text: str):
        """상태바 우측에 현재 실행 정보(데이터셋/From-To 차트)를 상시 표시."""
        self.run_info_label.setText(text)

    def set_mode(self, mode: str):
        """중앙 화면과 Run Options 그룹을 모드에 맞게 전환한다."""
        self.current_mode = mode
        if mode == "production" and self.production_dashboard is not None:
            self.central_stack.setCurrentIndex(1)
        else:
            self.central_stack.setCurrentIndex(0)
        show_prod = (mode == "production")
        # Input Files 는 세 입력(Layout/From-To/Production)을 항상 보여준다
        self.logistics_options_group.setVisible(not show_prod)
        self.production_options_group.setVisible(show_prod)
        # combo 동기화 (외부에서 호출된 경우)
        idx = self.mode_combo.findData(mode)
        if idx >= 0 and self.mode_combo.currentIndex() != idx:
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(idx)
            self.mode_combo.blockSignals(False)

    # ── 통합: 타임라인(과거 재생) 패널 ────────────────────────
    def _create_timeline_panel(self):
        self.timeline_dock = QDockWidget("Timeline / Playback", self)
        self.timeline_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea
        )
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(8, 4, 8, 4)

        # ● LIVE 버튼: 과거 탐색 중 현재(실시간)로 복귀
        self.live_button = QPushButton("● LIVE")
        self.live_button.setFixedWidth(80)
        self.live_button.setStyleSheet("color:#e33; font-weight:bold;")
        self.live_button.clicked.connect(self._on_go_live)
        row.addWidget(self.live_button)

        # ▶ Play/Pause: (시뮬레이션 정지 상태에서) 기록을 비디오처럼 재생
        self.play_button = QPushButton("▶ Play")
        self.play_button.setCheckable(True)
        self.play_button.setFixedWidth(90)
        self.play_button.toggled.connect(self._on_play_toggled)
        row.addWidget(self.play_button)

        self.timeline_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_slider.setMinimum(0)
        self.timeline_slider.setMaximum(1000)   # 0.0~1.0 → /1000
        self.timeline_slider.setValue(0)
        self.timeline_slider.sliderMoved.connect(self._on_timeline_scrubbed)
        self.timeline_slider.sliderPressed.connect(self._on_timeline_pressed)
        row.addWidget(self.timeline_slider, 1)

        self.timeline_time_label = QLabel("t = 0.0s")
        self.timeline_time_label.setMinimumWidth(190)
        row.addWidget(self.timeline_time_label)

        self.timeline_dock.setWidget(container)
        self._timeline_duration = 0.0
        self._timeline_start = 0.0
        self._sim_is_live = False     # 시뮬레이션이 실시간 진행 중인지
        self._update_live_button()

    def set_live_running(self, running: bool):
        """시뮬레이션 실시간 진행 여부를 패널에 알린다."""
        self._sim_is_live = running
        self._update_live_button()
        self.set_options_locked(running)

    def _update_live_button(self):
        # LIVE 버튼은 '실시간 진행 중'이고 '현재 과거를 보고 있을 때'만 활성화 의미가 있다.
        on = getattr(self, "_reviewing", False)
        if self._sim_is_live:
            self.live_button.setEnabled(True)
            if on:
                self.live_button.setText("● Go LIVE")
                self.live_button.setStyleSheet("color:#fff; background:#c33; font-weight:bold;")
            else:
                self.live_button.setText("● LIVE")
                self.live_button.setStyleSheet("color:#e33; font-weight:bold;")
            # 실시간 중에는 Play(과거재생) 비활성
            self.play_button.setEnabled(False)
        else:
            self.live_button.setEnabled(False)
            self.live_button.setText("● LIVE")
            self.live_button.setStyleSheet("color:#888;")
            self.play_button.setEnabled(True)

    def _on_go_live(self):
        self.go_live_signal.emit()

    def _on_play_toggled(self, checked: bool):
        self.play_button.setText("⏸ Pause" if checked else "▶ Play")
        self.playback_toggled_signal.emit(checked)

    def _on_timeline_pressed(self):
        # 스크럽 시작 시: 정지 상태면 재생을 멈춘다.
        # (실시간 진행 중이면 시뮬레이션은 건드리지 않고 '과거 보기'로만 들어간다.)
        if not self._sim_is_live and self.play_button.isChecked():
            self.play_button.setChecked(False)

    def _on_timeline_scrubbed(self, value: int):
        frac = value / 1000.0
        self.timeline_scrubbed_signal.emit(frac)

    def set_reviewing(self, reviewing: bool):
        """과거 탐색(리뷰) 상태 표시 갱신."""
        self._reviewing = reviewing
        self._update_live_button()

    def set_timeline_enabled(self, enabled: bool):
        """타임라인 진행바/Play 사용 가능 여부 (결과 우선 모드에서 비활성화)."""
        self.timeline_slider.setEnabled(enabled)
        if not enabled and self.play_button.isChecked():
            self.play_button.setChecked(False)
        # 재생이 불가능한 상태에서는 Play 버튼도 비활성화
        if not self._sim_is_live:
            self.play_button.setEnabled(enabled)

    def set_timeline_bounds(self, start_time: float, duration: float):
        self._timeline_start = start_time
        self._timeline_duration = duration

    def set_timeline_position(self, frac: float, sim_time: float, label_days: bool = False):
        """외부(앱)에서 재생 위치를 슬라이더에 반영."""
        v = int(max(0.0, min(1.0, frac)) * 1000)
        if self.timeline_slider.value() != v:
            self.timeline_slider.blockSignals(True)
            self.timeline_slider.setValue(v)
            self.timeline_slider.blockSignals(False)
        if label_days:
            self.timeline_time_label.setText(f"Day {sim_time / 86400.0:.3f}")
        else:
            self.timeline_time_label.setText(f"t = {sim_time:.1f}s")

    # ✅ EQ 토글 핸들러
    def _on_toggle_eq(self, state):
        self.show_eq = (state == Qt.CheckState.Checked.value)

        for _, item in self.eq_items.items():
            item.setVisible(self.show_eq)

        # EQ가 꺼지면 텍스트도 같이 꺼짐
        for _, t in self.eq_text_items.items():
            t.setVisible(self.show_eq and self.show_eq_text)

    def _on_toggle_eq_text(self, state):
        self.show_eq_text = (state == Qt.CheckState.Checked.value)
        for _, t in self.eq_text_items.items():
            t.setVisible(self.show_eq and self.show_eq_text)

    def update_statistics(self):
        ds = self.data_set
        clock = ds.main_clock
        processed = ds.num_of_processed_lot
        total = ds.lot_count
        oht_count = len(ds.oht_list)

        # [최적화] 단일 순회로 4가지 상태 카운트
        idle = repositioning = assigned = loaded = 0
        for o in ds.oht_list.values():
            s = o.status
            if s == "IDLE":
                idle += 1
            elif s == "REPOSITIONING":
                repositioning += 1
            elif s == "ASSIGNED":
                assigned += 1
            elif s == "LOADED":
                loaded += 1

        known = idle + repositioning + assigned + loaded
        other = max(oht_count - known, 0)

        throughput = (processed / clock * 3600) if clock > 0 else 0.0

        self._stat_labels["stat_time"].setText(f"{clock:.1f}s")
        self._stat_labels["stat_lots"].setText(f"{processed} / {total}")
        self._stat_labels["stat_ohts"].setText(str(oht_count))
        self._stat_labels["stat_idle"].setText(str(idle))
        self._stat_labels["stat_repositioning"].setText(str(repositioning))
        self._stat_labels["stat_idle_total"].setText(str(idle + repositioning))
        self._stat_labels["stat_assigned"].setText(str(assigned))
        self._stat_labels["stat_loaded"].setText(str(loaded))
        self._stat_labels["stat_other"].setText(str(other))
        self._stat_labels["stat_throughput"].setText(f"{throughput:.1f} lots/h")

        # ── 추가 물류/생산 KPI (SimulationLogger 누적치 기반) ──
        try:
            from ufast.common.logger import get_logger
            k = get_logger().get_live_kpis()
            self._stat_labels["stat_tt"].setText(f"{k['avg_transport_time']:.1f}s")
            self._stat_labels["stat_dt"].setText(f"{k['avg_delivery_time']:.1f}s")
            self._stat_labels["stat_call_wait"].setText(f"{k['avg_call_wait']:.1f}s")
            rd = k['avg_rundown_time']
            self._stat_labels["stat_rundown"].setText(
                f"{rd:.1f}s" if k['rundown_count'] else "-")
            self._stat_labels["stat_wip"].setText(str(k['wip']))
        except Exception:
            # 로거가 아직 준비되지 않았으면 조용히 건너뜀
            pass

    def create_menu_bar(self):
        menu_bar = self.menuBar()

        # File — 파일 종류별 그룹핑: Layout(DXF/Rail) / Flow Data(FromTo)
        file_menu = menu_bar.addMenu("&File")

        layout_menu = file_menu.addMenu("&Layout")

        load_dxf_action = QAction("Load &DXF...", self)
        load_dxf_action.setShortcut("Ctrl+O")
        load_dxf_action.triggered.connect(self.load_layout_dialog)
        layout_menu.addAction(load_dxf_action)

        load_rail_action = QAction("Load &Rail...", self)
        load_rail_action.triggered.connect(self.load_rail_dialog)
        layout_menu.addAction(load_rail_action)

        layout_menu.addSeparator()

        save_rail_action = QAction("&Save Rail...", self)
        save_rail_action.setShortcut("Ctrl+S")
        save_rail_action.triggered.connect(self.save_rail_dialog)
        layout_menu.addAction(save_rail_action)

        flow_menu = file_menu.addMenu("Flow &Data")

        load_fromto_action = QAction("Load &FromTo...", self)
        load_fromto_action.triggered.connect(self.load_fromto_dialog)
        flow_menu.addAction(load_fromto_action)

        # 생산(PySCFabSim) 데이터셋 — 폴더가 추가되어야 생산 모드가 열린다
        prod_menu = file_menu.addMenu("&Production Data")

        add_prod_dataset_action = QAction("Add Dataset &Folder...", self)
        add_prod_dataset_action.triggered.connect(self.production_add_dataset_signal.emit)
        prod_menu.addAction(add_prod_dataset_action)

        file_menu.addSeparator()

        reset_action = QAction("&Reset All", self)
        reset_action.setShortcut("Ctrl+N")
        reset_action.setStatusTip("Discard all loaded data and restore the initial state")
        reset_action.triggered.connect(self.clear_all)
        file_menu.addAction(reset_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.setStatusTip("Exit application")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View
        view_menu = menu_bar.addMenu("&View")
        self.toggle_layer_action = QAction("&Layer Panel", self)
        self.toggle_layer_action.setCheckable(True)
        self.toggle_layer_action.setChecked(True)
        view_menu.addAction(self.toggle_layer_action)

        self.toggle_control_action = QAction("&Simulation Panel", self)
        self.toggle_control_action.setCheckable(True)
        self.toggle_control_action.setChecked(True)
        view_menu.addAction(self.toggle_control_action)

        self.toggle_results_action = QAction("&Results Panel", self)
        self.toggle_results_action.setCheckable(True)
        self.toggle_results_action.setChecked(True)
        view_menu.addAction(self.toggle_results_action)

        self.toggle_timeline_action = QAction("&Timeline Panel", self)
        self.toggle_timeline_action.setCheckable(True)
        self.toggle_timeline_action.setChecked(True)
        view_menu.addAction(self.toggle_timeline_action)

        # Simulation
        sim_menu = menu_bar.addMenu("&Simulation")
        run_action = QAction("&Run", self)
        run_action.triggered.connect(self.emit_start_simulation)
        stop_action = QAction("&Stop", self)
        stop_action.triggered.connect(self.emit_stop_simulation)
        save_log_action = QAction("Save &Log...", self)
        save_log_action.triggered.connect(self.emit_save_log)
        sim_menu.addAction(run_action)
        sim_menu.addAction(stop_action)
        sim_menu.addSeparator()
        sim_menu.addAction(save_log_action)

    def load_layout_dialog(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Open DXF Layout", ".", "DXF Files (*.dxf);;All Files (*)")
        if fname:
            self.layout_file_loaded_signal.emit(fname)

    def load_rail_dialog(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Open Rail File", ".", "Rail Files (*.rail);;All Files (*)")
        if fname:
            self.rail_file_loaded_signal.emit(fname)

    def load_fromto_dialog(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Open FromTo File", ".", "FromTo Files (*.dat);;All Files (*)")
        if fname:
            self.fromto_file_loaded_signal.emit(fname)

    def save_rail_dialog(self):
        fname, _ = QFileDialog.getSaveFileName(self, "Save Rail File", ".", "Rail Files (*.rail);;All Files (*)")
        if fname:
            self.rail_file_save_signal.emit(fname)

    def update_status_coords(self, x: float, y: float):
        self.statusBar().showMessage(f"X: {x:.1f}  Y: {y:.1f}")

    def clear_all(self):
        # 로딩 데이터·시뮬레이션 상태가 전부 사라지므로 실행 전에 확인을 받는다.
        answer = QMessageBox.question(
            self,
            "Reset All",
            "Discard all loaded data (layout, flow data) and simulation results?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.scene.clear()
        self.oht_items.clear()
        self.eq_items.clear()
        self.eq_text_items.clear()
        self._eq_render_state.clear()
        self.layer_panel.clear_layers()
        self.clear_signal.emit()

    def emit_start_simulation(self):
        self.simulation_start_signal.emit()

    def emit_stop_simulation(self):
        self.simulation_stop_signal.emit()

    def emit_save_log(self):
        self.save_log_signal.emit()

    def _add_figure(self, fig, pen):
        if isinstance(fig, CLine):
            self.scene.addLine(fig.start_x, fig.start_y, fig.end_x, fig.end_y, pen)
            return fig.start_x, fig.start_y, fig.end_x, fig.end_y
        elif isinstance(fig, CQuadCurve):
            path = QPainterPath()
            path.moveTo(fig.start_x, fig.start_y)
            path.quadTo(fig.ctrl_x, fig.ctrl_y, fig.end_x, fig.end_y)
            item = QGraphicsPathItem(path)
            item.setPen(pen)
            self.scene.addItem(item)
            xs = [fig.start_x, fig.ctrl_x, fig.end_x]
            ys = [fig.start_y, fig.ctrl_y, fig.end_y]
            return min(xs), min(ys), max(xs), max(ys)
        elif isinstance(fig, CText):
            r = 200  # 표시용 크기(필요시 조절)
            dot = QGraphicsEllipseItem(fig.start_x - r, fig.start_y - r, 2 * r, 2 * r)
            dot.setPen(QPen(pen.color(), max(1, int(pen.widthF() * 0.2))))
            dot.setBrush(QBrush(pen.color()))
            self.scene.addItem(dot)

            txt = QGraphicsSimpleTextItem(fig.text)
            txt.setBrush(QBrush(pen.color()))
            txt.setTransform(QTransform(1, 0, 0, -1, 0, 0))  # Y반전 보정
            txt.setPos(fig.start_x + r * 1.2, fig.start_y + r * 0.5)
            self.scene.addItem(txt)

            return fig.start_x - r, fig.start_y - r, fig.start_x + r, fig.start_y + r

        return None

    def draw_layout(self):
        self.scene.clear()
        self.oht_items.clear()

        config = ConfigLoader.get_instance()
        rail_color = config.get("appearance", "rail_color", "#FFFFFF")
        rail_width = config.get("appearance", "rail_width", 100)
        section_color = config.get("appearance", "section_color", "#FFFF00")

        pen_rail = QPen(QColor(rail_color), rail_width)
        pen_section = QPen(QColor(section_color), rail_width)
        pen_bg = QPen(QColor(255, 255, 255), 20)

        total = 0
        if hasattr(self.data_set, "layers"):
            for layer in self.data_set.layers:
                if layer.is_turned_on:
                    total += len(layer.shape_list)
        for section in self.data_set.sections:
            total += len(section.figures)

        progress = None
        if total >= 500:
            progress = QProgressDialog("Drawing layout...", None, 0, total, self)
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)

        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")
        drawn = 0

        def expand_bounds(x1, y1, x2, y2):
            nonlocal min_x, min_y, max_x, max_y
            min_x = min(min_x, x1, x2)
            min_y = min(min_y, y1, y2)
            max_x = max(max_x, x1, x2)
            max_y = max(max_y, y1, y2)

        # Layers
        if hasattr(self.data_set, "layers"):
            for layer in self.data_set.layers:
                if not layer.is_turned_on:
                    continue
                pen = pen_rail if layer.is_rail_layer else pen_bg
                for fig in layer.shape_list:
                    bounds = self._add_figure(fig, pen)
                    if bounds:
                        expand_bounds(*bounds)
                    drawn += 1
                    if progress and drawn % 200 == 0:
                        progress.setValue(drawn)
                        QApplication.processEvents()

        # Sections
        for section in self.data_set.sections:
            for fig in section.figures:
                bounds = self._add_figure(fig, pen_section)
                if bounds:
                    expand_bounds(*bounds)
                drawn += 1
                if progress and drawn % 200 == 0:
                    progress.setValue(drawn)
                    QApplication.processEvents()

        # ✅ EQ 표시
        self.eq_items.clear()
        self.eq_text_items.clear()
        self._eq_render_state.clear()

        eq_size = rail_width * 6
        # EQ 초기 색상은 IDLE (회색) - 시뮬레이션 중 update_animation에서 동적 변경
        idle_fill, idle_edge = EQ_STATUS_STYLES["IDLE"]
        pen_eq_idle = QPen(idle_edge, rail_width * 0.5)
        brush_eq_idle = QBrush(idle_fill)
        font_eq = QFont("Arial", max(int(eq_size * 0.3), 1))

        for eq_name, eq in self.data_set.eq_list.items():
            x, y = eq.left, eq.top

            rect_item = QGraphicsRectItem(x - eq_size / 2, y - eq_size / 2, eq_size, eq_size)
            rect_item.setPen(pen_eq_idle)
            rect_item.setBrush(brush_eq_idle)
            rect_item.setZValue(5)
            self.scene.addItem(rect_item)
            expand_bounds(x - eq_size / 2, y - eq_size / 2, x + eq_size / 2, y + eq_size / 2)

            text_item = QGraphicsSimpleTextItem(eq_name)
            text_item.setFont(font_eq)
            text_item.setBrush(QBrush(QColor(200, 200, 200)))  # 흰색 계열 텍스트
            text_item.setZValue(6)
            text_item.setTransform(QTransform(1, 0, 0, -1, 0, 0))  # 텍스트 뒤집힘 방지
            text_item.setPos(x + eq_size * 0.6, y + eq_size * 0.2)
            self.scene.addItem(text_item)

            self.eq_items[eq_name] = rect_item
            self.eq_text_items[eq_name] = text_item

        if progress:
            progress.setValue(total)

        # Fit view
        if min_x < max_x and min_y < max_y:
            margin = max(max_x - min_x, max_y - min_y) * 0.05
            rect = QRectF(
                min_x - margin, min_y - margin,
                (max_x - min_x) + margin * 2, (max_y - min_y) + margin * 2
            )
            self.scene.setSceneRect(rect)
            self.view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

        # ✅ 현재 토글 상태 즉시 반영
        self._on_toggle_eq(Qt.CheckState.Checked.value if self.show_eq else Qt.CheckState.Unchecked.value)
        self._on_toggle_eq_text(Qt.CheckState.Checked.value if self.show_eq_text else Qt.CheckState.Unchecked.value)

    def update_animation(self):
        # 통합: 재생(scrub) 중에는 라이브 애니메이션을 멈추고 스냅샷만 표시
        if getattr(self, "_playback_mode", False):
            return
        current_time = self.data_set.main_clock

        # OHT 생성 및 업데이트
        for name, oht in self.data_set.oht_list.items():
            if name not in self.oht_items:
                item = OHTItem(oht)
                self.scene.addItem(item)
                self.oht_items[name] = item
            self.oht_items[name].update_position(current_time)

        # EQ 상태별 색상 업데이트
        # 대형 SMAT2022 layout은 EQ가 1,000개 이상이므로 매 프레임 전체 EQ를 재도색하지 않는다.
        # 상태/대기 LOT 수가 바뀐 항목만 갱신하고, 전체 스캔도 약 100ms 단위로 제한한다.
        self._eq_update_counter += 1
        if self._eq_update_counter % 6 == 0:
            for eq_name, rect_item in self.eq_items.items():
                eq = self.data_set.eq_list.get(eq_name)
                if eq is None:
                    continue
                status = getattr(eq, 'eq_status', 'IDLE')
                lot_count = getattr(eq, 'waiting_lot_count', 0)
                state_key = (status, lot_count)
                if self._eq_render_state.get(eq_name) == state_key:
                    continue
                self._eq_render_state[eq_name] = state_key

                fill, edge = EQ_STATUS_STYLES[eq_status_key(status)]
                rect_item.setBrush(QBrush(fill))
                rect_item.setPen(QPen(edge, rect_item.pen().widthF()))

                text_item = self.eq_text_items.get(eq_name)
                if text_item and lot_count > 0:
                    text_item.setText(f"{eq_name} [{lot_count}]")
                elif text_item:
                    text_item.setText(eq_name)

        # 통계 갱신
        if not hasattr(self, "_stat_counter"):
            self._stat_counter = 0
        self._stat_counter += 1
        if self._stat_counter % 10 == 0:
            self.update_statistics()
        # 명시적 전체 viewport 갱신은 하지 않는다. 변경된 QGraphicsItem만 자동 invalidation된다.

    # ── 통합: 타임라인 스냅샷 렌더 (과거 재생) ────────────────
    def render_logistics_snapshot(self, snap):
        """
        TimelineRecorder 의 물류 스냅샷을 화면에 복원한다.

        중요: 라이브 OHT 데이터 객체를 변경하지 않는다(apply_snapshot 사용).
        따라서 시뮬레이션이 백그라운드에서 계속 진행 중이어도 과거 장면을
        안전하게 들여다볼 수 있다. update_animation 은 _playback_mode 플래그로
        잠시 멈춰 라이브 렌더와 충돌하지 않게 한다.
        """
        self._playback_mode = True
        ds = self.data_set
        sim_time = snap.sim_time

        for name, info in snap.ohts.items():
            oht = ds.oht_list.get(name)
            item = self.oht_items.get(name)
            if item is None:
                if oht is None:
                    continue
                item = OHTItem(oht)
                self.scene.addItem(item)
                self.oht_items[name] = item
            item.apply_snapshot(
                info["section_id"], info["enter_time"], info["status"], sim_time
            )

        for eq_name, einfo in snap.eqs.items():
            rect_item = self.eq_items.get(eq_name)
            if rect_item is None:
                continue
            status = einfo.get("status", "IDLE")
            lot_count = einfo.get("lot_count", 0)
            fill, _edge = EQ_STATUS_STYLES[eq_status_key(status)]
            rect_item.setBrush(QBrush(fill))
            text_item = self.eq_text_items.get(eq_name)
            if text_item:
                text_item.setText(f"{eq_name} [{lot_count}]" if lot_count > 0 else eq_name)

    def exit_playback_mode(self):
        """재생/리뷰 모드 해제 → 라이브 애니메이션 재개."""
        self._playback_mode = False
