"""Minimal runner so the suite also runs where pytest is not installed: python tests/run.py"""
import inspect
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_app as T  # noqa: E402
from app import create_app  # noqa: E402
from app.config import TestConfig  # noqa: E402
from app import db as _db  # noqa: E402

failed = 0
for name, fn in sorted(vars(T).items()):
    if not name.startswith("test_") or not callable(fn):
        continue
    tmp = Path(tempfile.mkdtemp())
    _db.reset_memory_db()
    TestConfig.MEDIA_DIR = tmp / "media"
    app = create_app(TestConfig)
    kwargs = {}
    for p in inspect.signature(fn).parameters:
        kwargs[p] = {"app": app, "client": app.test_client(), "tmp_path": tmp}[p]
    try:
        fn(**kwargs)
        print(f"PASS {name}")
    except Exception:
        failed += 1
        print(f"FAIL {name}")
        traceback.print_exc()
print(f"\n{failed} failed")
sys.exit(1 if failed else 0)
