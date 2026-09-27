"""Linux syscall allowlist installed inside the disposable reader before parsing."""

import ctypes
import importlib
import errno
import os
import platform

from canvas_mcp.domain.file_content import REMOTE_PARSER_MEMORY_BYTES


def lock_down(artifact_fd: int) -> None:
    resource = importlib.import_module("resource")

    resource.setrlimit(resource.RLIMIT_AS, (REMOTE_PARSER_MEMORY_BYTES,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # All imports and the one read-only artifact have been opened beforehand.
    # No open/openat, socket, connect, exec, clone, ptrace or cross-process reads.
    machine = platform.machine()
    allowed: tuple[int, ...]
    if machine == "x86_64":
        arch = 0xC000003E
        read, write = 0, 1
        allowed = (
            3,
            5,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
            15,
            16,
            24,
            28,
            32,
            33,
            35,
            39,
            60,
            63,
            72,
            96,
            102,
            104,
            107,
            108,
            158,
            186,
            202,
            228,
            231,
            262,
            318,
            334,
        )
    elif machine == "aarch64":
        arch = 0xC00000B7
        read, write = 63, 64
        allowed = (
            57,
            80,
            62,
            222,
            226,
            215,
            214,
            134,
            135,
            139,
            29,
            124,
            233,
            23,
            24,
            101,
            172,
            93,
            160,
            25,
            169,
            174,
            176,
            175,
            177,
            178,
            98,
            113,
            94,
            79,
            278,
            293,
        )
    else:
        raise RuntimeError()

    class Filter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    allow, deny = 0x7FFF0000, 0x00050000 | errno.EPERM
    rules = [(0x20, 0, 0, 4), (0x15, 1, 0, arch), (0x06, 0, 0, 0x80000000), (0x20, 0, 0, 0)]
    # read may access only the explicitly inherited read-only artifact FD.
    rules.extend(
        [
            (0x15, 0, 4, read),
            (0x20, 0, 0, 16),
            (0x15, 0, 1, artifact_fd),
            (0x06, 0, 0, allow),
            (0x06, 0, 0, deny),
            (0x20, 0, 0, 0),
        ]
    )
    # stdout is the bounded JSON result; stderr is redirected to /dev/null.
    rules.extend(
        [
            (0x15, 0, 5, write),
            (0x20, 0, 0, 16),
            (0x15, 1, 0, 1),
            (0x15, 0, 1, 2),
            (0x06, 0, 0, allow),
            (0x06, 0, 0, deny),
            (0x20, 0, 0, 0),
        ]
    )
    for syscall in allowed:
        rules.extend([(0x15, 0, 1, syscall), (0x06, 0, 0, allow)])
    rules.append((0x06, 0, 0, deny))
    filters = (Filter * len(rules))(*(Filter(*row) for row in rules))
    program = Program(len(rules), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(22, 2, ctypes.byref(program), 0, 0):
        raise OSError()
    # Prevent a privileged future exec from undoing this irreversible filter.
    os.close(0)
