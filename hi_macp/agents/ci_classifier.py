from typing import Dict, Any
import re


class CIFailureClassifier:
    """Lightweight classifier to tag CI failure reason/location from logs or status."""

    def classify(self, log_summary: Dict[str, Any]) -> Dict[str, Any]:
        reason = "unknown"
        location = None
        errors = log_summary.get("errors") or []
        text = "\n".join(errors)[:5000]

        patterns = [
            ("import_error", r"ImportError|ModuleNotFoundError"),
            ("test_failure", r"FAIL|AssertionError"),
            ("lint_error", r"flake8|pylint|lint"),
            ("timeout", r"timeout"),
            ("dependency_error", r"pip install|Could not find a version"),
        ]
        for label, pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                reason = label
                break

        # extract a file:line hint
        loc_match = re.search(r"([A-Za-z0-9_./-]+\.py):(\d+)", text)
        if loc_match:
            location = f"{loc_match.group(1)}:{loc_match.group(2)}"

        return {"ci_failure_reason": reason, "ci_failure_location": location}
