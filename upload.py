"""Upload a finished SlopBot run to YouTube as a Short.

Examples:
  python upload.py output\\20261113-t34-places --dry-run             (show what would be sent)
  python upload.py output\\20261113-t34-places                       (upload as private)
  python upload.py output\\20261113-t34-places --privacy unlisted
  python upload.py output\\20261113-t34-places --describe-only       (just write youtube.json)
"""
import argparse
import sys
from pathlib import Path

from slopbot import describe
from slopbot.youtube import PRIVACY, upload


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True, errors="replace")
    parser = argparse.ArgumentParser(description="Upload a finished run (an output\\... folder) to YouTube.")
    parser.add_argument("run_dir", nargs="+", help="one or more output\\... folders containing final.mp4")
    parser.add_argument("--privacy", choices=PRIVACY, default="private", help="default: private")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the title/description/tags it would send, without signing in or uploading")
    parser.add_argument("--describe-only", action="store_true",
                        help="only (re)write youtube.json and upload_info.txt with Claude's description")
    parser.add_argument("--redescribe", action="store_true",
                        help="ask Claude for a new description even if youtube.json already exists")
    parser.add_argument("--force", action="store_true", help="upload again even if this run was uploaded before")
    args = parser.parse_args()

    failed = 0
    for run_dir in args.run_dir:
        run_dir = Path(run_dir)
        try:
            if args.describe_only:
                meta = describe.load_or_write(run_dir, redo=True)
                print(f"\n{meta['title']}\n\n{meta['full_description']}\n")
            else:
                upload(run_dir, args.privacy, dry_run=args.dry_run, force=args.force,
                       redescribe=args.redescribe)
        except (Exception, SystemExit) as exc:
            failed += 1
            print(f"[error] {run_dir}: {exc}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
