from app.system_certs import use_system_certificates


def test_downloads_use_the_systems_certificates():
    # Windows ARM, 2026-10-22: Maxon's root was not yet in Windows' store, so Python's own check failed
    called = []
    assert use_system_certificates(inject=lambda: called.append(1)) and called == [1]


def test_without_truststore_the_old_check_stays():
    def missing():
        raise ImportError("no truststore")
    assert use_system_certificates(inject=missing) is False
