#!/usr/bin/env python3

import os
import glob
from datetime import datetime
import RPi.GPIO as GPIO
import time
import subprocess
import signal

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

def main():
    # Set up GPIO
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(13, GPIO.IN)

    # Track previous state to detect rising edge
    prev_input = GPIO.input(13)

    try:
        while True:
            current_input = GPIO.input(13)
            if current_input == 0 and prev_input == 1:
                print("Switch turned ON. Sending SIGINT (Ctrl+C)...")

                # Find the closest maskcam_run.py PID using the PID file logic
                result = find_closest_pidfile()
                if result:
                    _, pid = result
                    os.kill(int(pid), signal.SIGINT)
                    print(f"Sent SIGINT to process {pid} (maskcam_run.py)")
                else:
                    print("No running maskcam_run.py process found via PID file.")

                time.sleep(3)

                # Run the 'compare_time_get_gps.py' script
                print("Running compare_time_get_gps.py...")
                subprocess.Popen(["python3", "compare_time_get_gps.py"])

                # Delay before running the second script
                time.sleep(1) 

                # Run the 'send_data_telegraf.py' script
                print("Running send_data_telegraf.py...")
                subprocess.Popen(["python3", "send_data_telegraf.py"])

                break

            prev_input = current_input
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("Script interrupted by user.")

    finally:
        GPIO.cleanup()

if __name__ == "__main__":
    main()
