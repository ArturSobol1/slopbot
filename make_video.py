"""SlopBot - turns a topic into a vertical explainer video.

Examples:
  python make_video.py explainer "why the sky is blue"
  python make_video.py dialogue "how vaccines work"
  python make_video.py explainer --topics-file topics.txt
  python make_video.py dialogue --script examples/dialogue.json --offline
  python make_video.py explainer --resume output/20261113-162159-...   (finish an interrupted run)
  python make_video.py explainer "why the sky is blue" --upload        (then upload to YouTube, private)
"""
import argparse
import sys
from pathlib import Path

from slopbot.pipeline import make_video


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # show progress live, even when piped to a file
    parser = argparse.ArgumentParser(description="Turn a topic into a vertical explainer video.")
    parser.add_argument("format", choices=["explainer", "dialogue"],
                        help="explainer = narrator over stock footage; dialogue = two characters over gameplay")
    parser.add_argument("topic", nargs="?", help="what the video should explain")
    parser.add_argument("--topics-file", help="text file with one topic per line (makes one video each)")
    parser.add_argument("--script", help="use an existing script .json instead of asking Claude")
    parser.add_argument("--offline", action="store_true",
                        help="test mode: Windows' built-in voice and plain backgrounds (no ElevenLabs or stock footage)")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="voice speed, 0.7-1.2 (default 1.0 = the voice's natural pace)")
    parser.add_argument("--no-music", action="store_true", help="skip background music")
    parser.add_argument("--music", metavar="FILE", help="use this music file instead of letting Claude pick a track")
    parser.add_argument("--visuals", choices=["animated", "footage"], default="animated",
                        help="explainer visuals: animated = Claude-designed 3D animations in Blender, with real "
                             "footage where it fits better (default); footage = real footage/photos/stock only")
    parser.add_argument("--resume", metavar="RUN_DIR",
                        help="finish an interrupted run (an output/... folder), reusing its script and shots")
    parser.add_argument("--upload", action="store_true",
                        help="upload each finished video to YouTube as a Short (see README; off by default)")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private",
                        help="privacy for --upload (default: private)")
    args = parser.parse_args()

    if args.script or args.resume:
        jobs = [None]
    elif args.topics_file:
        jobs = [t.strip() for t in Path(args.topics_file).read_text(encoding="utf-8").splitlines() if t.strip()]
    elif args.topic:
        jobs = [args.topic]
    else:
        parser.error("give a topic, --topics-file or --script")

    failed = 0
    for topic in jobs:
        try:
            final = make_video(args.format, topic=topic, script_path=args.script, offline=args.offline,
                               speed=args.speed, music=not args.no_music, visuals=args.visuals,
                               resume=args.resume, music_file=args.music)
        except Exception as exc:  # keep going through a topics file if one video fails
            failed += 1
            print(f"[error] {topic or args.script or args.resume}: {exc}", file=sys.stderr)
            if len(jobs) == 1:
                raise
            continue
        if args.upload:
            from slopbot.youtube import upload
            try:
                upload(final.parent, args.privacy)
            except (Exception, SystemExit) as exc:  # the video stays in its folder: retry with upload.py
                failed += 1
                print(f"[error] upload failed: {exc}\n"
                      f"        The video is safe in {final.parent}. Retry with:\n"
                      f"        .venv\\Scripts\\python upload.py \"{final.parent}\" --privacy {args.privacy}",
                      file=sys.stderr)
    if failed:
        print(f"{failed} of {len(jobs)} videos failed.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
