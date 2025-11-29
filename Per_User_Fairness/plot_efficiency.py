import re
import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import make_interp_spline


series = {
    "User Fair Controller Low Load": {
        #0.0001: "long_s4_d2_0.0001.log",
        #0.001:  "long_s4_d2_0.001.log",
        0.01:   "long_s4_d2_0.01.log",
        0.1:    "long_s4_d2_0.1.log",
        1 :     "long_s4_d2_1.log",
        10:     "long_s4_d2_10.log"
    },

    "User Fair Controller High Load": {
       # 0.0001: "long_s4_d2_0.0001_high.log",
       # 0.001:  "long_s4_d2_0.001_high.log",
        0.01:   "long_s4_d2_0.01_high_.log",
        0.1:    "long_s4_d2_0.1_high_.log",
        1 :     "long_s4_d2_1_high_.log",
        10:     "long_s4_d2_10_high.log"
    },

    "Normal Controller High Load": {   
        #0.0001: "long_s4_d2_0.0001_normal.log",
        #0.001:  "long_s4_d2_0.001_normal.log",
        0.01:   "long_s4_d2_0.01_high_normal.log",
        0.1:    "long_s4_d2_0.1_high_normal.log",
        1 :     "long_s4_d2_1_high_normal.log",
        10:     "long_s4_d2_10_high_normal.log"
    }
}
# Wireless link capacity
LINK_CAP_BYTES_PER_SEC = 2 * 1024 * 1024 # convert Mbps → bytes/sec



pattern = re.compile(
    r"(\d+\.\d+)-(\d+\.\d+)\s+sec\s+([\d\.]+)\s+([KMG])Bytes"
)

def extract_goodput_bytes_per_sec(file):
    intervals = []

    with open(file) as f:
        for line in f:
            m = pattern.search(line)
            if not m:
                continue

            start = float(m.group(1))
            end   = float(m.group(2))
            val   = float(m.group(3))
            unit  = m.group(4)

            # Convert units
            if unit == "K":
                val *= 1024
            elif unit == "M":
                val *= 1024 * 1024
            elif unit == "G":
                val *= 1024 * 1024 * 1024

            dt = end - start
            if dt > 0:
                intervals.append(val / LINK_CAP_BYTES_PER_SEC)  # bytes/sec

    if not intervals:
        return 0.0
    return np.mean(intervals)
   



def load_series(mapping):
    errs = []
    gps  = []

    for err, file in mapping.items():
        errs.append(err)
        gps.append(extract_goodput_bytes_per_sec(file))

    return np.array(errs), np.array(gps)



def smooth(errs, gps):
    logX = np.log10(errs)
    Xsmooth = np.linspace(logX.min(), logX.max(), 300)
    spline = make_interp_spline(logX, gps, k=3)
    Ys = spline(Xsmooth)
    return 10 ** Xsmooth, Ys



plt.figure(figsize=(10,5))

for label, logs in series.items():
    errs, gps = load_series(logs)
    xs, ys = smooth(errs, gps)
    plt.plot(xs, ys, linewidth=2, label=f"{label}")
    plt.scatter(errs, gps, s=40)

plt.xscale("log")
plt.xlabel("Wireless Link Error Probability", fontsize=12)
plt.ylabel("Goodput (Bytes/sec)", fontsize=12)
plt.title("Goodput vs Wireless Error Probability (Smooth)")
plt.grid(True, which="both", linestyle="--", alpha=0.6)
plt.legend()
plt.tight_layout()
plt.savefig("goodput_bytes_smooth.png")
plt.close()


plt.figure(figsize=(10,5))

for label, logs in series.items():
   errs, gps = load_series(logs)
   

    # Compute efficiency
   eff = gps 

#    if label == "Normal Controller High Load":
#         eff = eff / eff[0]
#         #desired_start = 0.4
#         #scale_factor = desired_start / start_eff
#         #eff = eff * scale_factor
#         # Optional: keep it below others
#         eff = eff * 0.4
   xs, ys = smooth(errs, eff)
   plt.plot(xs, ys, linewidth=2, label=label)
   plt.scatter(errs, eff, s=40)

plt.xscale("log")
plt.xlabel("Wireless Packet loss rate", fontsize=12)
plt.ylabel("Efficiency (Goodput / Link Capacity)", fontsize=12)
plt.title("Efficiency vs Wireless Packet loss rate (Smooth)")
plt.grid(True, which="both", linestyle="--", alpha=0.6)
plt.legend()
plt.tight_layout()
plt.savefig("efficiency_smooth.png")
plt.close()
