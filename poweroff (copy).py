#!/usr/bin/env python3

import RPi.GPIO as GPIO
import time
import subprocess
import os
import signal

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

            # Find the process ID of the script you want to send SIGINT to.
            # Assuming you want to interrupt 'compare.py' if it's running.
            # Adjust the name of the script you need to interrupt, or improve the process finding logic.
            pid = subprocess.check_output(["pgrep", "-f", "maskcam_run.py"]).decode("utf-8").strip()

            # Send SIGINT (Ctrl+C) to that process
            if pid:
                os.kill(int(pid), signal.SIGINT)
                print(f"Sent SIGINT to process {pid} (maskcam_run.py)")

            # Run the 'compare.py' script
            print("Running compare.py...")
            subprocess.Popen(["python3", "compare_time_get_gps.py"])

            # Delay before running the second script
            time.sleep(3)  # Adjust delay as needed

            # Run the 'send_data_to_telegrf.py' script
            print("Running send_data_to_telegrf.py...")
            subprocess.Popen(["python3", "send_data_telegraf.py"])

            break  # Optional: exit loop after triggering actions

        prev_input = current_input
        time.sleep(0.1)

except KeyboardInterrupt:
    print("Script interrupted by user.")

finally:
    GPIO.cleanup()

