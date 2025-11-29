# FAN Per-User Fairness in Ryu + Mininet

This repo implements a Flow-Aware Networking (FAN) controller in Ryu with a new per-user fairness mechanism inspired by the paper:

Per User Fairness in Flow-Aware Networks — Domżał, Wójcik, Jajszczyk (IEEE, 2012)

It also provides Mininet experiment scripts to reproduce key simulation scenarios and generate graphs similar to the paper: efficiency vs wireless loss and fair_rate vs time.


## Building and Runings Experiments

To build run the test you first have to install the following dependencies:
(it is recommended to run this setup in a seperate vm)
- mininet 
- Ryu controller

Then to run first do the project directory and run this comman
```bash
ryu-manager fan_controller.py
```

after this open a new terminal and start mininet cli using the command:
```bash
sudo python3 topo.py
```
This will setup the topology and open a mininet cli, now inside the cli run the following commands
```bash
mininet > py exec(open('paper_flows_simulation_nolog.py').read())
mininet > py main(net,load="high", err=0.01,)
```
The logs of the expeirment will be over-written in fair_raw.log, controller.log and ramaf.log

Now run this experimet for each loss value, youll have to change the loss values in the topo.py code :
```python
net.addLink(r2, d2, cls=TCLink, bw=5, delay='5ms', loss=0.01)
```
Repeat experiment for loss
- 10
- 1
- 0.1
- 0.001

Perform all test for
- fan_controller
-  normal_controller

and for both 
- high load
- low load

This results in around **180 Minutes** of total experiment.
Additionally, the fair rate vs time graphs uses data collected over 15 minutes.

---
Once these runs are complete, you will have all the required logs to generate the graphs.