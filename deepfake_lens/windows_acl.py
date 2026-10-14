"""R22-2 (round 22): owner-only folders on Windows for the relocated tool base.

R21-10 moved the tool's own folders (logs, heatmaps, the numba cache) out of
an examined folder to the first usable candidate — but on Windows every
candidate (``%LOCALAPPDATA%``, ``%TEMP%``) is inside the user profile, so
``classify %USERPROFILE%\\x.jpg`` and ``cd ~; deepfake-lens web`` still ended
with exit 2. User decision 2026-10-14: a candidate outside the profile,
``%ProgramData%\\deepfake-lens\\<user>``, made with an owner-only ACL and
verified like the POSIX candidates (this user's own folder that nobody else
can write).

ProgramData is shared by every account, so the checks are the ACL analogue
of the POSIX rule (owner = this user, no group/other write):

- the folder's owner is this user (or SYSTEM / Administrators, which can do
  anything anyway), and every allow entry of its DACL that grants any write,
  delete or permission right names this user, SYSTEM, Administrators or
  OWNER RIGHTS; a NULL DACL (everyone: full access) is refused;
- its parent ``%ProgramData%\\deepfake-lens`` is owned by this user, SYSTEM or
  Administrators and grants nobody else a right to delete or rename its
  entries or change its permissions (another account may create its own
  subfolder there — the default ProgramData inheritance — but not replace
  this one).

A folder this process creates gets the protected DACL
:data:`OWNER_ONLY_SDDL` (SYSTEM, Administrators, owner: full; nothing
inherited) — what ``os.mkdir(mode=0o700)`` applies on Python 3.13+, applied
explicitly so 3.11/3.12 get it too.

R23-6 (round 23): an inherit-only entry (``INHERIT_ONLY_ACE``, icacls
``(IO)``) applies to the folder's future children, not to the folder. The
default ``C:\\ProgramData`` DACL has ``CREATOR OWNER:(OI)(CI)(IO)(F)`` and a
folder made under it inherits that entry; :func:`dacl_parent_safe` read it as
"another account may replace entries" and refused every default parent, so
on a real Windows the relocation never happened (the R22-2 fixture had no
such entry). The parent check now ignores inherit-only entries (this user's
own folder below it is checked on its own DACL, which is protected), and
:func:`dacl_owner_only` accepts CREATOR OWNER (what this user creates inside
its own folder is this user's). When this tool creates
``%ProgramData%\\deepfake-lens`` itself it gets the protected
:data:`SHARED_PARENT_SDDL` (:func:`make_shared_parent`). Residual
(documented): a parent another account created first is owned by that
account, so this user's ProgramData candidate is refused (the other
candidates, or exit 2, as before).

The decisions are pure functions over ``(owner SID, [(ACE type, flags, mask,
SID)])`` (:func:`dacl_owner_only`, :func:`dacl_parent_safe`) so they are
tested on every OS; only :func:`_read_security` / :func:`make_owner_only`
call the Windows API (ctypes, advapi32) and are exercised through mocks
elsewhere ("Windows/macOS paths: fake objects and mocks only", HANDOFF §7).
"""

from __future__ import annotations

import logging
import os
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

