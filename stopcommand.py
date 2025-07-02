import os
import glob
from datetime import datetime

def find_closest_pidfile(directory="/tmp", prefix="maskcam_run_", suffix=".pid"):
    # List all matching pid files
    pattern = os.path.join(directory, f"{prefix}*{suffix}")
    pidfiles = glob.glob(pattern)
    if not pidfiles:
        print("No PID files found.")
        return None

    # Parse timestamps and find the closest
    now = datetime.now()
    closest_file = None
    closest_time = None
    min_diff = None

    for pidfile in pidfiles:
        # Extract timestamp from filename
        basename = os.path.basename(pidfile)
        try:
            timestamp_str = basename[len(prefix):-len(suffix)]
            file_time = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            diff = abs((now - file_time).total_seconds())
            if min_diff is None or diff < min_diff:
                min_diff = diff
                closest_time = file_time
                closest_file = pidfile
        except Exception as e:
            print(f"Skipping file {pidfile}: {e}")

    if closest_file:
        print(f"Closest PID file: {closest_file} (timestamp: {closest_time})")
        with open(closest_file, "r") as f:
            pid = int(f.read().strip())
        print(f"PID in file: {pid}")
        return closest_file, pid
    else:
        print("No valid PID files found.")
        return None

if __name__ == "__main__":
    find_closest_pidfile()