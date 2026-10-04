# Empty on purpose: lets pytest import data/ and engine/ from the repo root.


def pytest_configure(config):
    config.addinivalue_line("markers", "julia: uses the real Julia-1 model (skipped when it is not installed)")
