"""Small Win32 storage primitive layer. No shell, content parser or remote paths.

Directory handles omit FILE_SHARE_DELETE, pinning every ancestor against rename
or replacement while in use. Reparse points are opened themselves and rejected.
Files are created exclusively, renamed/deleted by handle, and checked by identity.
Unsupported platforms fail closed; this adapter is replaceable behind storage.
"""

import ctypes
import os
import re
from ctypes import wintypes as w
from pathlib import Path
from typing import Any

from canvas_mcp.domain.errors import StorageError, UnsupportedCapabilityError


class SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", w.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", w.BOOL)]


class FileInfo(ctypes.Structure):
    _fields_ = [
        ("attributes", w.DWORD),
        ("created", w.FILETIME),
        ("accessed", w.FILETIME),
        ("written", w.FILETIME),
        ("volume", w.DWORD),
        ("size_high", w.DWORD),
        ("size_low", w.DWORD),
        ("links", w.DWORD),
        ("index_high", w.DWORD),
        ("index_low", w.DWORD),
    ]


class RenameInfo(ctypes.Structure):
    _fields_ = [
        ("replace", w.BOOLEAN),
        ("root", w.HANDLE),
        ("length", w.DWORD),
        ("name", w.WCHAR * 1),
    ]


def check(ok: object) -> None:
    if not ok:
        raise StorageError()


def bind(library: Any, name: str, args: list[Any], result: Any) -> Any:
    function = getattr(library, name)
    function.argtypes, function.restype = args, result
    return function


