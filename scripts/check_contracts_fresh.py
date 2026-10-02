#!/usr/bin/env python3
"""Wire-compatibility guard for the gRPC contracts (ADR-0020, #128).

Regenerates the stubs from ``contracts/`` into a scratch directory and
compares the serialized FileDescriptorProtos with the committed ones in
``src/kingdoms/rpc_generated``. Fails closed when they diverge: a
committed stub that no longer matches its ``.proto`` is exactly the
wire-compat drift this check exists to catch (field renumbering, type
changes, removals that would break cross-process RPC at deploy time).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONTRACTS = REPO / "contracts"
COMMITTED = REPO / "src" / "kingdoms" / "rpc_generated" / "kingdoms" / "v1"


def generate(out_dir: Path) -> None:
    """Run protoc into ``out_dir``, mirroring ``make contracts``."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"--python_out={out_dir}",
            f"--grpc_python_out={out_dir}",
            f"--proto_path={CONTRACTS}",
            *map(str, (CONTRACTS / "kingdoms" / "v1").glob("*.proto")),
        ],
        check=True,
    )


def descriptors(paths: list[Path], root: Path) -> list[tuple[str, bytes]]:
    """Import each generated module and return its serialized descriptor."""
    import importlib.util

    out: list[tuple[str, bytes]] = []
    for pb2 in sorted(paths):
        spec = importlib.util.spec_from_file_location("_pb2_" + pb2.stem, pb2)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        out.append((pb2.name, module.DESCRIPTOR.serialized_pb))
    return out


def main() -> int:
    """Exit 1 when committed stubs drift from the contracts."""
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        generate(out_dir)
        fresh = descriptors(sorted(out_dir.glob("kingdoms/v1/*_pb2.py")), out_dir)
    committed = descriptors(sorted(COMMITTED.glob("*_pb2.py")), COMMITTED)
    if fresh == committed:
        print("CONTRACTS FRESH: committed stubs match contracts/")
        return 0
    print(
        "::error::Wire-compat drift: committed stubs in "
        "src/kingdoms/rpc_generated no longer match contracts/ (kingdoms.v1). "
        "Run 'make contracts' and commit the regenerated stubs."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
