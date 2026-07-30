"""Generate test X.509 certificate PEM files for live system testing.

Usage:
    python -m scripts.generate_test_certs
    python -m scripts.generate_test_certs --out /tmp/certs/
    python -m scripts.generate_test_certs --cn api.prod.test-domain.com --days 400

Regenerates tests/fixtures/certs/*.pem by default (safe to re-run).
"""
from __future__ import annotations

import argparse
import sys

from tests.fixtures.certs.generate import main as _gen_fixtures, make_cert_pem
import pathlib


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate test certificates for ASCRA.")
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory (default: tests/fixtures/certs/)",
    )
    parser.add_argument("--cn", default=None, help="Generate a single cert for this CN")
    parser.add_argument("--san", nargs="+", help="SANs for the single cert (default: [CN])")
    parser.add_argument("--days", type=int, default=400, help="Validity days (default: 400)")
    parser.add_argument("--expired", action="store_true", help="Generate an expired cert")
    args = parser.parse_args()

    if args.cn:
        out_dir = pathlib.Path(args.out) if args.out else pathlib.Path("tests/fixtures/certs")
        out_dir.mkdir(parents=True, exist_ok=True)
        san = args.san or [args.cn]
        pem = make_cert_pem(cn=args.cn, san=san, days_valid=args.days, expired=args.expired)
        slug = args.cn.replace(".", "-").replace("*", "wildcard")
        out_path = out_dir / f"cert_{slug}.pem"
        out_path.write_bytes(pem)
        print(f"Wrote {out_path}")
    else:
        if args.out:
            import pathlib as _pl
            import tests.fixtures.certs.generate as _gen
            _gen._HERE = _pl.Path(args.out)  # type: ignore[attr-defined]
        _gen_fixtures()


if __name__ == "__main__":
    main()
    sys.exit(0)
