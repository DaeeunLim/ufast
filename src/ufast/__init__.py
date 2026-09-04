"""
U-FAST (Unified Fab-AMHS Simulation Toolkit)
— 생산(fab) 시뮬레이션과 물류(AMHS) 시뮬레이션의 통합 co-simulation 툴킷.

서브패키지:
  cosim        통합 실행기 (run/analyze/aggregate, AMHS 코어)
  production   생산 DES (PySCFabSim 포크)
  route        AMHS 경로 관리 (Dijkstra + 혼잡 penalty)
  common       파서·로거·전략 로더 공용 유틸
  drawing      CAD 도형 데이터 모델
  core         GUI용 시뮬레이션 엔티티 (OHT·EQ·Section)
  control      물류 이벤트 컨트롤러 (GUI 모드)
  layout       레일 도면 ↔ Section 변환
  gui          PyQt6 위젯
  viz          Rerun 3D 시각화
  integration  GUI 생산 시뮬 연동
  verification 사후 규칙 위반 점검
"""

__version__ = "0.1.0"
