"""Repository-relative path constants (for repo / editable installs).

When the package is installed into site-packages with pip, REPO_ROOT is meaningless, so the
Dataset/results defaults are a convenience for running from inside the repository.
These constants are not used when explicit paths are given as CLI arguments.
"""
import os

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))       # .../src/ufast
SRC_DIR = os.path.dirname(_PKG_DIR)                          # .../src
REPO_ROOT = os.path.dirname(SRC_DIR)                         # repository root
DATASET_DIR = os.path.join(REPO_ROOT, 'dataset')
RESULTS_DIR = os.path.join(REPO_ROOT, 'results')
