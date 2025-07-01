import serial
import time
from datetime import datetime
import os

# Update the port name below based on your system
ser = serial.Serial('/dev/ttyUSB0', 115200)
time.sleep(2)

# Set output directory
output_dir = '/home/lab5/Desktop/gps_data'
os.makedirs(output_dir, exist_ok=True)  # Create the directory if it doesn't exist

# Generate filename with current date and time
timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
filename = f'esp32_data_{timestamp}.txt'
file_path = os.path.join(output_dir, filename)

with open(file_path, 'w') as f:
    while True:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        print(line)
        f.write(line + '\n')
        f.flush()  # Ensure it's written immediately

