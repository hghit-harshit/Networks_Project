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



def main(net, load="low", err=0.0001, cont_type = ""):
    import time
    import numpy as np
    import random
    import threading

    # -----------------------------
    # PAPER PARAMETERS
    # -----------------------------

    # 400 elastic flows: S1=160, S2=80, S3=40, S4=20
    ELASTIC_COUNTS = {
        "s1": 40,
        "s2": 30,
        "s3": 20,
        "s4": 10
    }

    # 20 streaming flows (VoIP @ 80kbps)
    NUM_STREAMS = 20

    # Packet sizes
    ELASTIC_PACKET = 1000   # bytes
    STREAM_PACKET = 100     # bytes

    STREAM_RATE = "80K"     # 80 kbps

    SIM_DURATION = 300      # seconds

    # Pareto parameters for elastic flow size
    PARETO_ALPHA = 1.2
    PARETO_SCALE = 10000*1024    # mean file size scale (bytes)

    threads = []
    # -----------------------------
    # DISTRIBUTIONS
    # -----------------------------

    def pareto_bytes():
        """Flow size (paper does volume from Pareto)."""
        #return int(np.random.pareto(PARETO_ALPHA) * PARETO_SCALE) + ELASTIC_PACKET
        u = np.random.random()
        return int(PARETO_SCALE / (u ** (1 / PARETO_ALPHA))) 


    def exp_wait(lmbda):
        """Exponential wait time for inter-arrivals."""
        return np.random.exponential(lmbda)


# -----------------------------
# MAIN GENERATOR
# -----------------------------
    print("\n========== FAN PAPER TRAFFIC GENERATION ==========\n")

    # --------------------------
    # R2 → D2 WIRELESS ERROR RATE
    # --------------------------
    print(f"Setting wireless error rate on R2->D2 = {err}")
    r2 = net.get("r2")
    r2.cmd(f"tc qdisc replace dev r2-eth2 root netem loss {err*100}%")

    # --------------------------
    # CHOOSE EXPONENTIAL MEANS
    # --------------------------
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

    # --------------------------
    # DESTINATIONS
    # --------------------------
    D1 = ("10.0.10.10", 5001)
    D2 = ("10.0.20.10", 5001)

    # Start servers
    d1 = net.get("d1")
    d2 = net.get("d2")

    d1.cmd("iperf -s -p 5001 &")
    d2.cmd("iperf -s -p 5001 &")

    print("Servers running on D1:5001 and D2:5001")

    # --------------------------
    # ONE LONG-LIVED ELASTIC FLOW (S4 → D2)
    # --------------------------
    print("Starting long-lived elastic flow S4 -> D2")
    s4 = net.get("s4")
    s4.cmd(f"iperf -c {D2[0]} -p {D2[1]} -t {SIM_DURATION} -i 1 > long_s4_d2_{err}_{load}_{cont_type}.log 2>&1 &")

    # --------------------------
    # STREAMING (20 UDP @80kbps)
    # EXPO distributed start times
    # --------------------------
    print("Scheduling 20 streaming flows")

    for i in range(NUM_STREAMS):
        w = exp_wait(1.0)  # mean 1s start delay
        src = random.choice(["s1", "s2", "s3", "s4"])
        sender = net.get(src)

        print(f"[Stream {i}] {src} → D1 wait={w:.3f}s")
        time.sleep(w)

        sender.cmd(
            f"iperf -u -c {D1[0]} -p {D1[1]} -b {STREAM_RATE} "
            f"-l {STREAM_PACKET} -t {SIM_DURATION} &"
            #f"> stream_{i}.log 2>&1 &"
        )

    # --------------------------
    # ELASTIC FLOWS (TCP)
    # Each source has its own exponential inter-arrival distribution
    # Flow size determines duration: size / 100Mbps bottleneck
    # --------------------------

    print("Scheduling elastic flows (Pareto sizes + exponential starts)")

    
    #flow_offset = 0
    start_time = time.time()

    
    def run_source(src, count, net, INTER, start_time, SIM_DURATION, D1, flow_id_start):
        flow_id = flow_id_start

        for _ in range(count):
            if time.time() - start_time > SIM_DURATION:
                break

            wait = exp_wait(INTER[src])
            size_bytes = pareto_bytes()

            # dur = max(1, int(size_bytes / (12_500_000)))

            time.sleep(wait)

            sender = net.get(src)

            sender.cmd(
                f"iperf -c {D1[0]} -p {D1[1]} -n {size_bytes} &"
            )

            print(f"[Elastic {flow_id}] {src} → D1  started  size={size_bytes}")
            flow_id += 1
    
    
    
    flow_offset = 0

    for src, count in ELASTIC_COUNTS.items():
        t = threading.Thread(
            target=run_source,
            args=(src, count, net, INTER, start_time, SIM_DURATION, D1, flow_offset)
        )
        t.daemon = True
        threads.append(t)
        t.start()

        flow_offset += count
    

    print("\n========== ALL FLOWS SCHEDULED ==========\n")