class WindowsFS:
    def __init__(self) -> None:
        if os.name != "nt":
            raise UnsupportedCapabilityError()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        security = ctypes.WinDLL("advapi32", use_last_error=True)
        p = ctypes.c_void_p
        self.close_handle = bind(kernel, "CloseHandle", [w.HANDLE], w.BOOL)
        self._create = bind(
            kernel,
            "CreateFileW",
            [w.LPCWSTR, w.DWORD, w.DWORD, p, w.DWORD, w.DWORD, w.HANDLE],
            w.HANDLE,
        )
        self._mkdir = bind(kernel, "CreateDirectoryW", [w.LPCWSTR, p], w.BOOL)
        self._rmdir = bind(kernel, "RemoveDirectoryW", [w.LPCWSTR], w.BOOL)
        self._info = bind(kernel, "GetFileInformationByHandle", [w.HANDLE, p], w.BOOL)
        self._set = bind(
            kernel, "SetFileInformationByHandle", [w.HANDLE, ctypes.c_int, p, w.DWORD], w.BOOL
        )
        self._read = bind(kernel, "ReadFile", [w.HANDLE, p, w.DWORD, p, p], w.BOOL)
        self._write = bind(kernel, "WriteFile", [w.HANDLE, p, w.DWORD, p, p], w.BOOL)
        self._flush = bind(kernel, "FlushFileBuffers", [w.HANDLE], w.BOOL)
        self._seek = bind(
            kernel, "SetFilePointerEx", [w.HANDLE, ctypes.c_longlong, p, w.DWORD], w.BOOL
        )
        self._final = bind(
            kernel, "GetFinalPathNameByHandleW", [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD], w.DWORD
        )
        self._free = bind(kernel, "LocalFree", [p], p)
        to_sd = bind(
            security,
            "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            [w.LPCWSTR, w.DWORD, p, p],
            w.BOOL,
        )
        self._get_security = bind(
            security, "GetSecurityInfo", [w.HANDLE, ctypes.c_int, w.DWORD, p, p, p, p, p], w.DWORD
        )
        self._to_sddl = bind(
            security,
            "ConvertSecurityDescriptorToStringSecurityDescriptorW",
            [p, w.DWORD, w.DWORD, p, p],
            w.BOOL,
        )
        current = bind(kernel, "GetCurrentProcess", [], w.HANDLE)
        open_token = bind(security, "OpenProcessToken", [w.HANDLE, w.DWORD, p], w.BOOL)
        token_info = bind(
            security, "GetTokenInformation", [w.HANDLE, ctypes.c_int, p, w.DWORD, p], w.BOOL
        )
        sid_string = bind(security, "ConvertSidToStringSidW", [p, p], w.BOOL)
        token, size = w.HANDLE(), w.DWORD()
        check(open_token(current(), 8, ctypes.byref(token)))
        try:
            token_info(token, 1, None, 0, ctypes.byref(size))
            check(0 < size.value <= 65536)
            buffer = ctypes.create_string_buffer(size.value)
            check(token_info(token, 1, buffer, size.value, ctypes.byref(size)))
            sid = ctypes.cast(buffer, ctypes.POINTER(p))[0]
            string = w.LPWSTR()
            check(sid_string(sid, ctypes.byref(string)))
            try:
                self.owner = string.value
            finally:
                self._free(string)
        finally:
            self.close_handle(token)
        check(isinstance(self.owner, str) and self.owner.startswith("S-1-"))
        self._descriptor = p()
        sddl = f"O:{self.owner}D:P(A;OICI;FA;;;{self.owner})(A;OICI;FA;;;SY)"
        check(to_sd(sddl, 1, ctypes.byref(self._descriptor), None))
        self._attributes = SecurityAttributes(
            ctypes.sizeof(SecurityAttributes), self._descriptor, False
        )

    def close(self) -> None:
        if self._descriptor:
            self._free(self._descriptor)
            self._descriptor = ctypes.c_void_p()

    def mkdir(self, path: Path) -> None:
        check(self._mkdir(str(path), ctypes.byref(self._attributes)))

    def rmdir(self, path: Path) -> None:
        check(self._rmdir(str(path)))

    def open_directory(self, path: Path) -> int:
        # Deny write-capable opens (including reparse mutation), delete and rename.
        handle = self._create(str(path), 0x20080, 1, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise StorageError()
        try:
            info = self.info(handle)
            check(info.attributes & 0x10 and not info.attributes & 0x400)
            return int(handle)
        except BaseException:
            self.close_handle(handle)
            raise

    def info(self, handle: int) -> FileInfo:
        result = FileInfo()
        check(self._info(handle, ctypes.byref(result)))
        return result

    def identity(self, handle: int) -> tuple[int, int, int]:
        info = self.info(handle)
        return info.volume, info.index_high, info.index_low

    def final_path(self, handle: int) -> Path:
        buffer = ctypes.create_unicode_buffer(32768)
        size = self._final(handle, buffer, len(buffer), 0)
        check(0 < size < len(buffer))
        value = buffer.value
        check(value.startswith("\\\\?\\") and not value.startswith("\\\\?\\UNC\\"))
        return Path(value[4:])

    def private(self, handle: int) -> None:
        descriptor, text = ctypes.c_void_p(), w.LPWSTR()
        check(
            self._get_security(handle, 1, 5, None, None, None, None, ctypes.byref(descriptor)) == 0
        )
        try:
            check(self._to_sddl(descriptor, 1, 5, ctypes.byref(text), None))
            value = text.value or ""
            # Only owner and SYSTEM full-control allow ACEs, protected DACL.
            prefix, separator, acl = value.partition("D:")
            check(
                prefix == f"O:{self.owner}" and separator and acl.split("(", 1)[0] in ("P", "PAI")
            )
            aces = re.findall(r"\(([^()]+)\)", acl)
            check(len(aces) == 2)
            trustees = set()
            for ace in aces:
                parts = ace.split(";")
                check(
                    len(parts) == 6
                    and parts[0] == "A"
                    and parts[1] in ("OICI", "")
                    and parts[2:5] == ["FA", "", ""]
                )
                trustees.add(parts[5])
            check(trustees == {self.owner, "SY"})
        finally:
            if text:
                self._free(text)
            self._free(descriptor)

    def open_file(
        self,
        path: Path,
        *,
        create: bool = False,
        write: bool = False,
        delete: bool = False,
        lease: bool = False,
    ) -> int:
        access = 0x80020000 | (0x40000000 if write else 0) | (0x10000 if delete or lease else 0)
        flags = 0x00200000 | (0x04000000 if lease else 0)
        handle = self._create(
            str(path),
            access,
            0 if write or delete or lease else 1,
            ctypes.byref(self._attributes) if create else None,
            1 if create else 3,
            flags,
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise StorageError()
        try:
            info = self.info(handle)
            check(not info.attributes & (0x10 | 0x400) and info.links == 1)
            self.private(handle)
            return int(handle)
        except BaseException:
            self.close_handle(handle)
            raise

    def write(self, handle: int, data: bytes) -> None:
        check(len(data) <= 65536)
        written = w.DWORD()
        check(self._write(handle, data, len(data), ctypes.byref(written), None))
        check(written.value == len(data))

    def read(self, handle: int, size: int = 65536) -> bytes:
        check(0 <= size <= 65536)
        buffer, read = ctypes.create_string_buffer(size), w.DWORD()
        check(self._read(handle, buffer, size, ctypes.byref(read), None))
        return buffer.raw[: read.value]

    def rewind(self, handle: int) -> None:
        check(self._seek(handle, 0, None, 0))

    def seek(self, handle: int, offset: int) -> None:
        check(0 <= offset <= 262_144_000)
        check(self._seek(handle, offset, None, 0))

    def flush(self, handle: int) -> None:
        check(self._flush(handle))

    def delete(self, handle: int) -> None:
        value = w.BOOLEAN(True)
        check(self._set(handle, 4, ctypes.byref(value), ctypes.sizeof(value)))

    def rename(self, handle: int, path: Path, *, replace: bool = False) -> None:
        # Win32 FileRenameInfo needs an absolute, NUL-terminated target buffer.
        # Callers pin every parent. Artifacts never replace; the dedicated MIME
        # registry alone explicitly requests atomic state-file replacement.
        encoded = str(path).encode("utf-16-le")
        size = RenameInfo.name.offset + len(encoded)
        buffer = ctypes.create_string_buffer(max(size + 2, ctypes.sizeof(RenameInfo)))
        info = RenameInfo.from_buffer(buffer)
        check(type(replace) is bool)
        info.replace, info.root, info.length = replace, None, len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.name.offset, encoded, len(encoded))
        check(self._set(handle, 3, buffer, len(buffer)))
