import re
import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import make_interp_spline


series = {
    "Low Load": {
        #0.0001: "long_s4_d2_0.0001.log",
        #0.001:  "long_s4_d2_0.001.log",
        0.01:   "long_s4_d2_0.01.log",
        0.1:    "long_s4_d2_0.1.log",
        1 :     "long_s4_d2_1.log",
        10:     "long_s4_d2_10.log"
    },

    "High Load": {
       # 0.0001: "long_s4_d2_0.0001_high.log",
       # 0.001:  "long_s4_d2_0.001_high.log",
        0.01:   "long_s4_d2_0.01_high_.log",
        0.1:    "long_s4_d2_0.1_high_.log",
        1 :     "long_s4_d2_1_high_.log",
        10:     "long_s4_d2_10_high.log"
    },

    "Normal Load": {   # ← ADDING YOUR THIRD CLASS
        #0.0001: "long_s4_d2_0.0001_normal.log",
        #0.001:  "long_s4_d2_0.001_normal.log",
        0.01:   "long_s4_d2_0.01_high_normal.log",
        0.1:    "long_s4_d2_0.1_high_normal.log",
        1 :     "long_s4_d2_1_high_normal.log",
        10:     "long_s4_d2_10_high_normal.log"
    }
}

# ==============================================================
#                BYTE PARSER (NO printing)
# ==============================================================

pattern = re.compile(
    r"(\d+\.\d+)-(\d+\.\d+)\s+sec\s+([\d\.]+)\s+([KMG])Bytes"
)

def extract_avg_bytes_per_sec(file):
    intervals = []

    with open(file) as f:
        for line in f:
            m = pattern.search(line)
            if m:
                start = float(m.group(1))
                end   = float(m.group(2))
                val   = float(m.group(3))
                unit  = m.group(4)

                # Convert units → bytes
                if unit == "K":
                    val *= 1024
                elif unit == "M":
                    val *= 1024 * 1024
                elif unit == "G":
                    val *= 1024 * 1024 * 1024

                interval_sec = end - start
                if interval_sec > 0:
                    intervals.append(val / interval_sec)  # bytes/sec

    if not intervals:
        return 0.0

    return np.mean(intervals)


# ==============================================================
#                   LOAD SERIES
# ==============================================================

def load_series(log_dict):
    errs = []
    gps = []

    for err, file in log_dict.items():
        errs.append(err)
        gps.append(extract_avg_bytes_per_sec(file))

    return np.array(errs), np.array(gps)


# ==============================================================
#                   SMOOTHING (SPLINE)
# ==============================================================

def smooth_curve(errs, gps):
    logX = np.log10(errs)
    splineX = np.linspace(logX.min(), logX.max(), 300)
    spline = make_interp_spline(logX, gps, k=3)
    smoothY = spline(splineX)
    return 10 ** splineX, smoothY


# ==============================================================
#                       PLOTTING
# ==============================================================

plt.figure(figsize=(10, 5))

for label, logs in series.items():
    errs, gps = load_series(logs)
    xs, ys = smooth_curve(errs, gps)
    plt.plot(xs, ys, linewidth=2, label=label)
    plt.scatter(errs, gps, s=40)

plt.xscale("log")
plt.xlabel("Wireless Link Error Probability", fontsize=12)
plt.ylabel("Bytes/sec", fontsize=12)
plt.title("Goodput (Bytes/sec) vs Wireless Error Probability", fontsize=14)
plt.grid(True, which="both", linestyle="--", alpha=0.6)
plt.legend()
plt.tight_layout()
plt.savefig("bytes_vs_error_smooth.png")
plt.close()
