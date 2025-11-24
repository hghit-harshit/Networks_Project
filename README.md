# FAN Per-User Fairness in Ryu + Mininet

This repo implements a Flow-Aware Networking (FAN) controller in Ryu with a new per-user fairness mechanism inspired by the paper:

Per User Fairness in Flow-Aware Networks — Domżał, Wójcik, Jajszczyk (IEEE, 2012)

It also provides Mininet experiment scripts to reproduce key simulation scenarios and generate graphs similar to the paper: efficiency vs wireless loss and waiting times for streaming flows, comparing a basic FAN admission vs per-user fairness with RAMAF.

Note: Reproducing the TCP NewJersey results requires kernel changes not available by default. This repo focuses on TCP NewReno and provides an ECN-based approximation option for the “NewJersey” curves. See limitations below.

## Components
- `ryu_app/fair_fan.py`: Ryu controller implementing:
  - Measurement-Based Admission Control (MBAC)
  - New fair-rate estimator (bytes per elastic flow) with smoothing
  - Priority load estimation
  - Per-user fairness limiter (max new flows per source per interval)
  - RAMAF (remove most active flows) mechanism
  - Learning-switch forwarding, optional OVS QoS queues for priority traffic
- `mininet/topo_fig2.py`: Minimal FAN link topology (Fig. 2 analogue)
- `mininet/topo_fig4.py`: FAN with a constrained “wireless” link (Fig. 4 analogue)
- `experiments/run_experiments.py`: Automates experiments: launches Ryu, starts Mininet, generates elastic + streaming flows, sweeps wireless loss and load levels, collects CSVs
- `experiments/plot_results.py`: Generates efficiency and waiting time graphs from CSVs

## Quick Start (Linux/WSL2 Ubuntu recommended)

1) Install dependencies

- System packages (Ubuntu):
```
sudo apt update
sudo apt install -y python3-pip python3-ryu mininet openvswitch-switch iproute2 iperf3 tcpdump
```
- Python packages:
```
pip3 install -r requirements.txt
```

2) Start the controller (terminal 1)
```
ryu-manager ryu_app/fair_fan.py --ofp-tcp-listen-port 6653 \
  --fair.enable=true --fair.per_interval_limit=1 --fair.alpha=0.1 \
  --fair.min_fair_rate_frac=0.05 --fair.max_priority_load_frac=0.7 \
  --ramaf.enable=true --ramaf.interval=0.5 --ramaf.max_remove=10
```

3) Run experiments (terminal 2)

- Figure 4 analogue and efficiency plots (NewReno):
```
python3 experiments/run_experiments.py --scenario fig4 --controller 127.0.0.1:6653 \
  --loss_sweep 0.0001 0.001 0.01 0.1 --duration 300 \
  --load low high --mode basic fair
```
- Generate graphs:
```
python3 experiments/plot_results.py --input data/fig4 --output plots
```

Outputs are written under `data/` and `plots/`.

## Windows notes
- Use WSL2 Ubuntu for Mininet/Ryu. On Windows PowerShell:
```
wsl --install -d Ubuntu
wsl
sudo apt update; sudo apt install -y mininet ryu-tool iperf3 python3-pip
pip3 install -r /mnt/d/shared_folder/requirements.txt
```
- Run commands from within WSL where OVS/Mininet are available. Paths under Windows are mounted at `/mnt/d/shared_folder`.

## Limitations / Differences
- PFQ vs PDRR: This controller uses OVS with two queues (priority vs elastic) and learning-switch forwarding, approximating priority scheduling. Full PFQ/PDRR packet schedulers aren’t reimplemented in software; we emulate priority via OVS QoS.
- TCP NewJersey: Not available in standard Linux. We provide an optional ECN-based approximation. Graphs labeled “NewJersey” can be generated in “ecn-approx” mode and are identified as such in legends.
- Wireless link: We emulate via Mininet TCLink with `bw=5` and `netem loss` for specified loss rates.

## Repo Structure
- `ryu_app/` — Ryu controller
- `mininet/` — Topology scripts
- `experiments/` — Automation + plotting
- `data/` — Collected CSVs
- `plots/` — Generated figures

## Next Steps
- Add queue occupancy feedback for RAMAF K estimation
- Integrate Mininet-WiFi when available
- Extend to per-port fair-rate accounting and multi-bottleneck paths

## Citation
If you use this work, please cite the original paper and reference this implementation.