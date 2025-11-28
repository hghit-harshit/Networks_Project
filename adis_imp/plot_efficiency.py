import re
import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import make_interp_spline

# ==============================================================
# CONFIGURE DATASETS (ADD YOUR FILES HERE)
# ==============================================================

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

    "Normal Controller High Load": {   # ← ADDING YOUR THIRD CLASS
        #0.0001: "long_s4_d2_0.0001_normal.log",
        #0.001:  "long_s4_d2_0.001_normal.log",
        0.01:   "long_s4_d2_0.01_high_normal.log",
        0.1:    "long_s4_d2_0.1_high_normal.log",
        1 :     "long_s4_d2_1_high_normal.log",
        10:     "long_s4_d2_10_high_normal.log"
    }
}
# Wireless link capacity (R2 → D2 = L4 = 5 Mbps)
LINK_CAP_BYTES_PER_SEC = 5 * 1024 * 1024 / 8  # convert Mbps → bytes/sec

# ==============================================================
# BYTE PARSER (NO PRINTING)
# ==============================================================

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
                intervals.append(val / dt)  # bytes/sec

    if not intervals:
        return 0.0

    return np.mean(intervals)

# ==============================================================
# LOAD SERIES
# ==============================================================

def load_series(mapping):
    errs = []
    gps  = []

    for err, file in mapping.items():
        errs.append(err)
        gps.append(extract_goodput_bytes_per_sec(file))

    return np.array(errs), np.array(gps)

# ==============================================================
# SMOOTHING (CUBIC SPLINE)
# ==============================================================

def smooth(errs, gps):
    logX = np.log10(errs)
    Xsmooth = np.linspace(logX.min(), logX.max(), 300)
    spline = make_interp_spline(logX, gps, k=3)
    Ys = spline(Xsmooth)
    return 10 ** Xsmooth, Ys

# ==============================================================
#         PLOT GOODPUT (bytes/sec)
# ==============================================================

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

# ==============================================================
#         PLOT EFFICIENCY = goodput/link_capacity
# ==============================================================

plt.figure(figsize=(10,5))

for label, logs in series.items():
   errs, gps = load_series(logs)

    # --- ADJUST NORMAL LOAD CURVE ---
   

    # Compute efficiency
   eff = gps / LINK_CAP_BYTES_PER_SEC

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
