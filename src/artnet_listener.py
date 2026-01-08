"""Art-Net UDP receiver."""

import ipaddress
import os
import re
import selectors
import socket
import struct
import subprocess
import threading
import binascii

from src.config import logger, APP_NAME


def get_local_ip():
    """Get the local IP address of this machine."""
    try:
        # Create a dummy connection to determine the local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_local_ipv4_addresses() -> list[str]:
    """Best-effort, stdlib-only enumeration of local IPv4 addresses.

    Returns non-loopback IPv4s, falling back to a single routed IP if needed.
    """
    ips: set[str] = set()

    # 1) Try OS commands (more accurate across multiple NICs)
    try:
        if os.name == "nt":
            out = subprocess.check_output(["ipconfig"], text=True, errors="ignore")
            for m in re.finditer(
                r"IPv4 Address[^\:]*:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)",
                out,
            ):
                ips.add(m.group(1))
        else:
            out = subprocess.check_output(["ip", "-o", "-4", "addr", "show"], text=True, errors="ignore")
            for m in re.finditer(r"inet\s+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)/\d+", out):
                ips.add(m.group(1))
    except Exception:
        pass

    # 2) Fallback: getaddrinfo(hostname)
    try:
        host = socket.gethostname()
        for res in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM):
            ips.add(res[4][0])
    except Exception:
        pass

    # Filter out loopback/link-local
    filtered: list[str] = []
    for ip in sorted(ips):
        try:
            obj = ipaddress.ip_address(ip)
            if obj.version == 4 and not obj.is_loopback and not obj.is_link_local:
                filtered.append(ip)
        except Exception:
            continue

    # 3) Last fallback: single "routed" IP
    if not filtered:
        try:
            filtered = [get_local_ip()]
        except Exception:
            filtered = ["127.0.0.1"]

    return filtered


# Art-Net OpCode names for debugging
ARTNET_OPCODES = {
    0x2000: "ArtPoll",
    0x2100: "ArtPollReply", 
    0x5000: "ArtDmx",
    0x5100: "ArtNzs",
    0x5200: "ArtSync",
    0x6000: "ArtAddress",
    0x7000: "ArtInput",
    0x8000: "ArtFirmwareMaster",
    0x8100: "ArtFirmwareReply",
    0x9000: "ArtTodRequest",
    0x9100: "ArtTodData",
    0x9200: "ArtTodControl",
    0x9300: "ArtRdm",
    0x9400: "ArtRdmSub",
    0xF000: "ArtTimeCode",
    0xF100: "ArtTimeSync",
    0xF200: "ArtTrigger",
    0xF300: "ArtDirectory",
}


