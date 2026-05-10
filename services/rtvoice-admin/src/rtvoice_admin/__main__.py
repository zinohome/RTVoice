"""rtvoice-admin CLI entry point."""
import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rtvoice-admin",
                                description="RTVoice multi-tenant admin CLI")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("version", help="show version")
    args = p.parse_args(argv)
    if args.cmd == "version":
        from rtvoice_admin import __version__
        print(__version__)
        return 0
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
