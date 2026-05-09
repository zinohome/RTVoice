"""Smoke test: package importable, version present."""


def test_version():
    import rtvoice_client
    assert rtvoice_client.__version__ == "0.1.0"


def test_package_layout():
    import rtvoice_client
    assert "Client" in rtvoice_client.__all__
    assert "AsyncClient" in rtvoice_client.__all__


def test_py_typed_marker_exists():
    import rtvoice_client
    from pathlib import Path
    pkg_dir = Path(rtvoice_client.__file__).parent
    assert (pkg_dir / "py.typed").is_file()
