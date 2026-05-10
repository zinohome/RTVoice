def test_version():
    import rtvoice_admin
    assert rtvoice_admin.__version__ == "0.13.0"


def test_main_version_command(capsys):
    from rtvoice_admin.__main__ import main
    rc = main(["version"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "0.13.0" in captured.out