# SYSTEM, Administrators, OWNER RIGHTS full access; protected (nothing inherited).
OWNER_ONLY_SDDL = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)"
# R23-6: %ProgramData%\deepfake-lens when this tool makes it — protected; SYSTEM / Administrators full, the owner
# full on the folder itself, CREATOR OWNER full on what each account creates inside, Users read, traverse and
# add a subfolder (0x1200a9 | FILE_ADD_SUBDIRECTORY) on the folder only — nobody else may delete, rename or
# re-permission an entry.
SHARED_PARENT_SDDL = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;;FA;;;OW)(A;OICIIO;FA;;;CO)(A;;0x1200ad;;;BU)"
SID_SYSTEM = "S-1-5-18"
SID_ADMINISTRATORS = "S-1-5-32-544"
SID_OWNER_RIGHTS = "S-1-3-4"
SID_CREATOR_OWNER = "S-1-3-0"
TRUSTED_SIDS = frozenset({SID_SYSTEM, SID_ADMINISTRATORS})
ACCESS_ALLOWED_ACE_TYPE = 0
ACCESS_DENIED_ACE_TYPE = 1
# ACE flags (icacls (OI) (CI) (NP) (IO) (I)).
OBJECT_INHERIT_ACE = 0x1
CONTAINER_INHERIT_ACE = 0x2
NO_PROPAGATE_INHERIT_ACE = 0x4
INHERIT_ONLY_ACE = 0x8
INHERITED_ACE = 0x10
# Rights that change the folder's content, names or permissions.
FILE_WRITE_DATA = 0x2  # add a file
FILE_APPEND_DATA = 0x4  # add a subfolder
FILE_WRITE_EA = 0x10
FILE_DELETE_CHILD = 0x40
FILE_WRITE_ATTRIBUTES = 0x100
DELETE = 0x10000
WRITE_DAC = 0x40000
WRITE_OWNER = 0x80000
GENERIC_ALL = 0x10000000
GENERIC_WRITE = 0x40000000
WRITE_RIGHTS = (
    FILE_WRITE_DATA | FILE_APPEND_DATA | FILE_WRITE_EA | FILE_DELETE_CHILD | FILE_WRITE_ATTRIBUTES
    | DELETE | WRITE_DAC | WRITE_OWNER | GENERIC_ALL | GENERIC_WRITE
)
# Rights over a shared parent that let an account remove or replace another account's entry.
REPLACE_RIGHTS = FILE_DELETE_CHILD | DELETE | WRITE_DAC | WRITE_OWNER | GENERIC_ALL


class Ace(NamedTuple):
    type: int
    flags: int
    mask: int
    sid: str


class Security(NamedTuple):
    """A file's owner SID and DACL (``None``: a NULL DACL — everyone has full access)."""

    owner: str
    dacl: list[Ace] | None


def dacl_owner_only(security: Security, user_sid: str) -> bool:
    """R22-2: the folder is this user's own and nobody else can write, delete or re-permission it."""
    if security.owner not in {user_sid, *TRUSTED_SIDS} or security.dacl is None:
        return False
    # R23-6: CREATOR OWNER names whoever creates a child — inside this folder only this user can (or an admin).
    # Any other account's entry counts, inherit-only included: it would apply to what this user writes inside.
    allowed = {user_sid, SID_OWNER_RIGHTS, SID_CREATOR_OWNER, *TRUSTED_SIDS}
    for ace in security.dacl:
        if ace.type == ACCESS_DENIED_ACE_TYPE:
            continue
        if ace.type != ACCESS_ALLOWED_ACE_TYPE:
            return False  # an object / callback allow entry this check does not read
        if ace.mask & WRITE_RIGHTS and ace.sid not in allowed:
            return False
    return True


def dacl_parent_safe(security: Security, user_sid: str) -> bool:
    """R22-2: the shared parent lets no other account delete, rename or re-permission this user's folder."""
    if security.owner not in {user_sid, *TRUSTED_SIDS} or security.dacl is None:
        return False
    allowed = {user_sid, SID_OWNER_RIGHTS, *TRUSTED_SIDS}
    for ace in security.dacl:
        if ace.flags & INHERIT_ONLY_ACE:
            continue  # R23-6: applies to the folder's children only (CREATOR OWNER:(OI)(CI)(IO)(F) of ProgramData)
        if ace.type == ACCESS_DENIED_ACE_TYPE:
            continue
        if ace.type != ACCESS_ALLOWED_ACE_TYPE:
            return False
        if ace.mask & REPLACE_RIGHTS and ace.sid not in allowed:
            return False
    return True


# --- Windows API (ctypes) -------------------------------------------------------

def _advapi() -> Any:
    import ctypes

    return ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]


def _sid_text(advapi: Any, sid: Any) -> str:
    import ctypes

    text = ctypes.c_wchar_p()
    if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW")  # type: ignore[attr-defined]
    try:
        return str(text.value)
    finally:
        ctypes.windll.kernel32.LocalFree(text)  # type: ignore[attr-defined]


