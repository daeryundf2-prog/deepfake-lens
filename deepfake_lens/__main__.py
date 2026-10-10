import sys

try:
    from .cli import main
except KeyboardInterrupt:  # R16-13: Ctrl-C while the package is still importing — no traceback either
    print("중단됨(사용자 요청)", file=sys.stderr)
    raise SystemExit(130) from None


if __name__ == "__main__":
    raise SystemExit(main())
