#!/usr/bin/env python3
"""
FAN Topology for Mininet
Implements the network topology from "Per User Fairness in Flow-Aware Networks" (IEEE ICC 2012)

Topology (Figure 4 from paper):
   s1 (10.0.1.10/24) ---\
   s2 (10.0.2.10/24) ----\
   s3 (10.0.3.10/24) ----- R1 (100 Mbps) ----- R2 ----- d1 (10.0.10.10/24)
   s4 (10.0.4.10/24) ----/                            \
                                                       \-- d2 (10.0.20.10/24) [5 Mbps, lossy]

Link capacities:
 - s1-s4 to R1: 1000 Mbps
 - R1 to R2 (L2): 100 Mbps (bottleneck, with CAKE / per-flow scheduling)
 - R2 to d1 (L3): 1000 Mbps
 - R2 to d2 (L4): 5 Mbps with 0.01% loss (wireless)

Usage:
  sudo python3 fan_topo.py
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink
import time
import random
import os


def setup_qdisc(net):
    """
    Setup per-flow/per-user QoS on the bottleneck link using Linux qdisc.

    We install:
      - CAKE (Common Applications Kept Enhanced) on R1->R2 interface, with:
          * bandwidth 100 Mbit (bottleneck rate)
          * per-flow queueing
          * per-host fairness (dual-srchost)
          * basic DiffServ (diffserv3)
      - If CAKE is not available, we fall back to fq_codel on that interface.
    """
    info('*** Setting up qdisc (CAKE/fq_codel) on bottleneck link\n')

    r1 = net.get('r1')
    r2 = net.get('r2')

    # Find the R1-R2 interface on R1 robustly
    r1_r2_intf = None

    for intf in r1.intfList():
        # Skip lo / None links
        if not hasattr(intf, 'link') or intf.link is None:
            continue

        link = intf.link
        # link.intf1 and link.intf2 are the two ends of the link
        other = link.intf1 if link.intf2 is intf else link.intf2

        if other.node is r2:
            r1_r2_intf = intf.name
            break

    if not r1_r2_intf:
        info('*** ERROR: Could not find R1-R2 interface on r1\n')
        return

    info(f'Bottleneck interface (R1->R2) detected as: {r1_r2_intf}\n')

    # First, try to load CAKE module (if needed)
    r1.cmd('modprobe sch_cake 2>/dev/null || true')

    # Install CAKE on bottleneck at 100 Mbps, with per-flow queueing and per-host fairness:
    info(f'*** Installing CAKE on {r1_r2_intf} (100 Mbps) with flows + diffserv3\n')
    out = r1.cmd(
        f'tc qdisc replace dev {r1_r2_intf} '
        f'root cake bandwidth 100mbit diffserv3 flows dual-srchost 2>&1'
    )

    if ('Unknown qdisc' in out or
        'No such file' in out or
        'Illegal' in out):
        # CAKE not available, fall back to fq_codel
        info('*** CAKE not available, falling back to fq_codel on bottleneck\n')
        out_fq = r1.cmd(
            f'tc qdisc replace dev {r1_r2_intf} root fq_codel 2>&1'
        )
        info(out_fq)
    else:
        info(out)

    # Show qdisc status for debugging
    info(r1.cmd(f'tc -s qdisc show dev {r1_r2_intf}\n'))
    info('*** qdisc setup complete\n')


def setup_routing(net):
    """
    L3 Routing: Add default gateway routes for all hosts
    to allow inter-subnet communication via R1/R2.
    """
    info('*** Setting up L3 routing (default gateways)\n')

    # Source hosts
    net.get('s1').cmd('ip route add default via 10.0.1.1')
    net.get('s2').cmd('ip route add default via 10.0.2.1')
    net.get('s3').cmd('ip route add default via 10.0.3.1')
    net.get('s4').cmd('ip route add default via 10.0.4.1')

    # Destination hosts
    net.get('d1').cmd('ip route add default via 10.0.10.1')
    net.get('d2').cmd('ip route add default via 10.0.20.1')

    info('*** Routing setup complete\n')

    # Disable offloading for accurate measurements
    info('*** Disabling TCP offloading on all hosts\n')
    for host in net.hosts:
        host.cmd(
            'ethtool -K %s-eth0 tx off rx off tso off gso off gro off lro off '
            '2>/dev/null || true' % host.name
        )


def run_topology():
    """
    Create and run the FAN topology
    """
    setLogLevel('info')

    # Create network
    net = Mininet(
        controller=RemoteController,
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=False
    )

    info('*** Adding controller\n')
    c0 = net.addController(
        'c0',
        controller=RemoteController,
        ip='127.0.0.1',
        port=6633
    )

    info('*** Adding switches (routers)\n')
    r1 = net.addSwitch('r1', protocols='OpenFlow13')
    r2 = net.addSwitch('r2', protocols='OpenFlow13')

    info('*** Adding source hosts\n')
    s1 = net.addHost('s1', ip='10.0.1.10/24', mac='00:00:00:00:01:01')
    s2 = net.addHost('s2', ip='10.0.2.10/24', mac='00:00:00:00:02:01')
    s3 = net.addHost('s3', ip='10.0.3.10/24', mac='00:00:00:00:03:01')
    s4 = net.addHost('s4', ip='10.0.4.10/24', mac='00:00:00:00:04:01')

    info('*** Adding destination hosts\n')
    d1 = net.addHost('d1', ip='10.0.10.10/24', mac='00:00:00:00:10:01')
    d2 = net.addHost('d2', ip='10.0.20.10/24', mac='00:00:00:00:20:01')

    info('*** Creating links\n')
    # Source hosts to R1 (1000 Mbps)
    net.addLink(s1, r1, cls=TCLink, bw=1000, delay='1ms')
    net.addLink(s2, r1, cls=TCLink, bw=1000, delay='1ms')
    net.addLink(s3, r1, cls=TCLink, bw=1000, delay='1ms')
    net.addLink(s4, r1, cls=TCLink, bw=1000, delay='1ms')

    # R1 to R2 (100 Mbps bottleneck - L2)
    net.addLink(r1, r2, cls=TCLink, bw=100, delay='1ms')

    # R2 to D1 (1000 Mbps - L3)
    net.addLink(r2, d1, cls=TCLink, bw=1000, delay='1ms')

    # R2 to D2 (5 Mbps with 0.01% loss - L4, wireless)
    net.addLink(r2, d2, cls=TCLink, bw=5, delay='5ms', loss=0.01)

    info('*** Starting network\n')
    net.start()

    # Wait for switches to connect to controller
    info('*** Waiting for switches to connect to controller...\n')
    time.sleep(3)

    # Setup routing and qdisc (CAKE/fq_codel)
    setup_routing(net)
    setup_qdisc(net)

    info('*** Network is ready!\n')
    info('*** Testing connectivity (ping may fail initially until ARP completes)\n')

    # Test connectivity
    info('*** Testing s1 -> d1\n')
    result = net.pingFull([s1, d1], timeout=2)

    info('\n*** FAN Topology Ready ***\n')
    info('Controller: Remote controller at 127.0.0.1:6633\n')
    info('Bottleneck: R1-R2 link at 100 Mbps with CAKE/fq_codel per-flow scheduling\n')
    info('Wireless: R2-D2 link at 5 Mbps with 0.01%% loss\n')
    info('\n*** Commands to try:\n')
    info('  pingall                 - Test connectivity\n')
    info('  iperf s1 d1             - Test TCP throughput\n')
    info('  s1 iperf -s -u &         - Start UDP server on s1\n')
    info('  d1 iperf -c 10.0.1.10 -u -b 10M  - UDP client from d1\n')
    info('\n*** Starting CLI ***\n')

    CLI(net)

    info('*** Stopping network\n')
    net.stop()


if _name_ == '_main_':
    # Check if running as root
    if os.geteuid() != 0:
        print("This script must be run as root (use sudo)")
        exit(1)

    run_topology()