#!/usr/bin/env python3
import time
import subprocess
import numpy as np
import random


ALPHA = 1.5               # Pareto shape
SCALE = 0.8               # Minimum inter-arrival time
NUM_FLOWS = 60            # Total flows
FLOW_DURATION = 8         # iperf flow duration (seconds)

SENDERS = ["s1", "s2", "s3", "s4"]  # available sources
RECEIVERS = [
    ("10.0.10.10", 5001),  # d1 (fast)
    ("10.0.20.10", 5001)   # d2 (wireless slow link)
]

USE_UDP = False   # True = UDP flows, False = TCP flows



def pareto_wait():
    """Return Pareto-distributed wait time."""
    return np.random.pareto(ALPHA) * SCALE

def choose_sender():
    """Pick a random sender host."""
    return random.choice(SENDERS)

def choose_receiver():
    """Pick a random receiver (d1 or d2)."""
    return random.choice(RECEIVERS)


def main():
    print("=== Starting Pareto Traffic Generator ===")

    for idx in range(NUM_FLOWS):

        wait = pareto_wait()
        print(f"[Flow {idx}] waiting {wait:.3f}s")
        time.sleep(wait)

        sender = choose_sender()
        dst_ip, dst_port = choose_receiver()

        proto = "UDP" if USE_UDP else "TCP"
        print(f"[Flow {idx}] {sender} -> {dst_ip}:{dst_port}  [{proto}]")

        if USE_UDP:
            cmd = (
                f"iperf -c {dst_ip} -p {dst_port} -u "
                f"-b 20M -t {FLOW_DURATION} > flow_{idx}.log 2>&1"
            )
        else:
            cmd = (
                f"iperf -c {dst_ip} -p {dst_port} "
                f"-t {FLOW_DURATION} > flow_{idx}.log 2>&1"
            )

        # execute iperf from sender namespace
        subprocess.Popen(
            f"mnexec -a $(pgrep -f {sender}) {cmd}",
            shell=True
        )

    print("=== All flows started ===")


if __name__ == "__main__":
    main()
