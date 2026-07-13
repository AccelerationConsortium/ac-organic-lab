"""``lab-skills`` command-line entry point.

Currently one subcommand tree: ``lab-skills mcp serve`` boots the
control-capable MCP server (:mod:`lab_skills.mcp`) over stdio. Kept
argparse-only (no extra deps) so the CLI imports even where the ``mcp``
package is absent — ``mcp serve`` imports it lazily when actually run.
"""

from __future__ import annotations

import argparse
from typing import Sequence


def _parse_binding(pairs: Sequence[str]) -> dict[str, str]:
    """Turn ``["sealer=plateloc", "reader=cytation_5"]`` into a dict.

    Raises ``argparse``-friendly ``ValueError`` on a malformed pair.
    """

    binding: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"--binding expects ROLE=ID, got {pair!r}")
        role, equipment_id = pair.split("=", 1)
        role, equipment_id = role.strip(), equipment_id.strip()
        if not role or not equipment_id:
            raise ValueError(f"--binding expects non-empty ROLE=ID, got {pair!r}")
        binding[role] = equipment_id
    return binding


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lab-skills", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    mcp = sub.add_parser("mcp", help="MCP server commands")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)

    serve = mcp_sub.add_parser(
        "serve", help="Run the lab-skills MCP server over stdio"
    )
    serve.add_argument(
        "--registry",
        default=None,
        help="Path to equipment.yaml (default: LAB_REGISTRY_PATH or the "
        "nearest equipment.yaml walking up from the package).",
    )
    serve.add_argument(
        "--binding",
        action="append",
        default=[],
        metavar="ROLE=ID",
        help="Bind a role to an equipment id (repeatable), e.g. "
        "--binding sealer=plateloc.",
    )
    serve.add_argument(
        "--owner",
        default="mcp:lab-skills",
        help="Claim owner stamped into details.claimed_by (default: mcp:lab-skills).",
    )
    serve.add_argument(
        "--allow-control",
        action="store_true",
        help="Register the actuating execute_plan tool. Without this the "
        "server is read + validate + dry-run only.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "mcp" and args.mcp_command == "serve":
        from .mcp import MCPConfig, serve
        from .registry import load_registry

        try:
            binding = _parse_binding(args.binding)
        except ValueError as exc:
            parser.error(str(exc))

        registry = load_registry(args.registry)
        serve(
            MCPConfig(
                registry=registry,
                binding=binding,
                owner=args.owner,
                allow_control=args.allow_control,
            )
        )
        return 0

    parser.error("unknown command")  # pragma: no cover - argparse requires a subcommand
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main"]