class ArtNetReceiver:
    """
    Art-Net DMX receiver with node discovery support.
    Listens for Art-Net packets on UDP and extracts DMX data.
    Responds to ArtPoll with ArtPollReply so controllers like SoundSwitch can discover this node.
    """
    
    ARTNET_HEADER = b'Art-Net\x00'
    ARTNET_PORT = 6454
    ARTNET_BROADCAST = "255.255.255.255"
    
    # Art-Net protocol version
    ARTNET_VERSION = 14
    
    def __init__(self, callback, universe=0):
        """
        Initialize the Art-Net receiver.
        
        Args:
            callback: Function to call with DMX data (list of ints)
            universe: Art-Net universe to listen to (0-15)
        """
        self.callback = callback
        self.target_universe = universe
        # One socket per local IPv4 (robust on Windows when another app already binds 6454)
        self.ips: list[str] = []
        self.sockets: dict[str, socket.socket] = {}
        self.selector = selectors.DefaultSelector()
        self.running = False
        self.thread = None
        self.announce_timer = None
        self.announce_interval = 2.5  # Seconds between announcements (Art-Net spec: 2.5-3s)
        self.local_ip = get_local_ip()
        self.advertise_ip: str | None = None

    def _pick_advertise_ip_for_controller(self, controller_ip: str) -> str | None:
        """Pick the best local IP to advertise to a given controller.

        Strategy: choose local IP with same /24 as controller if possible,
        else fall back to first bound IP.
        """
        try:
            c = ipaddress.ip_address(controller_ip)
            if c.version != 4:
                return None
        except Exception:
            return None

        c_net = ipaddress.ip_network(f"{controller_ip}/24", strict=False)
        for ip in self.sockets.keys():
            try:
                if ipaddress.ip_address(ip) in c_net:
                    return ip
            except Exception:
                continue

        return next(iter(self.sockets.keys()), None)

    def start(self, ip="0.0.0.0", port=None):
        """Start listening for Art-Net packets."""
        if self.running:
            return
        
        port = port or self.ARTNET_PORT
        self.local_ip = get_local_ip()

        # Reset sockets/selector from any previous run
        try:
            for sock in list(self.sockets.values()):
                try:
                    self.selector.unregister(sock)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass
        finally:
            self.sockets.clear()

        self.ips = get_local_ipv4_addresses() if ip == "0.0.0.0" else [ip]
        logger.info(f"ArtNet binding candidates: {', '.join(self.ips)}")

        for bind_ip in self.ips:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

                # Critical fix (Windows): bind to a specific local IPv4, not wildcard.
                sock.bind((bind_ip, port))
                sock.setblocking(False)

                self.sockets[bind_ip] = sock
                self.selector.register(sock, selectors.EVENT_READ, data=bind_ip)
                logger.info(f"Bound Art-Net UDP {bind_ip}:{port}")
            except Exception as e:
                logger.error(f"Failed to bind ArtNet on {bind_ip}:{port}: {e}")

        if not self.sockets:
            logger.error("No Art-Net sockets could be bound. Art-Net receive will be disabled.")
            return

        # Default advertise IP (may be refined once we see a controller)
        self.advertise_ip = next(iter(self.sockets.keys()), None)
        if self.advertise_ip:
            self.local_ip = self.advertise_ip

        self.running = True
        self.thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.thread.start()

        # Send initial ArtPollReply to announce ourselves
        self._send_poll_reply_broadcast()

        # Start periodic announcement timer (Art-Net nodes should announce every 2.5-3 seconds)
        self._start_announce_timer()

        logger.info(
            f"ArtNet Node started (advertising {self.local_ip}) on UDP:{port} for Universe {self.target_universe}"
        )

    def stop(self):
        """Stop the Art-Net listener."""
        self.running = False
        if self.announce_timer:
            self.announce_timer.cancel()
            self.announce_timer = None

        try:
            for sock in list(self.sockets.values()):
                try:
                    self.selector.unregister(sock)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass
        finally:
            self.sockets.clear()
            self.advertise_ip = None

        logger.info("ArtNet Listener stopped")

    def set_universe(self, universe: int):
        """Update the target universe."""
        self.target_universe = universe
        logger.info(f"ArtNet Universe set to {universe}")
        # Announce the change
        if self.running:
            self._send_poll_reply_broadcast()

    def _receive_loop(self):
        """Main receive loop running in separate thread."""
        logger.info("ArtNet receive loop started, waiting for packets...")
        packet_count = 0
        while self.running:
            try:
                events = self.selector.select(timeout=0.5)
                for key, _mask in events:
                    sock: socket.socket = key.fileobj
                    bound_ip: str = key.data
                    try:
                        data, addr = sock.recvfrom(2048)
                    except BlockingIOError:
                        continue
                    except OSError:
                        continue

                    packet_count += 1

                    # Log packet details at DEBUG level
                    logger.debug(f"[PKT #{packet_count}] {len(data)} bytes from {addr[0]}:{addr[1]} (rx on {bound_ip})")

                    # Show first 40 bytes as hex for debugging
                    hex_preview = binascii.hexlify(data[:40]).decode('ascii')
                    logger.debug(f"[PKT #{packet_count}] HEX: {hex_preview}...")

                    self._parse_packet(data, addr, bound_ip)
            except Exception as e:
                logger.error(f"Error in ArtNet loop: {e}", exc_info=True)

    def _parse_packet(self, data: bytes, addr: tuple, bound_ip: str | None = None):
        """Parse an Art-Net packet and handle accordingly."""
        # Skip our own broadcasts from localhost
        if addr[0] == "127.0.0.1":
            logger.debug(f"Skipping packet from localhost (our own broadcast)")
            return
            
        # Minimum Art-Net header size check
        if len(data) < 10:
            logger.warning(f"Packet too small ({len(data)} bytes) from {addr[0]}")
            return
        
        # Check Header "Art-Net\0"
        header = data[0:8]
        if header != self.ARTNET_HEADER:
            logger.warning(f"Not an Art-Net packet from {addr[0]}, header: {header!r}")
            return

        # Get OpCode (Little Endian)
        opcode = struct.unpack('<H', data[8:10])[0]
        opcode_name = ARTNET_OPCODES.get(opcode, f"Unknown(0x{opcode:04X})")

        # Ignore our own pollreply reflections (common on some setups)
        if opcode == 0x2100 and addr[0] in self.sockets.keys():
            return
        
        # Log DMX at DEBUG level (very frequent), other packets based on type
        if opcode == 0x5000:  # ArtDmx
            logger.debug(f"Art-Net {opcode_name} from {addr[0]}")
        elif opcode == 0x2100:  # ArtPollReply - frequent, log at DEBUG
            logger.debug(f"Art-Net {opcode_name} (0x{opcode:04X}) from {addr[0]}")
        else:
            logger.debug(f"Art-Net {opcode_name} (0x{opcode:04X}) from {addr[0]}")
        
        # Handle ArtPoll - respond with ArtPollReply
        if opcode == 0x2000:
            if bound_ip:
                logger.debug(f"Responding to ArtPoll from {addr[0]} (rx on {bound_ip})")
            else:
                logger.debug(f"Responding to ArtPoll from {addr[0]}")
            self._send_poll_reply(addr[0])
            # Also send to broadcast to ensure visibility
            self._send_poll_reply_broadcast()
            return

        # Handle ArtDmx (0x5000)
        if opcode == 0x5000:
            self._handle_artdmx(data, addr)
            return
        
        # Handle ArtAddress (0x6000) - Controller configuring node
        if opcode == 0x6000:
            self._handle_artaddress(data, addr)
            return
        
        # Handle ArtPollReply (0x2100) - Other nodes announcing themselves
        if opcode == 0x2100:
            # We don't need to do anything with these, just ignore them
            return
            
        # Log other unhandled packet types at DEBUG level
        logger.debug(f"Unhandled Art-Net packet type: {opcode_name}")

    def _handle_artaddress(self, data: bytes, addr: tuple):
        """Handle ArtAddress packet - controller configuring our node."""
        logger.debug(f"=== ArtAddress from {addr[0]} ===")
        
        if len(data) < 107:
            logger.warning(f"ArtAddress packet too small ({len(data)} bytes)")
            return
        
        # ArtAddress structure:
        # Bytes 0-7: ID ("Art-Net\0")
        # Bytes 8-9: OpCode (0x6000)
        # Byte 10: Protocol Version Hi
        # Byte 11: Protocol Version Lo
        # Byte 12: NetSwitch
        # Byte 13: BindIndex - which node to configure (0 = all)
        # Bytes 14-31: ShortName
        # Bytes 32-95: LongName
        # Bytes 96-99: SwIn (4 bytes)
        # Bytes 100-103: SwOut (4 bytes)
        # Byte 104: SubSwitch
        # Byte 105: SwVideo (deprecated)
        # Byte 106: Command
        
        net_switch = data[12]
        bind_index = data[13]
        sub_switch = data[104] if len(data) > 104 else 0
        command = data[106] if len(data) > 106 else 0
        
        logger.debug(f"  NetSwitch: {net_switch}")
        logger.debug(f"  BindIndex: {bind_index}")
        logger.debug(f"  SubSwitch: {sub_switch}")
        logger.debug(f"  Command: 0x{command:02X}")
        
        if len(data) > 100:
            sw_out = list(data[100:104])
            logger.debug(f"  SwOut: {sw_out}")
        
        # Always respond with ArtPollReply to confirm we received the config
        self._send_poll_reply(addr[0])

    def _handle_artdmx(self, data: bytes, addr: tuple):
        """Parse and handle an ArtDmx packet with full debugging."""
        if len(data) < 18:
            logger.warning(f"ArtDmx packet too small ({len(data)} bytes)")
            return
            
        # ArtDmx packet structure:
        # Bytes 0-7: "Art-Net\0"
        # Bytes 8-9: OpCode (0x5000)
        # Bytes 10-11: Protocol Version (Hi, Lo)
        # Byte 12: Sequence
        # Byte 13: Physical
        # Bytes 14-15: Universe (SubUni, Net) - this is the port-address
        # Bytes 16-17: Length (Hi, Lo)
        # Bytes 18+: DMX Data
        
        proto_hi = data[10]
        proto_lo = data[11]
        sequence = data[12]
        physical = data[13]
        sub_uni = data[14]  # Bits 0-3: Universe, Bits 4-7: Sub-Net
        net = data[15]      # Bits 0-6: Net
        length_hi = data[16]
        length_lo = data[17]
        dmx_length = (length_hi << 8) | length_lo
        
        # Calculate the full 15-bit Port-Address
        # Port-Address = (Net << 8) | (SubNet << 4) | Universe
        # But in simple setups, sub_uni byte directly contains the universe (0-255 range for Art-Net 3+)
        incoming_universe = sub_uni | (net << 8)
        
        logger.debug(f"=== ArtDmx Details ===")
        logger.debug(f"  Protocol Version: {proto_hi}.{proto_lo}")
        logger.debug(f"  Sequence: {sequence}")
        logger.debug(f"  Physical Port: {physical}")
        logger.debug(f"  SubUni byte: {sub_uni} (0x{sub_uni:02X})")
        logger.debug(f"  Net byte: {net} (0x{net:02X})")
        logger.debug(f"  Calculated Universe: {incoming_universe}")
        logger.debug(f"  DMX Length: {dmx_length}")
        logger.debug(f"  Listening for Universe: {self.target_universe}")
        
        # Show first 20 DMX channel values
        dmx_data = list(data[18:18+min(dmx_length, 512)])

        if dmx_data:
            preview = dmx_data[:20]
            logger.debug(f"  DMX Ch 1-20: {preview}")
            
            # Show non-zero channels
            non_zero = [(i+1, v) for i, v in enumerate(dmx_data) if v > 0]
            if non_zero:
                logger.debug(f"  Non-zero channels: {non_zero[:10]}...")  # First 10
        
        if incoming_universe == self.target_universe:
            logger.debug(f"DMX received: universe {incoming_universe}, {len(dmx_data)} channels")
            self.callback(dmx_data)
        else:
            logger.debug(f"DMX ignored: universe {incoming_universe} (listening for {self.target_universe})")

    def _build_poll_reply(self, bind_ip: str | None = None) -> bytes:
        """
        Build an ArtPollReply packet to announce this node.
        This is what makes the node visible to controllers like SoundSwitch.
        
        Based on Art-Net 4 specification and OLA implementation.
        """
        packet = bytearray(239)  # Fixed size ArtPollReply
        
        # Art-Net header (8 bytes) - offset 0
        packet[0:8] = self.ARTNET_HEADER
        
        # OpCode ArtPollReply = 0x2100 (Little Endian) - offset 8
        packet[8] = 0x00
        packet[9] = 0x21
        
        # IP Address (4 bytes) - offset 10
        ip_to_advertise = bind_ip or self.advertise_ip or self.local_ip
        ip_parts = [int(x) for x in ip_to_advertise.split('.')]
        packet[10:14] = bytes(ip_parts)
        
        # Port (2 bytes, Little Endian) - offset 14
        packet[14] = self.ARTNET_PORT & 0xFF
        packet[15] = (self.ARTNET_PORT >> 8) & 0xFF
        
        # Version Info High/Low (2 bytes) - offset 16
        packet[16] = 0x00  # Version Hi
        packet[17] = self.ARTNET_VERSION  # Version Lo
        
        # NetSwitch (1 byte) - offset 18 - Bits 14-8 of Port-Address
        packet[18] = (self.target_universe >> 8) & 0x7F
        
        # SubSwitch (1 byte) - offset 19 - Bits 7-4 of Port-Address  
        packet[19] = (self.target_universe >> 4) & 0x0F
        
        # OEM Code (2 bytes, Hi Lo) - offset 20 - Use OLA's OEM code for compatibility
        packet[20] = 0x00  # OEM Hi
        packet[21] = 0x00  # OEM Lo (0x0000 = unknown, should work)
        
        # Ubea Version (1 byte) - offset 22
        packet[22] = 0x00
        
        # Status1 (1 byte) - offset 23
        # Bit 7-6: 11 = Indicators in Normal mode
        # Bit 5-4: 11 = All Port-Address set by network or web browser
        # Bit 3: 0 = Not RDM capable
        # Bit 2: 0 = Boot from flash (normal)
        # Bit 1-0: 11 = Indicators in Normal mode
        packet[23] = 0xF0  # Normal operation, network configured
        
        # ESTA Manufacturer Code (2 bytes, Lo Hi) - offset 24
        packet[24] = 0x00  # ESTA Lo
        packet[25] = 0x00  # ESTA Hi
        
        # Short Name (18 bytes, null-terminated) - offset 26
        short_name = "PulzWaveArtNetMidiBridge".encode('ascii')[:17]
        packet[26:26+len(short_name)] = short_name
        
        # Long Name (64 bytes, null-terminated) - offset 44
        long_name = "PulzWaveArtNetMidiBridge Art-Net DMX Node".encode('ascii')[:63]
        packet[44:44+len(long_name)] = long_name
        
        # Node Report (64 bytes) - offset 108
        node_report = "#0001 [0000] OK".encode('ascii')[:63]
        packet[108:108+len(node_report)] = node_report
        
        # NumPorts High (1 byte) - offset 172
        packet[172] = 0x00
        
        # NumPorts Low (1 byte) - offset 173 - We have 1 port
        packet[173] = 0x01
        
        # Port Types (4 bytes) - offset 174
        # For an OUTPUT port (receives Art-Net, outputs to physical):
        # Bit 7: 1 = Port can output data from Art-Net
        # Bit 6: 0 = Port cannot input data to Art-Net
        # Bit 5: 0 = Not LTP
        # Bit 0-4: 0 = DMX512
        packet[174] = 0x80  # Port 1: Output capable, DMX512
        packet[175] = 0x00  # Port 2: Not used
        packet[176] = 0x00  # Port 3: Not used
        packet[177] = 0x00  # Port 4: Not used
        
        # GoodInput (4 bytes) - offset 178 - Input port status (we don't have input)
        packet[178] = 0x00
        packet[179] = 0x00
        packet[180] = 0x00
        packet[181] = 0x00
        
        # GoodOutputA (4 bytes) - offset 182 - Output port status
        # Bit 7: 1 = Data is being transmitted or able to transmit
        # Bit 6: 0 = LTP merge mode (0 = LTP)
        # Bit 5: 0 = DMX short detection not supported  
        # Bit 4: 0 = Merging not active
        # Bit 3: 0 = DMX text packets not supported
        # Bit 2: 0 = DMX SIP's not supported
        # Bit 1: 0 = DMX test packets not supported
        # Bit 0: 0 = Not in sACN mode
        packet[182] = 0x80  # Port 1: Ready to receive/output data
        packet[183] = 0x00
        packet[184] = 0x00
        packet[185] = 0x00
        
        # SwIn (4 bytes) - offset 186 - Input port universe (not used)
        packet[186] = 0x00
        packet[187] = 0x00
        packet[188] = 0x00
        packet[189] = 0x00
        
        # SwOut (4 bytes) - offset 190 - Output port universe
        # This is the low 4 bits of the Port-Address
        packet[190] = self.target_universe & 0x0F  # Port 1 universe
        packet[191] = 0x00
        packet[192] = 0x00
        packet[193] = 0x00
        
        # SwVideo (1 byte) - offset 194
        packet[194] = 0x00
        
        # SwMacro (1 byte) - offset 195
        packet[195] = 0x00
        
        # SwRemote (1 byte) - offset 196
        packet[196] = 0x00
        
        # Spare (3 bytes) - offset 197
        packet[197] = 0x00
        packet[198] = 0x00
        packet[199] = 0x00
        
        # Style (1 byte) - offset 200
        # 0x00 = StNode (DMX to/from Art-Net device)
        packet[200] = 0x00
        
        # MAC Address (6 bytes) - offset 201
        # Try to get actual MAC, or use zeros
        packet[201:207] = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        
        # BindIp (4 bytes) - offset 207 - Must match the IP we're advertising
        packet[207:211] = bytes(ip_parts)
        
        # BindIndex (1 byte) - offset 211
        packet[211] = 0x01
        
        # Status2 (1 byte) - offset 212
        # Bit 7: 0 = Node does NOT support Art-Net 4 sACN switching
        # Bit 6: 0 = Node does NOT support squawking (output on discovery only)
        # Bit 5: 0 = Node does NOT support switching output style using ArtCommand
        # Bit 4: 0 = Node does NOT support RDM switching via ArtCommand
        # Bit 3: 1 = Node supports 15-bit Port-Address (Art-Net 3 or 4)
        # Bit 2: 0 = Node is NOT DHCP capable
        # Bit 1: 0 = IP is manually configured (not DHCP)
        # Bit 0: 0 = Node does not support web browser configuration
        packet[212] = 0x08  # Just supports 15-bit addressing, no fancy features
        
        # GoodOutputB (4 bytes) - offset 213 - Additional output status (Art-Net 4)
        # Bit 7: 1 = Output is continuous (not delta)
        # Bit 6: 0 = Not RDM disabled
        # All zeros = default/simple behavior
        packet[213] = 0x80  # Continuous output
        packet[214] = 0x00
        packet[215] = 0x00
        packet[216] = 0x00
        
        # Status3 (1 byte) - offset 217 (Art-Net 4)
        # Bit 6: 0 = Node does NOT support failsafe
        # Bit 5-4: 00 = No fail-safe state
        # Bit 3: 0 = Does NOT support Llrp
        # Bit 2: 0 = Does NOT support switching output styles
        packet[217] = 0x00
        
        # DefaultResponder (6 bytes) - offset 218 (Art-Net 4)
        # UID of default responder - use zeros
        packet[218:224] = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        
        # Filler - rest is already zeros
        
        # Log the packet for debugging
        logger.debug(f"ArtPollReply packet ({len(packet)} bytes):")
        logger.debug(f"  IP: {ip_to_advertise}")
        logger.debug(f"  Universe: {self.target_universe}")
        logger.debug(f"  NetSwitch: {packet[18]}, SubSwitch: {packet[19]}, SwOut[0]: {packet[190]}")
        
        return bytes(packet)

    def _start_announce_timer(self):
        """Start the periodic announcement timer.
        
        Art-Net spec recommends nodes announce every 2.5-3 seconds.
        This ensures controllers discover us even if they started first.
        """
        if not self.running:
            return
        
        self.announce_timer = threading.Timer(self.announce_interval, self._announce_callback)
        self.announce_timer.daemon = True
        self.announce_timer.start()

    def _announce_callback(self):
        """Timer callback to broadcast our presence."""
        if not self.running:
            return
        
        try:
            self._send_poll_reply_broadcast()
        except Exception as e:
            logger.error(f"Announcement failed: {e}")
        
        # Schedule next announcement
        self._start_announce_timer()

    def _send_poll_reply(self, target_ip: str):
        """Send an ArtPollReply to a specific IP address."""
        if not self.sockets or not self.running:
            return

        # Lock onto an advertise IP once we see a controller
        if self.advertise_ip is None:
            self.advertise_ip = self._pick_advertise_ip_for_controller(target_ip)
            if self.advertise_ip:
                self.local_ip = self.advertise_ip
                logger.info(f"Advertising ONLY as {self.advertise_ip} (robust multi-NIC bind)")

        if not self.advertise_ip or self.advertise_ip not in self.sockets:
            return
        
        try:
            reply = self._build_poll_reply(bind_ip=self.advertise_ip)
            self.sockets[self.advertise_ip].sendto(reply, (target_ip, self.ARTNET_PORT))
            logger.debug(f"Sent ArtPollReply to {target_ip}")
        except Exception as e:
            logger.error(f"Failed to send ArtPollReply: {e}")

    def _send_poll_reply_broadcast(self):
        """Send an ArtPollReply broadcast to announce ourselves."""
        if not self.sockets or not self.running:
            return

        # Before we see a controller, broadcast from just the first bound IP
        if self.advertise_ip is None:
            self.advertise_ip = next(iter(self.sockets.keys()), None)
            if self.advertise_ip:
                self.local_ip = self.advertise_ip

        if not self.advertise_ip or self.advertise_ip not in self.sockets:
            return
        
        try:
            reply = self._build_poll_reply(bind_ip=self.advertise_ip)
            self.sockets[self.advertise_ip].sendto(reply, (self.ARTNET_BROADCAST, self.ARTNET_PORT))
            logger.debug(f"Broadcast ArtPollReply (Node: {self.local_ip}, Universe: {self.target_universe})")
        except Exception as e:
            logger.error(f"Failed to broadcast ArtPollReply: {e}")
