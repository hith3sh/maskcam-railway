import serial
import time

# Update the port name below based on your system
ser = serial.Serial('/dev/ttyUSB0', 115200)
time.sleep(2)

with open('esp32_data.txt', 'w') as f:
    while True:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        print(line)
        f.write(line + '\n')
        f.flush()  # Ensure it's written immediately

