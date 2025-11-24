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
 - R1 to R2 (L2): 100 Mbps (bottleneck)
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
import os


def setup_qos(net):
    """
    Part A: Setup QoS with 2-queue system (linux-htb) on key switch ports
    Queue 0: High-priority (for streaming/UDP flows)
    Queue 1: Low-priority (for elastic/TCP flows)
    """
    info('*** Setting up QoS (linux-htb with 2 queues)\n')
    
    r1 = net.get('r1')
    r2 = net.get('r2')
    
    # Get interface names
    r1_r2_intf = None
    r2_d1_intf = None
    r2_d2_intf = None
    
    # Find R1-R2 interface on R1
    for intf in r1.intfList():
        if intf.name.startswith('r1-eth'):
            # Check if this connects to r2
            r1_r2_intf = intf.name
            break
    
    # Find R2 interfaces
    for intf in r2.intfList():
        if intf.name.startswith('r2-eth'):
            # We need to identify which connects to d1 and d2
            # Based on link order: r2-eth1 -> r1, r2-eth2 -> d1, r2-eth3 -> d2
            if 'eth2' in intf.name:
                r2_d1_intf = intf.name
            elif 'eth3' in intf.name:
                r2_d2_intf = intf.name
    
    info(f'R1-R2 interface: {r1_r2_intf}\n')
    info(f'R2-D1 interface: {r2_d1_intf}\n')
    info(f'R2-D2 interface: {r2_d2_intf}\n')
    
    # Configure QoS on R1 (port to R2) - 100 Mbps bottleneck
    if r1_r2_intf:
        info(f'*** Configuring QoS on R1 port {r1_r2_intf} (100 Mbps)\n')
        r1.cmd(f'ovs-vsctl -- set Port {r1_r2_intf} qos=@newqos \
                -- --id=@newqos create QoS type=linux-htb other-config:max-rate=100000000 queues=0=@q0,1=@q1 \
                -- --id=@q0 create Queue other-config:min-rate=50000000 other-config:max-rate=100000000 \
                -- --id=@q1 create Queue other-config:min-rate=10000000 other-config:max-rate=50000000')
    
    # Configure QoS on R2 (port to D1) - 1000 Mbps
    if r2_d1_intf:
        info(f'*** Configuring QoS on R2 port {r2_d1_intf} (1000 Mbps)\n')
        r2.cmd(f'ovs-vsctl -- set Port {r2_d1_intf} qos=@newqos \
                -- --id=@newqos create QoS type=linux-htb other-config:max-rate=1000000000 queues=0=@q0,1=@q1 \
                -- --id=@q0 create Queue other-config:min-rate=500000000 other-config:max-rate=1000000000 \
                -- --id=@q1 create Queue other-config:min-rate=100000000 other-config:max-rate=500000000')
    
    # Configure QoS on R2 (port to D2) - 5 Mbps (wireless)
    if r2_d2_intf:
        info(f'*** Configuring QoS on R2 port {r2_d2_intf} (5 Mbps)\n')
        r2.cmd(f'ovs-vsctl -- set Port {r2_d2_intf} qos=@newqos \
                -- --id=@newqos create QoS type=linux-htb other-config:max-rate=5000000 queues=0=@q0,1=@q1 \
                -- --id=@q0 create Queue other-config:min-rate=2500000 other-config:max-rate=5000000 \
                -- --id=@q1 create Queue other-config:min-rate=500000 other-config:max-rate=2500000')
    
    info('*** QoS setup complete\n')


def setup_routing(net):
    """
    L3 Routing Fix: Add default gateway routes for all hosts
    This allows hosts on different subnets to communicate
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
    info('*** Disabling TCP offloading\n')
    for host in net.hosts:
        host.cmd('ethtool -K %s-eth0 tx off rx off tso off gso off gro off lro off 2>/dev/null || true' % host.name)


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
    
    # Setup routing and QoS
    setup_routing(net)
    setup_qos(net)
    
    info('*** Network is ready!\n')
    info('*** Testing connectivity (ping may fail initially until ARP completes)\n')
    
    # Test connectivity
    info('*** Testing s1 -> d1\n')
    result = net.pingFull([s1, d1], timeout=2)
    
    info('\n*** FAN Topology Ready ***\n')
    info('Controller: Remote controller at 127.0.0.1:6633\n')
    info('Bottleneck: R1-R2 link at 100 Mbps\n')
    info('Wireless: R2-D2 link at 5 Mbps with 0.01%% loss\n')
    info('\n*** Commands to try:\n')
    info('  pingall - Test connectivity\n')
    info('  iperf s1 d1 - Test throughput\n')
    info('  s1 iperf -s -u &  - Start UDP server on s1\n')
    info('  d1 iperf -c 10.0.1.10 -u -b 10M - UDP client from d1\n')
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