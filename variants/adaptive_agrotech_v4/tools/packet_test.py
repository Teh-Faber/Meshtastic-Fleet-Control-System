# Send a test text and position, then list packets and nodes heard.
# Usage: python packet_test.py COM_PORT SECONDS
import sys, time
import meshtastic.serial_interface
from pubsub import pub

port, secs = sys.argv[1], int(sys.argv[2])
rx = []

def on_rx(packet, interface):
    d = packet.get("decoded", {})
    rx.append(packet)
    print(f"RX from={packet.get('fromId')} port={d.get('portnum')} snr={packet.get('rxSnr')} rssi={packet.get('rxRssi')} hop={packet.get('hopStart')}->{packet.get('hopLimit')} text={d.get('text')!r}", flush=True)

pub.subscribe(on_rx, "meshtastic.receive")
iface = meshtastic.serial_interface.SerialInterface(port)
me = iface.getMyNodeInfo()
print("ME", me["user"]["id"], me["user"].get("longName"), flush=True)
print("NODES before", len(iface.nodes or {}), flush=True)
p = iface.sendText("AgroTech v4 test", wantAck=False)
print("TX text id", p.id, flush=True)
iface.sendPosition()  # no fix; still exercises TX
end = time.time() + secs
while time.time() < end:
    time.sleep(1)
print("NODES after", len(iface.nodes or {}), flush=True)
for nid, n in (iface.nodes or {}).items():
    print(" ", nid, n.get("user", {}).get("longName"), "snr", n.get("snr"), "lastHeard", n.get("lastHeard"), "hops", n.get("hopsAway"))
m = (iface.nodes or {}).get(me["user"]["id"], {}).get("deviceMetrics", {})
print("METRICS", m)
print("RX total", len(rx))
iface.close()
