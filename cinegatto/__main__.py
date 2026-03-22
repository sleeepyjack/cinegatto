import argparse
import sys

from cinegatto.app import run


def main():
    parser = argparse.ArgumentParser(description="cinegatto — cinema for cats")
    parser.add_argument(
        "-c", "--config",
        help="Path to user config JSON file",
        default=None,
    )
    args = parser.parse_args()
    run(config_path=args.config)


if __name__ == "__main__":
    main()
