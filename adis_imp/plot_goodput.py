# import re
# import numpy as np
# import matplotlib.pyplot as plt
# from scipy.interpolate import make_interp_spline
# import math

# # ============================
# # EDIT THESE FILENAMES
# # ============================
# log_files = {
#     0.0001: "long_s4_d2_0.0001.log",
#     0.001:  "long_s4_d2_0.001.log",
#     0.01:   "long_s4_d2_0.01.log",
#     0.1:    "long_s4_d2_0.1.log",
#     1.0:    "long_s4_d2_1.log",
#     10.0:   "long_s4_d2_10.log"
# }

# def extract_goodput(logfile):
#     rates = []

#     pattern = re.compile(
#         r"\d+\.\d+-\d+\.\d+\s+sec\s+\S+\s+\S+\s+([\d\.]+)\s+([KMG])bits/sec"
#     )

#     with open(logfile) as f:
#         for line in f:
#             m = pattern.search(line)
#             if m:
#                 val = float(m.group(1))
#                 unit = m.group(2)

#                 # Normalize to Mbps
#                 if unit == "M":
#                     rates.append(val)
#                 elif unit == "K":
#                     rates.append(val / 1000.0)
#                 elif unit == "G":
#                     rates.append(val * 1000.0)

#     if not rates:
#         print(f"[WARN] No matches in {logfile}")
#         return 0.0

#     return np.mean(rates)


# # ============================
# # PROCESS LOGS
# # ============================

# errors = []
# goodputs = []

# for err, file in log_files.items():
#     print(f"Processing {file} ...")
#     g = extract_goodput(file)
#     print(f"  Error={err}, Goodput={g:.3f} Mbps")
#     errors.append(err)
#     goodputs.append(g)

# errors = np.array(errors)
# goodputs = np.array(goodputs)

# # ============================
# # SMOOTH CURVE (SPLINE)
# # ============================

# # log-scale interpolation: fit spline in log10(error) space
# logX = np.log10(errors)
# X_smooth = np.linspace(logX.min(), logX.max(), 200)

# # cubic spline
# spline = make_interp_spline(logX, goodputs, k=3)
# Y_smooth = spline(X_smooth)

# # convert smooth X back from log space
# X_smooth_plot = 10 ** X_smooth


# # ============================
# # PLOT
# # ============================

# plt.figure(figsize=(10,5))

# # smooth line
# plt.plot(X_smooth_plot, Y_smooth, linewidth=2, label="Smoothed Curve")

# # original points
# plt.scatter(errors, goodputs, color='blue', s=60, label="Measured Points")

# plt.xscale("log")
# plt.xlabel("Wireless Link Error Probability(%)", fontsize=12)
# plt.ylabel("Goodput (Mbps)", fontsize=12)
# plt.title("Goodput vs Wireless Link Error Probability", fontsize=14)
# plt.grid(True, which="both", linestyle="--", alpha=0.6)
# plt.legend()

# plt.tight_layout()
# plt.savefig("goodput_vs_error_smooth.png")
# plt.close()

# print("\nGraph saved as goodput_vs_error_smooth.png")

import re
import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import make_interp_spline

# ============================
# EDIT THESE FILENAMES
# ============================

low_logs = {
    #0.0001: "long_s4_d2_0.0001.log",
    #0.001:  "long_s4_d2_0.001.log",
    0.01:   "long_s4_d2_0.01.log",
    0.1:    "long_s4_d2_0.1.log",
    1 :     "long_s4_d2_1.log",
    10:     "long_s4_d2_10.log",
}

high_logs = {
    #0.0001: "long_s4_d2_0.0001_high.log",
    #0.01:  "long_s4_d2_0.001_high.log",
    0.01:   "long_s4_d2_0.01_high_.log",
    0.1:    "long_s4_d2_0.1_high_.log",
    1 :     "long_s4_d2_1_high_.log",
    10:     "long_s4_d2_10_high.log",
}


# ============================
# GOODPUT PARSER
# ============================

pattern = re.compile(
    r"\d+\.\d+-\d+\.\d+\s+sec\s+\S+\s+\S+\s+([\d\.]+)\s+([KMG])bits/sec"
)

def extract_goodput(fname):
    rates = []
    with open(fname) as f:
        for line in f:
            m = pattern.search(line)
            if m:
                val = float(m.group(1))
                unit = m.group(2)
                if unit == "M":
                    rates.append(val)
                elif unit == "K":
                    rates.append(val / 1000)
                elif unit == "G":
                    rates.append(val * 1000)
    if not rates:
        return 0
    return np.mean(rates)


# ============================
# LOAD DATASET
# ============================

def load_series(log_dict):
    errs = []
    gps  = []

    for err, file in log_dict.items():
        gp = extract_goodput(file)
        errs.append(err)
        gps.append(gp)
        print(f"{file}  GP={gp:.3f} Mbps")

    return np.array(errs), np.array(gps)


low_err, low_gp = load_series(low_logs)
high_err, high_gp = load_series(high_logs)


# ============================
# SMOOTH CURVES (YOUR SPLINE)
# ============================

def smooth(errors, goodputs):
    logX = np.log10(errors)
    Xsmooth = np.linspace(logX.min(), logX.max(), 300)
    spline = make_interp_spline(logX, goodputs, k=3)  # <-- YOUR SPLINE
    Ys = spline(Xsmooth)
    return 10 ** Xsmooth, Ys


low_xs, low_ys = smooth(low_err, low_gp)
high_xs, high_ys = smooth(high_err, high_gp)


# ============================
# PLOT
# ============================

plt.figure(figsize=(10,5))

plt.plot(low_xs, low_ys, label="Low Load (Smooth)", linewidth=2)
plt.scatter(low_err, low_gp, s=50)

plt.plot(high_xs, high_ys, label="High Load (Smooth)", linewidth=2)
plt.scatter(high_err, high_gp, s=50)

plt.xscale("log")
plt.xlabel("Wireless Link Error Probability", fontsize=12)
plt.ylabel("Goodput (Mbps)", fontsize=12)
plt.title("Goodput vs Wireless Link Error Probability (Low vs High Load)")
plt.grid(True, which="both", linestyle="--", alpha=0.6)
plt.legend()
plt.tight_layout()
plt.savefig("goodput_low_vs_high_smooth.png")
plt.close()

print("\nSaved: goodput_low_vs_high_smooth.png")
