"""Command line: `python -m simulator seed` and `python -m simulator wipe`."""

import argparse
import dataclasses
import sys

from shared.config import load_config, service_key
from simulator.client import Backend, SimulatorError, TimeoutSession, seed, wipe
from simulator.config import load_simulator_config
from simulator.credentials import write_credentials


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m simulator",
        description="Fill Attentio with a simulated classroom history through the backend API.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    seed_cmd = sub.add_parser("seed", help="wipe the simulated data, then generate and send a new cohort")
    seed_cmd.add_argument("--weeks", type=int, help="weeks of lectures ending today (default: config)")
    seed_cmd.add_argument("--seed", type=int, help="random seed (default: config)")
    seed_cmd.add_argument(
        "--small", action="store_true", help="a week and a few students per section, for quick looks"
    )
    for command in (seed_cmd, sub.add_parser("wipe", help="remove all simulated data")):
        command.add_argument(
            "--backend-url",
            help="backend address (default: ATTENTIO_BACKEND_URL, else backend.url in the config)",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    backend = Backend(TimeoutSession(), args.backend_url or load_config().backend_url, service_key())
    try:
        if args.command == "wipe":
            removed = wipe(backend)
            print("Removed simulated data: " + ", ".join(f"{n} {k}" for k, n in removed.items()))
            return 0
        config = load_simulator_config(small=args.small)
        if args.weeks is not None:
            config = dataclasses.replace(config, weeks=args.weeks)
        if args.seed is not None:
            config = dataclasses.replace(config, seed=args.seed)
        report = seed(backend, config, progress=print)
    except SimulatorError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(
        f"Done in {report.seconds:.0f} s: {report.students} students, "
        f"{report.lectures} lectures, {report.records} records, {report.tests} tests with {report.marks} marks"
    )
    path = write_credentials(report.accounts, report.demo_password, report.password_generated)
    print(f"Created {len(report.accounts)} simulated accounts. Emails are listed in {path}")
    if report.password_generated:
        print(f"Their password, shown only now and also saved in that file: {report.demo_password}")
    else:
        print("Their password is the value of ATTENTIO_DEMO_PASSWORD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
