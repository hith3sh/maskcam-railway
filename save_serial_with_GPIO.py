#!/usr/bin/env python3

import RPi.GPIO as GPIO
import serial
import time

# Setup GPIO
GPIO.setmode(GPIO.BOARD)
GPIO.setup(13, GPIO.IN)  # Pin 12 = GPIO18

# Setup Serial
ser = serial.Serial('/dev/ttyUSB0', 115200)
time.sleep(2)

# Store previous GPIO state
prev_input = GPIO.input(13)

try:
    with open('esp32_data.txt', 'w') as f:
        while True:
            # Check GPIO pin state
            current_input = GPIO.input(13)
            if current_input == 0 and prev_input == 1:  # Detect falling edge (change to rising if needed)
                print("GPIO pin triggered. Stopping serial read.")
                break
            prev_input = current_input

            # Read from serial
            if ser.in_waiting:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                print(line)
                f.write(line + '\n')
                f.flush()

            time.sleep(0.1)  # Avoid busy loop

except KeyboardInterrupt:
    print("Interrupted by user.")

finally:
    GPIO.cleanup()
    ser.close()

