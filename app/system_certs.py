"""HTTPS downloads check certificates with the operating system's own trust store (truststore, the way pip does).

Windows ARM test machine, 2026-10-22: Cinebench's download from Maxon failed with CERTIFICATE_VERIFY_FAILED. Python
reads only the root certificates Windows already has, and a fresh Windows fetches a missing root the first time
Windows itself checks a site; after PowerShell opened the same site, Python's download worked. truststore asks Windows
(or macOS, or Linux's OpenSSL store) to check, so the missing root is fetched as it would be for a browser."""
import logging

logger = logging.getLogger(__name__)


def use_system_certificates(inject=None) -> bool:
    """True when HTTPS now checks certificates through the system; False when truststore is not there (the old
    behaviour stays: Python's own check)."""
    try:
        if inject is None:
            import truststore
            inject = truststore.inject_into_ssl
        inject()
        return True
    except Exception:
        logger.warning("truststore not used: HTTPS checks certificates with Python's own store", exc_info=True)
        return False
