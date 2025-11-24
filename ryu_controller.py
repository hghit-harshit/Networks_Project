#!/usr/bin/env python3
"""
FAN Controller (ports-aware) - drop-in replacement

Uses 5-tuple flow keys (dpid, src_ip, dst_ip, proto, src_port, dst_port)
so each TCP/UDP connection is a separate flow for MBAC / RAMAF.
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, arp, ether_types, icmp, tcp, udp
from ryu.lib import hub
import time
from collections import defaultdict


class FANController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def _init_(self, *args, **kwargs):
        super(FANController, self)._init_(*args, **kwargs)

        # Router configuration
        self.router_interfaces = {
            1: {  # R1
                1: ("10.0.1.1", "aa:bb:cc:dd:01:01", "10.0.1.0/24"),
                2: ("10.0.2.1", "aa:bb:cc:dd:01:02", "10.0.2.0/24"),
                3: ("10.0.3.1", "aa:bb:cc:dd:01:03", "10.0.3.0/24"),
                4: ("10.0.4.1", "aa:bb:cc:dd:01:04", "10.0.4.0/24"),
                5: ("10.0.5.1", "aa:bb:cc:dd:01:05", "10.0.5.0/24"),
            },
            2: {  # R2
                1: ("10.0.5.2", "aa:bb:cc:dd:02:01", "10.0.5.0/24"),
                2: ("10.0.10.1", "aa:bb:cc:dd:02:02", "10.0.10.0/24"),
                3: ("10.0.20.1", "aa:bb:cc:dd:02:03", "10.0.20.0/24"),
            }
        }

        self.routing_table = {
            1: {  # R1
                "10.0.1.0/24": (1, None),
                "10.0.2.0/24": (2, None),
                "10.0.3.0/24": (3, None),
                "10.0.4.0/24": (4, None),
                "10.0.5.0/24": (5, None),
                "10.0.10.0/24": (5, "10.0.5.2"),
                "10.0.20.0/24": (5, "10.0.5.2"),
            },
            2: {  # R2
                "10.0.5.0/24": (1, None),
                "10.0.10.0/24": (2, None),
                "10.0.20.0/24": (3, None),
                "10.0.1.0/24": (1, "10.0.5.1"),
                "10.0.2.0/24": (1, "10.0.5.1"),
                "10.0.3.0/24": (1, "10.0.5.1"),
                "10.0.4.0/24": (1, "10.0.5.1"),
            }
        }

        # L2/L3 helpers
        self.arp_table = {}
        self.pending_packets = {}
        self.arp_requests = {}

        # FAN-specific state
        self.pfl = set()                           # Protected Flow List (flow_keys)
        self.pafl = []                             # PAFL queue (evicted flows)
        self.admitted_flows_number = defaultdict(int)  # per-src-ip admitted count
        self.is_congested = False
        self.fair_rate = 0
        self.flow_stats = {}   # keyed by full flow_key (dpid,src,dst,proto,sp,dp)
        self.prev_flow_stats = {}
        self.datapaths = {}
        self.BOTTLENECK_BW = 100 * 1000000 / 8  # bytes per second (100 Mbps)
        self.LINK_CAP_BITS = self.BOTTLENECK_BW * 8 # bits/sec
        self.alpha = 0.1                              # smoothing factor
        self.MIN_FAIR_RATE = 0.05 * self.BOTTLENECK_BW  # 5% capacity
        self.MAX_PRIORITY_LOAD = 0.70                  # 70% load
        self.fair_rate = self.BOTTLENECK_BW            # initialize
        self.last_fair_calc_time = time.time()
        self.last_priority_calc_time = time.time()
        # Configuration
        self.MONITOR_INTERVAL = 1.0
        self.IDLE_TIMEOUT = 60
        self.HARD_TIMEOUT = 0
        self.MAX_FLOWS_PER_USER = 2
        self.CONGESTION_THRESHOLD = 1.10
        
        self.BOTTLENECK_DPID = 1  # R1 enforces admission

        self.logger.info("=" * 70)
        self.logger.info("FAN Controller (ports-aware) Initialized")
        self.logger.info("=" * 70)

        self.monitor_thread = hub.spawn(self._monitor)

    # ----------------- Switch connection / table-miss -----------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        dpid = datapath.id

        self.logger.info(f"[DPID {dpid}] Switch connected")
        self.datapaths[dpid] = datapath

        if dpid not in self.arp_table:
            self.arp_table[dpid] = {}
        if dpid not in self.pending_packets:
            self.pending_packets[dpid] = {}
        if dpid not in self.arp_requests:
            self.arp_requests[dpid] = {}

        if dpid in self.router_interfaces:
            self.logger.info(f"[DPID {dpid}] Router interfaces:")
            for port, (ip, mac, net) in self.router_interfaces[dpid].items():
                self.logger.info(f"  Port {port}: {ip} ({mac}) - Network {net}")

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                         ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    # ----------------- Packet-in handler -----------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if not eth:
            return

        if eth.ethertype == ether_types.ETH_TYPE_IPV6 or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            self._handle_arp(datapath, pkt, eth, in_port)
            return

        if eth.ethertype == ether_types.ETH_TYPE_IP:
            self._handle_ipv4_with_mbac(datapath, pkt, eth, in_port)
            return

    # ----------------- ARP helpers -----------------
    def _handle_arp(self, datapath, pkt, eth, in_port):
        dpid = datapath.id
        arp_pkt = pkt.get_protocol(arp.arp)
        if not arp_pkt:
            return
        self.arp_table[dpid][arp_pkt.src_ip] = arp_pkt.src_mac
        self.logger.debug(f"[DPID {dpid}] ARP learned: {arp_pkt.src_ip} -> {arp_pkt.src_mac}")
        self._process_pending_packets(datapath, arp_pkt.src_ip)

        if arp_pkt.opcode == arp.ARP_REQUEST:
            if dpid in self.router_interfaces:
                for port, (ip, mac, net) in self.router_interfaces[dpid].items():
                    if arp_pkt.dst_ip == ip:
                        self._send_arp_reply(datapath, arp_pkt, mac, in_port)
                        return

        elif arp_pkt.opcode == arp.ARP_REPLY:
            self.logger.debug(f"[DPID {dpid}] ARP reply: {arp_pkt.src_ip} is at {arp_pkt.src_mac}")

    def _send_arp_reply(self, datapath, arp_req, src_mac, out_port):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        arp_reply = packet.Packet()
        arp_reply.add_protocol(ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_ARP,
            dst=arp_req.src_mac,
            src=src_mac
        ))
        arp_reply.add_protocol(arp.arp(
            opcode=arp.ARP_REPLY,
            src_mac=src_mac,
            src_ip=arp_req.dst_ip,
            dst_mac=arp_req.src_mac,
            dst_ip=arp_req.src_ip
        ))
        arp_reply.serialize()

        actions = [parser.OFPActionOutput(out_port)]
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=actions,
            data=arp_reply.data
        )
        datapath.send_msg(out)

    def _send_arp_request(self, datapath, src_ip, src_mac, dst_ip, out_port):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id

        arp_req = packet.Packet()
        arp_req.add_protocol(ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_ARP,
            dst='ff:ff:ff:ff:ff:ff',
            src=src_mac
        ))
        arp_req.add_protocol(arp.arp(
            opcode=arp.ARP_REQUEST,
            src_mac=src_mac,
            src_ip=src_ip,
            dst_mac='00:00:00:00:00:00',
            dst_ip=dst_ip
        ))
        arp_req.serialize()

        actions = [parser.OFPActionOutput(out_port)]
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=actions,
            data=arp_req.data
        )
        datapath.send_msg(out)
        self.arp_requests[dpid][dst_ip] = time.time()

    # ----------------- IPv4 + MBAC -----------------
    def _handle_ipv4_with_mbac(self, datapath, pkt, eth, in_port):
        dpid = datapath.id
        ipv4_pkt = pkt.get_protocol(ipv4.ipv4)
        if not ipv4_pkt:
            return

        src_ip = ipv4_pkt.src
        dst_ip = ipv4_pkt.dst
        proto = ipv4_pkt.proto

        # Router interface check (ICMP to router)
        if dpid in self.router_interfaces:
            for port, (ip, mac, net) in self.router_interfaces[dpid].items():
                if dst_ip == ip:
                    icmp_pkt = pkt.get_protocol(icmp.icmp)
                    if icmp_pkt and icmp_pkt.type == icmp.ICMP_ECHO_REQUEST:
                        self._send_icmp_reply(datapath, pkt, eth, ipv4_pkt, icmp_pkt, in_port)
                    return

        # ICMP: forward normally (do not MBAC)
        icmp_pkt = pkt.get_protocol(icmp.icmp)
        if icmp_pkt:
            self._route_packet(datapath, pkt, eth, ipv4_pkt, in_port)
            return

        # Extract transport ports (if any)
        tcp_pkt = pkt.get_protocol(tcp.tcp)
        udp_pkt = pkt.get_protocol(udp.udp)

        if tcp_pkt:
            src_port = tcp_pkt.src_port
            dst_port = tcp_pkt.dst_port
            is_streaming = False
        elif udp_pkt:
            src_port = udp_pkt.src_port
            dst_port = udp_pkt.dst_port
            is_streaming = True
        else:
            # Non-TCP/UDP: just route by L3
            self._route_packet(datapath, pkt, eth, ipv4_pkt, in_port)
            return

        # Build 5-tuple flow_key
        flow_key = (dpid, src_ip, dst_ip, proto, src_port, dst_port)

        self.logger.info(f"[DPID {dpid}] Flow: {src_ip}:{src_port} -> {dst_ip}:{dst_port} proto={proto}")

        # Non-bottleneck routers just forward (no admission)
        if dpid != self.BOTTLENECK_DPID:
            self._route_and_install_flow(datapath, pkt, eth, ipv4_pkt, in_port, flow_key, is_elastic=not is_streaming)
            return

        # Streaming (UDP) flows: accept immediately (protected)
        if is_streaming:
            self.logger.info(f"[DPID {dpid}] UDP streaming -> accept and install")
            # Add to PFL (protected) to ensure queue assignment if needed
            self.pfl.add(flow_key)
            self._route_and_install_flow(datapath, pkt, eth, ipv4_pkt, in_port, flow_key, is_elastic=False)
            return

        # Elastic (TCP) flows: MBAC
        if flow_key in self.pfl:
            self.logger.debug(f"[DPID {dpid}] Flow in PFL -> Accept")
            self._route_and_install_flow(datapath, pkt, eth, ipv4_pkt, in_port, flow_key, is_elastic=True)
            return

        if self.is_congested:
            self.logger.info(f"[DPID {dpid}] CONGESTED and flow not in PFL -> DROP {flow_key}")
            return

        if len(self.pafl) > 0:
            self.logger.info(f"[DPID {dpid}] PAFL not empty ({len(self.pafl)}), re-admit one and drop new flow")
            readmit_flow = self.pafl.pop(0)
            self.pfl.add(readmit_flow)
            self.logger.info(f"[DPID {dpid}] Readmitted flow: {readmit_flow}")
            return

        # Per-user admission count (user = src_ip)
        if self.admitted_flows_number[src_ip] >= self.MAX_FLOWS_PER_USER:
            self.logger.info(f"[DPID {dpid}] User {src_ip} exceeded max flows ({self.admitted_flows_number[src_ip]}/{self.MAX_FLOWS_PER_USER}) -> DROP")
            return

        # Admit new elastic flow
        self.logger.info(f"[DPID {dpid}] Admitting new elastic flow {flow_key}")
        self.pfl.add(flow_key)
        self.admitted_flows_number[src_ip] += 1
        self._route_and_install_flow(datapath, pkt, eth, ipv4_pkt, in_port, flow_key, is_elastic=True)

    # ----------------- Routing / forwarding -----------------
    def _route_and_install_flow(self, datapath, pkt, eth, ipv4_pkt, in_port, flow_key, is_elastic):
        """Route packet and install flow (handles ARP)"""
        dpid = datapath.id
        dst_ip = ipv4_pkt.dst

        route = self._find_route(dpid, dst_ip)
        if not route:
            self.logger.warning(f"[DPID {dpid}] No route to {dst_ip}")
            return

        out_port, next_hop = route
        next_hop_ip = next_hop if next_hop else dst_ip

        if dpid not in self.router_interfaces or out_port not in self.router_interfaces[dpid]:
            self.logger.warning(f"[DPID {dpid}] No interface for port {out_port}")
            return

        src_mac = self.router_interfaces[dpid][out_port][1]
        src_ip_router = self.router_interfaces[dpid][out_port][0]

        if next_hop_ip in self.arp_table[dpid]:
            dst_mac = self.arp_table[dpid][next_hop_ip]
            self._forward_and_install(datapath, pkt, in_port, out_port, src_mac, dst_mac, flow_key)
        else:
            # queue until ARP resolution; include flow_key so we can install correct match later
            if next_hop_ip not in self.pending_packets[dpid]:
                self.pending_packets[dpid][next_hop_ip] = []
            self.pending_packets[dpid][next_hop_ip].append((pkt, in_port, out_port, flow_key))

            if next_hop_ip not in self.arp_requests[dpid] or \
               time.time() - self.arp_requests[dpid][next_hop_ip] > 1.0:
                self._send_arp_request(datapath, src_ip_router, src_mac, next_hop_ip, out_port)

    def _route_packet(self, datapath, pkt, eth, ipv4_pkt, in_port):
        """Simple routing with a short-lived flow for ICMP"""
        dpid = datapath.id
        src_ip = ipv4_pkt.src
        dst_ip = ipv4_pkt.dst

        route = self._find_route(dpid, dst_ip)
        if not route:
            return

        out_port, next_hop = route
        next_hop_ip = next_hop if next_hop else dst_ip

        if dpid not in self.router_interfaces or out_port not in self.router_interfaces[dpid]:
            return

        src_mac = self.router_interfaces[dpid][out_port][1]
        src_ip_router = self.router_interfaces[dpid][out_port][0]

        if next_hop_ip in self.arp_table[dpid]:
            dst_mac = self.arp_table[dpid][next_hop_ip]

            parser = datapath.ofproto_parser
            match = parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=src_ip,
                ipv4_dst=dst_ip,
                ip_proto=1  # ICMP
            )
            actions = [
                parser.OFPActionSetField(eth_src=src_mac),
                parser.OFPActionSetField(eth_dst=dst_mac),
                parser.OFPActionOutput(out_port)
            ]
            self.add_flow(datapath, 5, match, actions, idle_timeout=10)
            self._forward_packet_out(datapath, pkt, in_port, out_port, src_mac, dst_mac)
        else:
            if next_hop_ip not in self.pending_packets[dpid]:
                self.pending_packets[dpid][next_hop_ip] = []
            self.pending_packets[dpid][next_hop_ip].append((pkt, in_port, out_port))
            if next_hop_ip not in self.arp_requests[dpid] or \
               time.time() - self.arp_requests[dpid][next_hop_ip] > 1.0:
                self._send_arp_request(datapath, src_ip_router, src_mac, next_hop_ip, out_port)

    def _forward_and_install(self, datapath, pkt, in_port, out_port, src_mac, dst_mac, flow_key):
        """Forward packet and install a flow that MATCHES ports when available"""
        dpid = datapath.id
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Unpack flow_key: (dpid, src_ip, dst_ip, proto, src_port, dst_port)
        _, src_ip, dst_ip, proto, src_port, dst_port = flow_key

        # Build OFPMatch including transport ports when present
        if proto == 6:  # TCP
            match = parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=src_ip,
                ipv4_dst=dst_ip,
                ip_proto=6,
                tcp_src=src_port,
                tcp_dst=dst_port
            )
        elif proto == 17:  # UDP
            match = parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=src_ip,
                ipv4_dst=dst_ip,
                ip_proto=17,
                udp_src=src_port,
                udp_dst=dst_port
            )
        else:
            # Fallback to L3 match
            match = parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=src_ip,
                ipv4_dst=dst_ip,
                ip_proto=proto
            )

        # Queue assignment logic:
        # - At bottleneck router (DPID 1), admitted elastic flows -> queue 1 (lower priority)
        # - Streaming (UDP) flows (we put them in PFL earlier) -> queue 0 (protected)
        queue_id = 0
        if dpid == self.BOTTLENECK_DPID:
            if flow_key in self.pfl:
                # if flow is protected but elastic, we send to queue 1 (elastic admitted)
                # streaming flows have been added to PFL and should be considered protected.
                # choose queue for elastic admitted flows as 1, streaming as 0
                # Determine streaming vs elastic by proto (UDP treated streaming earlier)
                if proto == 6:
                    # TCP admitted -> elastic queue (1)
                    queue_id = 1
                else:
                    # UDP -> protected queue (0)
                    queue_id = 0

        actions = [
            parser.OFPActionSetQueue(queue_id),
            parser.OFPActionSetField(eth_src=src_mac),
            parser.OFPActionSetField(eth_dst=dst_mac),
            parser.OFPActionOutput(out_port)
        ]

        # Install the flow
        self.add_flow(datapath, 10, match, actions,
                      idle_timeout=self.IDLE_TIMEOUT,
                      hard_timeout=self.HARD_TIMEOUT)

        self.logger.info(f"[DPID {dpid}] Flow installed: {src_ip}:{src_port} -> {dst_ip}:{dst_port} proto={proto} queue={queue_id} out_port={out_port}")

        # Forward the triggering packet
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=in_port,
            actions=actions,
            data=pkt.data
        )
        datapath.send_msg(out)

    def _forward_packet_out(self, datapath, pkt, in_port, out_port, src_mac, dst_mac):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        actions = [
            parser.OFPActionSetField(eth_src=src_mac),
            parser.OFPActionSetField(eth_dst=dst_mac),
            parser.OFPActionOutput(out_port)
        ]

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=in_port,
            actions=actions,
            data=pkt.data
        )
        datapath.send_msg(out)

    # ----------------- Flow add/delete -----------------
    def add_flow(self, datapath, priority, match, actions, buffer_id=None,
                 idle_timeout=0, hard_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                   priority=priority, match=match,
                                   instructions=inst, idle_timeout=idle_timeout,
                                   hard_timeout=hard_timeout)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                   match=match, instructions=inst,
                                   idle_timeout=idle_timeout, hard_timeout=hard_timeout)
        datapath.send_msg(mod)

    # ----------------- Monitoring / RAMAF -----------------
    def _monitor(self):
        while True:
            hub.sleep(self.MONITOR_INTERVAL)
            for dpid, datapath in list(self.datapaths.items()):
                self._request_stats(datapath)
            hub.sleep(0.2)
            self._analyze_stats()

    def _request_stats(self, datapath):
        parser = datapath.ofproto_parser
        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id

        for stat in body:
            match = stat.match
            # require ipv4_src and ipv4_dst for our tracked flows
            if 'ipv4_src' not in match or 'ipv4_dst' not in match:
                continue

            src_ip = match.get('ipv4_src')
            dst_ip = match.get('ipv4_dst')
            proto = match.get('ip_proto', 0)

            # extract transport ports if present
            src_port = match.get('tcp_src') or match.get('udp_src') or 0
            dst_port = match.get('tcp_dst') or match.get('udp_dst') or 0

            flow_key = (dpid, src_ip, dst_ip, proto, int(src_port), int(dst_port))

            self.flow_stats[flow_key] = {
                'byte_count': stat.byte_count,
                'packet_count': stat.packet_count,
                'duration_sec': stat.duration_sec,
                'last_seen': time.time()
            }

            # debug print for TCP
            if proto == 6 and stat.byte_count > 0:
                rate_mbps = (stat.byte_count / stat.duration_sec * 8 / 1000000) if stat.duration_sec > 0 else 0
                in_pfl = "YES" if flow_key in self.pfl else "NO"
                self.logger.info(f"[DPID {dpid}] TCP stats: {src_ip}:{src_port}->{dst_ip}:{dst_port} bytes={stat.byte_count} dur={stat.duration_sec:.1f}s rate={rate_mbps:.1f}Mbps inPFL={in_pfl}")

    def _analyze_stats(self):
        # No stats = idle link, no congestion
        if not self.flow_stats:
            self.fair_rate = self.LINK_CAP_BITS
            self.is_congested = False
            return

        now = time.time()
        interval = now - self.last_fair_calc_time
        if interval <= 0:
            interval = self.MONITOR_INTERVAL

        elastic_bytes = 0
        priority_bytes = 0
        elastic_flow_keys = set()

        # Only consider flows on the bottleneck switch (R1)
        for flow_key, stats in list(self.flow_stats.items()):
            dpid, _, _, proto, _, _ = flow_key

            # GC: drop stale stats
            if now - stats['last_seen'] > 120:
                del self.flow_stats[flow_key]
                if flow_key in self.prev_flow_stats:
                    del self.prev_flow_stats[flow_key]
                continue

            if dpid != self.BOTTLENECK_DPID:
                continue

            prev_stats = self.prev_flow_stats.get(flow_key)
            if prev_stats:
                byte_diff = stats['byte_count'] - prev_stats['byte_count']
                if byte_diff < 0:
                    byte_diff = 0
            else:
                byte_diff = 0

            # UDP = priority / streaming
            if proto == 17:
                priority_bytes += byte_diff
            # TCP + admitted (in PFL) = elastic
            elif proto == 6 and flow_key in self.pfl:
                elastic_bytes += byte_diff
                elastic_flow_keys.add(flow_key)

        num_elastic_flows = len(elastic_flow_keys)

        # Snapshot for next interval
        self.prev_flow_stats = {k: v.copy() for k, v in self.flow_stats.items()}
        self.last_fair_calc_time = now

        # Link capacity C in bits/sec (paper uses bits)
        C = self.LINK_CAP_BITS

        if num_elastic_flows > 0:
            used_bits = elastic_bytes * 8.0
            total_bits_expected = C * interval
            idle_bits = max(total_bits_expected - used_bits, 0.0)

            S = idle_bits / total_bits_expected if total_bits_expected > 0 else 0.0
            FB = elastic_bytes / float(num_elastic_flows)  # bytes per elastic flow in interval

            # Paper formula: fair_rate = max{ S*C , FB*8 } / (t2 - t1)
            measured_fair_rate = max(S * C, FB * 8.0) / interval if interval > 0 else 0.0

            self.logger.info(
                f"[FAIR-RAW] interval={interval:.3f}s "
                f"S={S:.3f} C={C/1e6:.1f}Mb/s "
                f"S*C={(S*C)/1e6:.2f}Mb/s "
                f"FB={FB:.1f}B FB*8={(FB*8.0)/1e6:.2f}Mb/s"
            )

            # Exponential smoothing
            self.fair_rate = self.alpha * self.fair_rate + (1.0 - self.alpha) * measured_fair_rate
        else:
            # No elastic flows -> fair_rate = full capacity
            S = 1.0
            self.fair_rate = C

        # Priority load (0–1), based on UDP bytes on the bottleneck
        if interval > 0 and C > 0:
            priority_load = (priority_bytes * 8.0) / (C * interval)
        else:
            priority_load = 0.0
        if priority_load > 1.0:
            priority_load = 1.0

        # Paper rule: congested if fair_rate < min_fair_rate OR priority_load > max_priority_load
        self.is_congested = (self.fair_rate < self.MIN_FAIR_RATE) or (priority_load > self.MAX_PRIORITY_LOAD)

        self.logger.info(
            f"[FAN] fair_rate={self.fair_rate/1e6:.2f}Mbps "
            f"elastic_flows={num_elastic_flows} "
            f"priority_load={priority_load*100:.1f}% "
            f"congested={self.is_congested}"
        )


    def _delete_flow(self, flow_key):
        dpid, src_ip, dst_ip, proto, src_port, dst_port = flow_key
        if dpid not in self.datapaths:
            return
        datapath = self.datapaths[dpid]
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        # Build match consistent with how we installed the flow
        if proto == 6:
            match = parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=src_ip,
                ipv4_dst=dst_ip,
                ip_proto=6,
                tcp_src=src_port,
                tcp_dst=dst_port
            )
        elif proto == 17:
            match = parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=src_ip,
                ipv4_dst=dst_ip,
                ip_proto=17,
                udp_src=src_port,
                udp_dst=dst_port
            )
        else:
            match = parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=src_ip,
                ipv4_dst=dst_ip,
                ip_proto=proto
            )

        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=match
        )
        datapath.send_msg(mod)
        self.logger.info(f"[DPID {dpid}] Sent FlowMod(DELETE) for {src_ip}:{src_port} -> {dst_ip}:{dst_port}")

    # ----------------- Pending packets after ARP -----------------
    def _process_pending_packets(self, datapath, resolved_ip):
        dpid = datapath.id
        if resolved_ip not in self.pending_packets[dpid]:
            return

        dst_mac = self.arp_table[dpid][resolved_ip]
        for entry in self.pending_packets[dpid][resolved_ip]:
            if len(entry) == 4:
                pkt, in_port, out_port, flow_key = entry
                src_mac = self.router_interfaces[dpid][out_port][1]
                self._forward_and_install(datapath, pkt, in_port, out_port, src_mac, dst_mac, flow_key)
            else:
                pkt, in_port, out_port = entry
                src_mac = self.router_interfaces[dpid][out_port][1]
                self._forward_packet_out(datapath, pkt, in_port, out_port, src_mac, dst_mac)

        del self.pending_packets[dpid][resolved_ip]
        if resolved_ip in self.arp_requests[dpid]:
            del self.arp_requests[dpid][resolved_ip]

    # ----------------- Utility / routing -----------------
    def _find_route(self, dpid, dst_ip):
        if dpid not in self.routing_table:
            return None
        for network, (out_port, next_hop) in self.routing_table[dpid].items():
            if self._ip_in_network(dst_ip, network):
                return (out_port, next_hop)
        return None

    def _ip_in_network(self, ip, network):
        network_prefix = network.split('/')[0].rsplit('.', 1)[0]
        ip_prefix = ip.rsplit('.', 1)[0]
        return network_prefix == ip_prefix

    def _send_icmp_reply(self, datapath, orig_pkt, orig_eth, orig_ipv4, orig_icmp, in_port):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        reply_pkt = packet.Packet()
        reply_pkt.add_protocol(ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_IP,
            dst=orig_eth.src,
            src=orig_eth.dst
        ))
        reply_pkt.add_protocol(ipv4.ipv4(
            dst=orig_ipv4.src,
            src=orig_ipv4.dst,
            proto=orig_ipv4.proto
        ))
        reply_pkt.add_protocol(icmp.icmp(
            type_=icmp.ICMP_ECHO_REPLY,
            code=icmp.ICMP_ECHO_REPLY_CODE,
            csum=0,
            data=orig_icmp.data
        ))
        reply_pkt.serialize()

        actions = [parser.OFPActionOutput(in_port)]
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=actions,
            data=reply_pkt.data
        )
        datapath.send_msg(out)