def current_user_sid() -> str:
    """The SID of the account this process runs as (its token's user)."""
    import ctypes
    from ctypes import wintypes

    advapi = _advapi()
    kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        raise OSError(ctypes.get_last_error(), "OpenProcessToken")  # type: ignore[attr-defined]
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation")  # type: ignore[attr-defined]
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]  # TOKEN_USER.User.Sid
        return _sid_text(advapi, ctypes.c_void_p(sid))
    finally:
        kernel.CloseHandle(token)


def _read_security(path: str) -> Security:
    """The owner SID and the DACL entries of ``path`` (GetNamedSecurityInfoW)."""
    import ctypes
    from ctypes import wintypes

    advapi = _advapi()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = advapi.GetNamedSecurityInfoW(
        ctypes.c_wchar_p(path), 1, 0x1 | 0x4,  # SE_FILE_OBJECT; OWNER | DACL
        ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor),
    )
    if status != 0:
        raise OSError(status, "GetNamedSecurityInfoW", path)
    try:
        owner_sid = _sid_text(advapi, owner)
        if not dacl.value:
            return Security(owner_sid, None)

        class AclHeader(ctypes.Structure):
            _fields_ = [("revision", ctypes.c_ubyte), ("sbz1", ctypes.c_ubyte), ("size", wintypes.WORD),
                        ("count", wintypes.WORD), ("sbz2", wintypes.WORD)]

        class AceHeader(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("size", wintypes.WORD), ("mask", wintypes.DWORD)]

        header = ctypes.cast(dacl, ctypes.POINTER(AclHeader)).contents
        aces: list[Ace] = []
        for index in range(header.count):
            pointer = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(pointer)):
                raise OSError(ctypes.get_last_error(), "GetAce", path)  # type: ignore[attr-defined]
            ace = ctypes.cast(pointer, ctypes.POINTER(AceHeader)).contents
            sid = ""
            if ace.type in (ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE):
                sid = _sid_text(advapi, ctypes.c_void_p((pointer.value or 0) + 8))  # SidStart after header + mask
            aces.append(Ace(int(ace.type), int(ace.flags), int(ace.mask), sid))
        return Security(owner_sid, aces)
    finally:
        ctypes.windll.kernel32.LocalFree(descriptor)  # type: ignore[attr-defined]


def make_owner_only(path: str) -> bool:
    """Give ``path`` the protected DACL :data:`OWNER_ONLY_SDDL` (SetFileSecurityW); False when that failed."""
    return _set_protected_dacl(path, OWNER_ONLY_SDDL)


def make_shared_parent(path: str) -> bool:
    """R23-6: give ``path`` (``%ProgramData%\\deepfake-lens``, just made) the protected :data:`SHARED_PARENT_SDDL`."""
    return _set_protected_dacl(path, SHARED_PARENT_SDDL)


def _set_protected_dacl(path: str, sddl: str) -> bool:
    import ctypes

    advapi = _advapi()
    descriptor = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        ctypes.c_wchar_p(sddl), 1, ctypes.byref(descriptor), None,
    ):
        return False
    try:
        # DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
        return bool(advapi.SetFileSecurityW(ctypes.c_wchar_p(path), 0x4 | 0x80000000, descriptor))
    finally:
        ctypes.windll.kernel32.LocalFree(descriptor)  # type: ignore[attr-defined]


def owner_only(path: str) -> bool:
    """R22-2: :func:`dacl_owner_only` of ``path`` for the current user; False when it cannot be read."""
    try:
        return dacl_owner_only(_read_security(path), current_user_sid())
    except (OSError, AttributeError, ValueError):
        logger.info("owner-only check of %s failed", path, exc_info=True)
        return False


def parent_safe(path: str) -> bool:
    """R22-2: :func:`dacl_parent_safe` of ``path`` (the shared parent folder); False when it cannot be read."""
    try:
        return dacl_parent_safe(_read_security(path), current_user_sid())
    except (OSError, AttributeError, ValueError):
        logger.info("parent check of %s failed", path, exc_info=True)
        return False


def user_folder_name() -> str:
    """The account's folder name under ``%ProgramData%\\deepfake-lens`` (USERNAME; never a path)."""
    name = os.environ.get("USERNAME", "") or "user"
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name).strip(".") or "user"
