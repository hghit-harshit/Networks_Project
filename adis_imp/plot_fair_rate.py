import matplotlib
# Force matplotlib to not use any Xwindows backend (prevents "no display name" errors)
matplotlib.use('Agg') 

import matplotlib.pyplot as plt
import re
from datetime import datetime
import matplotlib.ticker as ticker
plt.gca().xaxis.set_major_locator(ticker.MultipleLocator(10))  # every 10 seconds
# Configuration
LOG_FILE = "fair_raw.log"
OUTPUT_IMAGE = "fan_fair_rate.png"  # Name of the output file
CAPACITY_MBPS = 100 

def parse_log_and_plot():
    timestamps = []
    fair_rates = []
    
    # Regex to capture Timestamp and Fair Rate
    pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*\[FAIR-RAW\].*measured_fair_rate=([\d\.]+)Mb/s"
    )

    print(f"Reading {LOG_FILE}...")
    try:
        with open(LOG_FILE, 'r') as f:
            for line in f:
                match = pattern.search(line)
                if match:
                    ts_str = match.group(1)
                    rate_str = match.group(2)
                    
                    # Parse timestamp
                    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f")
                    timestamps.append(dt)
                    fair_rates.append(float(rate_str))
    except FileNotFoundError:
        print(f"Error: Could not find file '{LOG_FILE}'. Make sure it exists.")
        return

    if not timestamps:
        print("No [FAIR-RAW] data found in the log file.")
        return

    # Convert absolute timestamps to "seconds from start"
    start_time = timestamps[0]
    time_deltas = [(t - start_time).total_seconds()  for t in timestamps]
#plt.xlabel("Time (minutes)")


    # Plotting
    plt.figure(figsize=(12, 6))
    
    # Plot the Fair Rate
    plt.plot(time_deltas, fair_rates, label='Measured Fair Rate', color='blue', linewidth=2)
    #ax = plt.gca()
    #ax.xaxis.set_major_locator(ticker.MultipleLocator(1))  # 1 minute per tick

    # Plot the Capacity Line (Static)
    plt.axhline(y=CAPACITY_MBPS, color='red', linestyle='--', alpha=0.7, label=f'Link Capacity ({CAPACITY_MBPS} Mbps)')

    # Formatting
    plt.title("Fair Rate vs Time (FAN Topology)", fontsize=14)
    plt.xlabel("Time (seconds)", fontsize=12)
    plt.ylabel("Fair Rate (Mbps)", fontsize=12)
    plt.grid(True, linestyle=':', alpha=0.6)
    plt.legend()
    
    # --- CHANGE IS HERE ---
    print(f"Plotting {len(fair_rates)} data points...")
    plt.tight_layout()
    
    # Save to file
    plt.savefig(OUTPUT_IMAGE)
    print(f"Success! Graph saved to: {OUTPUT_IMAGE}")
    
    # Close memory to prevent leaks
    plt.close() 

if __name__ == "__main__":
    parse_log_and_plot()