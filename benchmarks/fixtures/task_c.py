"""The public Task C snapshots, generated rather than stored as files."""

from typing import Dict


PROMPT = """Refactor duplicate tag normalization into normalization.py. Export normalize_tags(values), then make both report.py and cli.py import it directly. Preserve trimming, lowercasing, blank removal, and stable deduplication. Do not leave another normalize_tags definition in report.py or cli.py. Make python acceptance.py pass."""
ACCEPTANCE = '''from pathlib import Path
from cli import preview
from report import render_report
values = [" Alpha ", "beta", "ALPHA", "", "Beta ", "gamma"]
assert render_report(values) == "alpha,beta,gamma"
assert preview(values) == "alpha | beta | gamma"
assert Path("normalization.py").is_file()
assert "def normalize_tags" not in Path("report.py").read_text(encoding="utf-8")
assert "from normalization import normalize_tags" in Path("report.py").read_text(encoding="utf-8")
assert "from normalization import normalize_tags" in Path("cli.py").read_text(encoding="utf-8")
'''
HELPER = '''def normalize_tags(values):
    result = []
    for value in values:
        tag = value.strip().lower()
        if tag and tag not in result:
            result.append(tag)
    return result
'''
REPORT_OLD = HELPER + '''\n\ndef render_report(values):
    return ",".join(normalize_tags(values))
'''
REPORT_NEW = '''from normalization import normalize_tags


def render_report(values):
    return ",".join(normalize_tags(values))
'''
CLI_OLD = '''from report import normalize_tags


def preview(values):
    return " | ".join(normalize_tags(values))
'''
CLI_NEW = '''from normalization import normalize_tags


def preview(values):
    return " | ".join(normalize_tags(values))
'''
SNAPSHOTS = {
    "30": ({"report.py": REPORT_OLD, "cli.py": CLI_OLD}, "Extract the helper into normalization.py and migrate both consumers directly."),
    "60": ({"normalization.py": HELPER, "report.py": REPORT_OLD, "cli.py": CLI_OLD}, "The shared helper exists, but duplicate code and the cli-to-report dependency remain."),
    "80": ({"normalization.py": HELPER, "report.py": REPORT_NEW, "cli.py": CLI_OLD}, "report.py is migrated; make cli.py import normalization.py directly."),
}


def snapshot_files(snapshot: str) -> Dict[str, str]:
    """Every file of one snapshot, laid out as the other two tasks store them."""
    files, _ = SNAPSHOTS[snapshot]
    return dict(files, **{"acceptance.py": ACCEPTANCE})


def context(snapshot: str) -> str:
    """The source agent's reasoning, in the sections the fixtures use."""
    _, handoff = SNAPSHOTS[snapshot]
    return "## Next step\n\n" + handoff + "\n"
