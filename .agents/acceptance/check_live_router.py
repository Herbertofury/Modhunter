"""Check the current router without treating a historical source tree as immutable."""
import argparse
import hashlib
import unittest
from pathlib import Path
from test_guidance import ROOT, SharedGuidanceTests

ORIGINAL_SHA256 = "04cf47717bb15f1136fe117dbc788c71b08c06b11f977168cf4e76f468fc0aae"
REQUIRED_CURRENT_ROUTES = (
    "guidance/PRE_RELEASE_OPTIMIZATION.md",
    "guidance/CLI_FIRST_EXECUTION.md",
    "docs/GAME-SYSTEMS-EXCHANGE.md",
    "guidance/DESIGN_QUALITY_STANDARD.md#portfolio-interface-quality-contract",
    "guidance/templates/INTERFACE_QUALITY_RECEIPT.md",
)

def check(original_router: Path) -> None:
    payload = original_router.read_bytes()
    if hashlib.sha256(payload).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("Historical router does not match its reviewed source hash")
    live = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    # Whole nonblank original lines must remain intact and in order. Unrelated
    # product files may evolve; the historical job independently verifies them.
    it = iter(live.splitlines())
    for idx, expected in enumerate(payload.decode("utf-8").splitlines(), start=1):
        if expected.strip() and not any(line == expected for line in it):
            raise ValueError(f"Original owner routing changed or removed at line {idx}")
    for suffix in REQUIRED_CURRENT_ROUTES:
        required = "https://github.com/Herbertofury/Athena/blob/main/" + suffix
        if live.count(required) != 1:
            raise ValueError(f"Missing or ambiguous live route: {suffix}")
    suite = unittest.TestSuite([SharedGuidanceTests("test_pinned_athena_snapshots_complete")])
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise ValueError("Current shared guidance snapshot or host binding drifted")
    print("Current router preserves original ownership, pinned guidance and current links.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original_router", type=Path)
    try:
        check(parser.parse_args().original_router)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
