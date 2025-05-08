#!/usr/bin/env python3

import RPi.GPIO as GPIO
import time
import subprocess

# Set up GPIO
GPIO.setmode(GPIO.BOARD)
GPIO.setup(12, GPIO.IN)

# Track previous state to detect rising edge
prev_input = GPIO.input(12)

try:
    while True:
        current_input = GPIO.input(12)
        if current_input == 0 and prev_input == 1:
            print("Switch turned ON. Shutting down...")
            subprocess.call(["sudo", "shutdown", "now"])
            break  # Optional: exit loop after triggering shutdown
        prev_input = current_input
        time.sleep(0.1)

except KeyboardInterrupt:
    print("Script interrupted by user.")

finally:
    GPIO.cleanup()

