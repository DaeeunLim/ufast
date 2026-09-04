"""
ufast — 생산(PySCFabSim) + 물류(U-FAST AMHS) 통합 시뮬레이션.

생산 레이어가 공정 step 사이에 이송 작업을 발주하고, 물류 레이어가
유한한 OHT 풀로 그 작업을 물리적으로 수행한다. 두 레이어는 단일
next-event 이벤트 큐(생산 Instance.events)와 단일 클럭을 공유한다.
"""
