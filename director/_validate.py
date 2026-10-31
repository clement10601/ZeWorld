"""載入 content-pack/validate.py（資料夾名稱有連字號，不能直接 import）。"""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parent.parent / "content-pack" / "validate.py"
_spec = importlib.util.spec_from_file_location("zeworld_validate", _path)
validate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate)
