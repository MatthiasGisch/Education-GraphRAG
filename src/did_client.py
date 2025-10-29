"""
DEPRECATED: D-ID integration removed.

The original `src/did_client.py` implementation has been intentionally removed
and replaced with this minimal stub to avoid accidental usage.

If you need to restore D-ID support, revert this file from version control or
re-implement the client. The application now uses `src.synthesia_client`.
"""


def __getattr__(name):
    raise RuntimeError(
        "D-ID integration has been archived to 'archive/removed_did_client.py' and removed from active code.\n"
        "If you need the original implementation restore it from git or from the archive file."
    )

