

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, arp, ether_types, icmp, tcp, udp
from ryu.lib import hub
import time
from collections import defaultdict
import logging


class FANController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(FANController, self).__init__(*args, **kwargs)

        
        #   Fair-rate raw log (for plotting later)
        self.fair_logger = logging.getLogger("fair_raw")
        self.fair_logger.setLevel(logging.INFO)
        fh_fair = logging.FileHandler("normal_fair_raw.log", mode="a")
        fh_fair.setLevel(logging.INFO)
        fair_formatter = logging.Formatter("%(asctime)s - %(message)s")
        fh_fair.setFormatter(fair_formatter)
        self.fair_logger.addHandler(fh_fair)

        # Main FAN controller log
        fh = logging.FileHandler("fan_controller.log", mode="a")
        fh.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s"
        )
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)

        
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

        
        self.arp_table = {}          # per-dpid: ip -> mac
        self.pending_packets = {}    # per-dpid: dst_ip -> list(...)
        self.arp_requests = {}       # per-dpid: dst_ip -> last_req_time

        
        self.pfl = set()        # Protected Flow List (flow_keys)
        self.flow_stats = {}    # current flow stats from switch
        self.prev_flow_stats = {}  # snapshot from previous interval
        self.datapaths = {}

        # Link capacity assumptions (bottleneck link)
        self.BOTTLENECK_BW = 100 * 1000000 / 8.0  # bytes/sec (100 Mbps)
        self.LINK_CAP_BITS = self.BOTTLENECK_BW * 8.0  # bits/sec

        # Smoothing for fair-rate (alpha close to 1 => smoother)
        self.alpha = 0.1
        self.MIN_FAIR_RATE = 0.05 * self.BOTTLENECK_BW * 8.0  # bits/sec (5% of capacity)
        self.MAX_PRIORITY_LOAD = 0.70  # 70% of link used by streaming
        self.fair_rate = self.LINK_CAP_BITS
        self.is_congested = False

        self.last_fair_calc_time = time.time()

        # Config
        self.MONITOR_INTERVAL = 1.0
        self.IDLE_TIMEOUT = 60
        self.HARD_TIMEOUT = 0
        self.BOTTLENECK_DPID = 1  # admission enforced at R1

        self.logger.info("=" * 70)
        self.logger.info("Classic FAN Controller (Option B) Initialized")
        self.logger.info("=" * 70)

        # Start periodic monitoring
        self.monitor_thread = hub.spawn(self._monitor)

    
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

        # Table-miss rule: send all unmatched packets to controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(
            ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if not eth:
            return

        # Ignore IPv6 & LLDP
        if eth.ethertype in (ether_types.ETH_TYPE_IPV6, ether_types.ETH_TYPE_LLDP):
            return

        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            self.logger.info("= *" * 10 + "\nARP Packet Received\n" + "= *" * 10)
            self._handle_arp(datapath, pkt, eth, in_port)
            return

        if eth.ethertype == ether_types.ETH_TYPE_IP:
            self.logger.info("= *" * 10 + "\nIPv4 Packet Received\n" + "= *" * 10)
            self._handle_ipv4_with_mbac(datapath, pkt, eth, in_port)
            return

    
    def _handle_arp(self, datapath, pkt, eth, in_port):
        dpid = datapath.id
        arp_pkt = pkt.get_protocol(arp.arp)
        if not arp_pkt:
            return

        self.logger.info(
            f"[DPID {dpid}] Handling ARP packet: opcode={arp_pkt.opcode} "
            f"src_ip={arp_pkt.src_ip} dst_ip={arp_pkt.dst_ip}"
        )

        # Learn source IP->MAC
        self.arp_table[dpid][arp_pkt.src_ip] = arp_pkt.src_mac
        self.logger.info(
            f"[DPID {dpid}] ARP learned: {arp_pkt.src_ip} -> {arp_pkt.src_mac}"
        )
        self._process_pending_packets(datapath, arp_pkt.src_ip)

        if arp_pkt.opcode == arp.ARP_REQUEST:
            # If target IP is one of our router interfaces, answer
            if dpid in self.router_interfaces:
                for port, (ip, mac, net) in self.router_interfaces[dpid].items():
                    if arp_pkt.dst_ip == ip:
                        self._send_arp_reply(datapath, arp_pkt, mac, in_port)
                        return

        elif arp_pkt.opcode == arp.ARP_REPLY:
            self.logger.info(
                f"[DPID {dpid}] ARP reply: {arp_pkt.src_ip} is at {arp_pkt.src_mac}"
            )

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

    
    def _handle_ipv4_with_mbac(self, datapath, pkt, eth, in_port):
        dpid = datapath.id
        ipv4_pkt = pkt.get_protocol(ipv4.ipv4)
        if not ipv4_pkt:
            return

        src_ip = ipv4_pkt.src
        dst_ip = ipv4_pkt.dst
        proto = ipv4_pkt.proto

        # If packet is destined to router IP (ICMP echo to router), reply
        if dpid in self.router_interfaces:
            for port, (ip, mac, net) in self.router_interfaces[dpid].items():
                if dst_ip == ip:
                    icmp_pkt = pkt.get_protocol(icmp.icmp)
                    if icmp_pkt and icmp_pkt.type == icmp.ICMP_ECHO_REQUEST:
                        self._send_icmp_reply(datapath, pkt, eth, ipv4_pkt, icmp_pkt, in_port)
                    return

        # ICMP: just route (no MBAC)
        icmp_pkt = pkt.get_protocol(icmp.icmp)
        if icmp_pkt:
            self._route_packet(datapath, pkt, eth, ipv4_pkt, in_port)
            return

        # Extract transport ports
        tcp_pkt = pkt.get_protocol(tcp.tcp)
        udp_pkt = pkt.get_protocol(udp.udp)

        if tcp_pkt:
            src_port = tcp_pkt.src_port
            dst_port = tcp_pkt.dst_port
            is_streaming = False   # TCP = elastic
        elif udp_pkt:
            src_port = udp_pkt.src_port
            dst_port = udp_pkt.dst_port
            is_streaming = True    # UDP = streaming
        else:
            # Non-TCP/UDP traffic: route as pure L3
            self._route_packet(datapath, pkt, eth, ipv4_pkt, in_port)
            return

        flow_key = (dpid, src_ip, dst_ip, proto, src_port, dst_port)
        self.logger.info(
            f"[DPID {dpid}] Flow: {src_ip}:{src_port} -> {dst_ip}:{dst_port} proto={proto}"
        )

        # Non-bottleneck routers: plain routing, no MBAC
        if dpid != self.BOTTLENECK_DPID:
            self._route_and_install(
                datapath, pkt, eth, ipv4_pkt, in_port, flow_key,
                is_elastic=not is_streaming
            )
            return

        

        # 1) Streaming (UDP) flows: always admitted, protected
        if is_streaming:
            self.logger.info(f"[DPID {dpid}] UDP streaming -> ALWAYS ACCEPT")
            self.pfl.add(flow_key)  # treat as protected flow
            self._route_and_install(
                datapath, pkt, eth, ipv4_pkt, in_port, flow_key, is_elastic=False
            )
            return

        # 2) Elastic (TCP) flows:
        # If flow already in PFL: always forwarded, regardless of congestion
        if flow_key in self.pfl:
            self.logger.info(f"[DPID {dpid}] TCP elastic in PFL -> ACCEPT")
            self._route_and_install(
                datapath, pkt, eth, ipv4_pkt, in_port, flow_key, is_elastic=True
            )
            return

        # New elastic flow (flow_key not in PFL):
        # If congested -> DROP
        if self.is_congested:
            self.logger.info(
                f"[DPID {dpid}] CONGESTED and new elastic flow {flow_key} -> DROP"
            )
            return

        # Not congested -> ADMIT: add to PFL and install
        self.logger.info(f"[DPID {dpid}] Admitting new elastic flow {flow_key}")
        self.pfl.add(flow_key)
        self._route_and_install(
            datapath, pkt, eth, ipv4_pkt, in_port, flow_key, is_elastic=True
        )

    
    def _route_and_install(self, datapath, pkt, eth, ipv4_pkt, in_port, flow_key, is_elastic):
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

        # ARP resolved?
        if next_hop_ip in self.arp_table[dpid]:
            dst_mac = self.arp_table[dpid][next_hop_ip]
            self._forward_and_install(datapath, pkt, in_port, out_port, src_mac, dst_mac, flow_key)
        else:
            # Queue packet until ARP resolution
            if next_hop_ip not in self.pending_packets[dpid]:
                self.pending_packets[dpid][next_hop_ip] = []
            self.pending_packets[dpid][next_hop_ip].append(
                (pkt, in_port, out_port, flow_key)
            )

            if (next_hop_ip not in self.arp_requests[dpid] or
                    time.time() - self.arp_requests[dpid][next_hop_ip] > 1.0):
                self._send_arp_request(datapath, src_ip_router, src_mac, next_hop_ip, out_port)

    def _route_packet(self, datapath, pkt, eth, ipv4_pkt, in_port):
        """Routing for control/ICMP traffic – installs short-lived flow."""
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
            if (next_hop_ip not in self.arp_requests[dpid] or
                    time.time() - self.arp_requests[dpid][next_hop_ip] > 1.0):
                self._send_arp_request(datapath, src_ip_router, src_mac, next_hop_ip, out_port)

    def _forward_and_install(self, datapath, pkt, in_port, out_port, src_mac, dst_mac, flow_key):
        """Forward packet and install a flow (matches 5-tuple when possible)."""
        dpid = datapath.id
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        _, src_ip, dst_ip, proto, src_port, dst_port = flow_key

        # Match on L3+L4 when possible
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
            match = parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=src_ip,
                ipv4_dst=dst_ip,
                ip_proto=proto
            )

        # DSCP marking: elastic vs streaming (optional, for queues)
        if proto == 6:
            dscp_value = 0      # elastic
        else:
            dscp_value = 46     # streaming, higher priority

        actions = [
            parser.OFPActionSetField(ip_dscp=dscp_value),
            parser.OFPActionSetField(eth_src=src_mac),
            parser.OFPActionSetField(eth_dst=dst_mac),
            parser.OFPActionOutput(out_port)
        ]

        # Install flow
        self.add_flow(
            datapath, 10, match, actions,
            idle_timeout=self.IDLE_TIMEOUT,
            hard_timeout=self.HARD_TIMEOUT
        )

        self.logger.info(
            f"[DPID {dpid}] Flow installed: {src_ip}:{src_port} -> {dst_ip}:{dst_port} "
            f"proto={proto} out_port={out_port}"
        )

        # Forward the packet that triggered packet-in
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

    
    def add_flow(self, datapath, priority, match, actions, buffer_id=None,
                 idle_timeout=0, hard_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions)]

        if buffer_id:
            mod = parser.OFPFlowMod(
                datapath=datapath,
                buffer_id=buffer_id,
                priority=priority,
                match=match,
                instructions=inst,
                idle_timeout=idle_timeout,
                hard_timeout=hard_timeout
            )
        else:
            mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=priority,
                match=match,
                instructions=inst,
                idle_timeout=idle_timeout,
                hard_timeout=hard_timeout
            )
        datapath.send_msg(mod)

    
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
            if 'ipv4_src' not in match or 'ipv4_dst' not in match:
                continue

            src_ip = match.get('ipv4_src')
            dst_ip = match.get('ipv4_dst')
            proto = match.get('ip_proto', 0)

            src_port = match.get('tcp_src') or match.get('udp_src') or 0
            dst_port = match.get('tcp_dst') or match.get('udp_dst') or 0

            flow_key = (dpid, src_ip, dst_ip, proto, int(src_port), int(dst_port))

            self.flow_stats[flow_key] = {
                'byte_count': stat.byte_count,
                'packet_count': stat.packet_count,
                'duration_sec': stat.duration_sec,
                'last_seen': time.time()
            }

            if proto == 6 and stat.byte_count > 0:
                rate_mbps = (
                    stat.byte_count / stat.duration_sec * 8 / 1_000_000
                    if stat.duration_sec > 0 else 0
                )
                in_pfl = "YES" if flow_key in self.pfl else "NO"
                self.logger.info(
                    f"[DPID {dpid}] TCP stats: {src_ip}:{src_port}->{dst_ip}:{dst_port} "
                    f"bytes={stat.byte_count} dur={stat.duration_sec:.1f}s "
                    f"rate={rate_mbps:.1f}Mbps inPFL={in_pfl}"
                )

    def _analyze_stats(self):
        """
        Basic FAN measurement:
        - Compute fair_rate (bits/sec) from elastic flows
        - Compute priority_load (fraction of link capacity)
        - Set self.is_congested accordingly
        """
        now = time.time()

        if not hasattr(self, 'last_fair_calc_time') or self.last_fair_calc_time is None:
            self.last_fair_calc_time = now - self.MONITOR_INTERVAL

        # No flows -> idle link, fair_rate = link capacity, not congested
        if not self.flow_stats:
            self.fair_rate = self.LINK_CAP_BITS
            self.is_congested = False
            self.last_fair_calc_time = now
            self.logger.info(
                "[FAIR] no flow_stats -> fair_rate set to link capacity, uncongested"
            )
            return

        interval = now - self.last_fair_calc_time
        if interval <= 0:
            interval = self.MONITOR_INTERVAL

        elastic_bytes = 0
        priority_bytes = 0
        elastic_flow_keys = set()

        # GC + compute byte diffs
        for flow_key, stats in list(self.flow_stats.items()):
            dpid, _, _, proto, _, _ = flow_key

            # Drop stale entries
            if now - stats.get('last_seen', now) > 120:
                self.flow_stats.pop(flow_key, None)
                self.prev_flow_stats.pop(flow_key, None)
                continue

            # Only consider bottleneck router
            if dpid != self.BOTTLENECK_DPID:
                continue

            prev_stats = self.prev_flow_stats.get(flow_key)
            if prev_stats:
                byte_diff = stats['byte_count'] - prev_stats.get('byte_count', 0)
                if byte_diff < 0:
                    byte_diff = 0
            else:
                byte_diff = 0

            if proto == 17:  # UDP (streaming)
                priority_bytes += byte_diff
            elif proto == 6 and flow_key in self.pfl:  # TCP elastic, admitted
                elastic_bytes += byte_diff
                elastic_flow_keys.add(flow_key)

        num_elastic_flows = len(elastic_flow_keys)
        C = self.LINK_CAP_BITS

        # Fair rate computation (bits/sec)
        if num_elastic_flows > 0:
            used_bits = float(elastic_bytes) * 8.0
            total_bits_available = float(C) * interval
            idle_bits = max(total_bits_available - used_bits, 0.0)

            FB = float(elastic_bytes) / float(num_elastic_flows)  # bytes/flow in interval
            bits_candidate = max(idle_bits, FB * 8.0)

            measured_fair_rate = bits_candidate / interval if interval > 0 else 0.0

            self.fair_logger.info(
                f"[FAIR-RAW] interval={interval:.3f}s "
                f"elastic_bytes={elastic_bytes}B priority_bytes={priority_bytes}B "
                f"used_bits={used_bits:.0f} idle_bits={idle_bits:.0f} "
                f"FB={FB:.1f}B measured_fair_rate={measured_fair_rate/1e6:.3f}Mb/s"
            )

            if self.fair_rate is None:
                self.fair_rate = measured_fair_rate
            else:
                # Exponential smoothing
                self.fair_rate = (
                    self.alpha * self.fair_rate +
                    (1.0 - self.alpha) * measured_fair_rate
                )
        else:
            # No elastic flows -> fair_rate = full capacity
            self.fair_rate = float(C)

        # Priority load (fraction of capacity used by streaming flows)
        if interval > 0 and C > 0:
            priority_load = (priority_bytes * 8.0) / (C * interval)
        else:
            priority_load = 0.0
        priority_load = min(max(priority_load, 0.0), 1.0)

        # Congestion rule (classic FAN)
        self.is_congested = (
            self.fair_rate < self.MIN_FAIR_RATE or
            priority_load > self.MAX_PRIORITY_LOAD
        )

        # Snapshot current stats for next interval
        try:
            self.prev_flow_stats = {k: v.copy() for k, v in self.flow_stats.items()}
        except Exception:
            self.prev_flow_stats = dict(self.flow_stats)

        self.last_fair_calc_time = now

        self.logger.info(
            f"[FAN] fair_rate={self.fair_rate/1e6:.3f}Mb/s "
            f"elastic_flows={num_elastic_flows} "
            f"priority_load={priority_load*100:.1f}% "
            f"congested={self.is_congested} "
            f"PFL={len(self.pfl)}"
        )

    
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