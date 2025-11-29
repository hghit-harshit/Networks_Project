#!/usr/bin/env python3


def main(net):
    
    import time
    import numpy as np
    import random
    #net = globals()['net']   
    ALPHA = 1.5             # Pareto shape
    SCALE = 0.8             # Minimum wait
    NUM_FLOWS = 60          # Number of flows
    FLOW_DURATION = 100       # iperf duration
    USE_UDP = False         # TCP by default

    SENDERS = ["s1", "s2", "s3", "s4"]
    RECEIVERS = [
        ("10.0.10.10", 5001),  # d1
        ("10.0.20.10", 5001)   # d2
    ]
    
    def pareto_wait():
        """Return Pareto-distributed wait time."""
        return np.random.pareto(ALPHA) * SCALE


    def choose_sender():
        return random.choice(SENDERS)


    def choose_receiver():
        return random.choice(RECEIVERS)


    print("\n=== Pareto Traffic Generator Started ===\n", flush=True)

    for idx in range(NUM_FLOWS):

        wait_time = pareto_wait()
        print(f"[Flow {idx}] Waiting {wait_time:.3f}s", flush=True)
        time.sleep(wait_time)

        sender_name = choose_sender()
        dst_ip, dst_port = choose_receiver()

        sender = net.get(sender_name)

        proto = "UDP" if USE_UDP else "TCP"
        print(f"[Flow {idx}] {sender_name} -> {dst_ip}:{dst_port} [{proto}]", flush=True)

        if USE_UDP:
            cmd = (
                f"iperf -c {dst_ip} -p {dst_port} -u "
                f"-b 20M -t {FLOW_DURATION} > flow_{idx}.log 2>&1 &"
            )
        else:
            cmd = (
                f"iperf -c {dst_ip} -p {dst_port} "
                f"-t {FLOW_DURATION} > flow_{idx}.log 2>&1 &"
            )

        sender.cmd(cmd)

    print("\n=== All flows started ===\n", flush=True)



if __name__ == "__main__":
    print("This script is intended to be run inside Mininet CLI using 'py pareto_flow.py' or imported as a module.")
    main()
