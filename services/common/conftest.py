import pathlib
import sys

# 让 pytest 直接 from rtvoice_auth import ...
sys.path.insert(0, str(pathlib.Path(__file__).parent))
