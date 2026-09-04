"""저장소 기준 경로 상수 (repo/editable 설치 기준).

pip 로 site-packages 에 설치된 경우 REPO_ROOT 는 의미가 없으므로,
Dataset·results 기본값은 저장소 안에서 실행할 때의 편의 기능이다.
CLI 인자로 명시 경로를 주면 이 상수들은 사용되지 않는다.
"""
import os

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))       # .../src/ufast
SRC_DIR = os.path.dirname(_PKG_DIR)                          # .../src
REPO_ROOT = os.path.dirname(SRC_DIR)                         # 저장소 루트
DATASET_DIR = os.path.join(REPO_ROOT, 'dataset')
RESULTS_DIR = os.path.join(REPO_ROOT, 'results')
