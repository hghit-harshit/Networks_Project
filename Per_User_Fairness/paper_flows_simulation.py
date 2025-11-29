#!/usr/bin/env python3
"""
FAN Paper Traffic Generator
Run inside Mininet CLI:

    py fan_paper_flows.main(net, load="low", err=0.001)

Options:
    load="low"  -> low-loaded FAN link (paper case 1)
    load="high" -> high-loaded FAN link (paper case 2)

err = wireless packet error rate on R2->D2 link (0.0001 to 0.1)
"""



def main(net, load="low", err=0.0001):
    import time
    import numpy as np
    import random

    

    # 400 elastic flows: S1=160, S2=80, S3=40, S4=20
    ELASTIC_COUNTS = {
        "s1": 160,
        "s2": 80,
        "s3": 40,
        "s4": 20
    }

    # 20 streaming flows (VoIP @ 80kbps)
    NUM_STREAMS = 20

    # Packet sizes
    ELASTIC_PACKET = 1000   # bytes
    STREAM_PACKET = 100     # bytes

    STREAM_RATE = "80K"     # 80 kbps

    SIM_DURATION = 300      # seconds

    # Pareto parameters for elastic flow size
    PARETO_ALPHA = 1.5
    PARETO_SCALE = 10000    # mean file size scale (bytes)


    

    def pareto_bytes():
        """Flow size (paper does volume from Pareto)."""
        return int(np.random.pareto(PARETO_ALPHA) * PARETO_SCALE) + ELASTIC_PACKET


    def exp_wait(lmbda):
        """Exponential wait time for inter-arrivals."""
        return np.random.exponential(lmbda)



    print("\n========== FAN PAPER TRAFFIC GENERATION ==========\n")

    
    print(f"Setting wireless error rate on R2->D2 = {err}")
    r2 = net.get("r2")
    r2.cmd(f"tc qdisc replace dev r2-eth2 root netem loss {err*100}%")

    if load == "low":
        inter_s1 = 0.4
        inter_s2 = 0.3
        inter_s3 = 0.2
        inter_s4 = 0.1
    else:
        inter_s1 = 0.025
        inter_s2 = 0.05
        inter_s3 = 0.075
        inter_s4 = 0.1

    INTER = {
        "s1": inter_s1,
        "s2": inter_s2,
        "s3": inter_s3,
        "s4": inter_s4
    }

    D1 = ("10.0.10.10", 5001)
    D2 = ("10.0.20.10", 5001)

    # Start servers
    d1 = net.get("d1")
    d2 = net.get("d2")

    d1.cmd("iperf -s -p 5001 &")
    d2.cmd("iperf -s -p 5001 &")

    print("Servers running on D1:5001 and D2:5001")

   
    print("Starting long-lived elastic flow S4 -> D2")
    s4 = net.get("s4")
    s4.cmd(f"iperf -c {D2[0]} -p {D2[1]} -t {SIM_DURATION} > long_s4_d2.log 2>&1 &")

    
    print("Scheduling 20 streaming flows")

    for i in range(NUM_STREAMS):
        w = exp_wait(1.0)  # mean 1s start delay
        src = random.choice(["s1", "s2", "s3", "s4"])
        sender = net.get(src)

        print(f"[Stream {i}] {src} → D1 wait={w:.3f}s")
        time.sleep(w)

        sender.cmd(
            f"iperf -u -c {D1[0]} -p {D1[1]} -b {STREAM_RATE} "
            f"-l {STREAM_PACKET} -t {SIM_DURATION} "
            f"> stream_{i}.log 2>&1 &"
        )

    

    print("Scheduling elastic flows (Pareto sizes + exponential starts)")

    flow_id = 0
    start_time = time.time()

    # For each sender S1..S4
    for src, count in ELASTIC_COUNTS.items():

        for _ in range(count):
            if time.time() - start_time > SIM_DURATION:
                print("Simulation time done. Stopping flow creation.")
                break

            wait = exp_wait(INTER[src])
            size_bytes = pareto_bytes()

            dur = max(1, int(size_bytes / (12_500_000)))  # 100 Mbps bottleneck = 12.5MB/s

            sender = net.get(src)

            print(f"[Elastic {flow_id}] {src} → D1 wait={wait:.3f}s size={size_bytes/1e6:.2f}MB dur≈{dur}s")
            time.sleep(wait)

            sender.cmd(
                f"iperf -c {D1[0]} -p {D1[1]} -n {size_bytes} "
                f"> elastic_{flow_id}.log 2>&1 &"
            )

            flow_id += 1

    print("\n========== ALL FLOWS SCHEDULED ==========\n")
