import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .snapshot import verify_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(prog="market-data")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="verify a pinned FINAL GATE snapshot")
    verify.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    identity = verify_snapshot(args.run_dir)
    print(json.dumps(asdict(identity), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

