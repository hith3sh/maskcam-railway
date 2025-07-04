import serial
import time
from datetime import datetime
import os
import multiprocessing as mp

def main(config=None, e_external_interrupt=None):
    # Set a timeout of 1 second on the serial port
    ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=1) 
    time.sleep(2)

    # Set output directory
    output_dir = '/home/lab5/Desktop/gps_data'
    os.makedirs(output_dir, exist_ok=True)  # Create the directory if it doesn't exist

    # Generate filename with current date and time
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    filename = f'esp32_data_{timestamp}.txt'
    file_path = os.path.join(output_dir, filename)


    try:
        with open(file_path, 'w') as f:
            while True:
                # 1. Check for the interrupt signal FIRST.
                if e_external_interrupt is not None and e_external_interrupt.is_set():
                    print("Interrupt received, stopping serial capture.")
                    break # Exit the loop gracefully

                # 2. Try to read a line. This will wait a maximum of 1 second.
                line = ser.readline()

                # 3. If a line was actually received, process it.
                if line:
                    decoded_line = line.decode('utf-8', errors='ignore').strip()
                    if decoded_line: # Ensure it's not an empty line
                        print(decoded_line)
                        f.write(decoded_line + '\n')
                        f.flush()

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        ser.close() # Always ensure the serial port is closed.
        print(f"File saved and serial port closed.")

if __name__ == "__main__":
    main()