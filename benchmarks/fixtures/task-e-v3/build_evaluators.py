"""Build task E v3's evaluators from task E v2's.

    python build_evaluators.py

v3's project ledger is a million rows, and the evaluators are probed every
second while a session runs, so they cannot read it. They judge the report
code on task E v2's four-week ledger instead -- the same report, the same two
causes, the same hidden rows -- carried inside the evaluator so the target
cannot edit it.
"""

import base64
import gzip
from pathlib import Path

HERE = Path(__file__).resolve().parent
V2 = HERE.parent / "task-e-v2"

OLD = '''def public_rows():
    data = Path("data/transactions.csv").read_bytes()
    if hashlib.sha256(data).hexdigest() != PUBLIC_LEDGER_SHA256:
        raise SystemExit("data/transactions.csv is not the ledger the fixture ships")
    return list(csv.DictReader(data.decode("utf-8").splitlines()))
'''

NEW = '''#: Task E v2's ledger, gzipped: the project's own is too large to read here.
LEDGER = %r


def public_rows():
    data = gzip.decompress(base64.b64decode(LEDGER))
    if hashlib.sha256(data).hexdigest() != PUBLIC_LEDGER_SHA256:
        raise SystemExit("the embedded ledger is damaged")
    return list(csv.DictReader(data.decode("utf-8").splitlines()))
'''


def main() -> None:
    ledger = (V2 / "project" / "data" / "transactions.csv").read_bytes()
    blob = base64.b64encode(gzip.compress(ledger, mtime=0)).decode("ascii")
    for name in ("progress.py", "acceptance.py"):
        text = (V2 / name).read_text(encoding="utf-8")
        assert text.count(OLD) == 1, name
        text = text.replace(OLD, NEW % blob)
        text = text.replace("import csv\n", "import base64\nimport csv\nimport gzip\n", 1)
        text = text.replace("evaluator for task E v2.", "evaluator for task E v3.", 1)
        text = text.replace(
            "Run with the target project as cwd.\n",
            "Run with the target project as cwd.\n\n"
            "Judged on task E v2's ledger, embedded below; see build_evaluators.py.\n", 1)
        (HERE / name).write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
