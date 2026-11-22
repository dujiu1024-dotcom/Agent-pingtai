from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/architecture/L4_T09_ATOMIC_CI_PRODUCTION_EVIDENCE_MATRIX.md"


def test_issue_848_l4_t09_matrix_has_all_unique_atomic_ids_and_evidence_columns() -> None:
    content = MATRIX.read_text(encoding="utf-8")
    rows = [
        line for line in content.splitlines()
        if line.startswith("| `") and re.match(r"\| `[CA]-\d+-\d{2}` \|", line)
    ]
    ids = [re.match(r"\| `([CA]-\d+-\d{2})` \|", row).group(1) for row in rows]
    assert len(ids) == 186
    assert len(set(ids)) == 186
    assert all((8 <= int(item.split("-")[1]) <= 31) or int(item.split("-")[1]) == 72 for item in ids)
    assert all(row.count("|") >= 8 for row in rows)
    assert "PRODUCTION_ACCEPTANCE_REGISTER.md" in content
    assert "NO-GO" in content
    assert "a1f621e3e8c3740f21c5e3cf1ed5be347b419e0d" in content


def test_matrix_does_not_treat_historical_checkbox_or_pr_merge_as_acceptance() -> None:
    content = MATRIX.read_text(encoding="utf-8")
    assert "checkbox 的完成含义" in content
    assert "PR merged" in content
    assert "queued/in_progress/skipped/cancelled 均不能算 pass" in content
