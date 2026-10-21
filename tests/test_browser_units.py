"""Run the browser unit tests under pytest.

The frontend has to convert GEN text to wei and rate text to 1e8 integers. That
conversion is exactly where the Steward found the amount being scaled by 1e8, so
the helpers live in units.mjs and are tested with the same arithmetic the page
uses. Node is required; without it the JS tests can still be run by hand with
`node --test tests/js/units.test.mjs`.
"""

import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_browser_units_match_the_contract_model():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; run `node --test tests/js/units.test.mjs`")
    r = subprocess.run(
        [node, "--test", str(ROOT / "tests" / "js" / "units.test.mjs")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert r.returncode == 0, r.stdout + r.stderr
