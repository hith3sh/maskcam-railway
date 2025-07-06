import os
import re
import requests
from datetime import datetime

# Directory and file pattern
final_data_dir = "/home/lab5/Desktop/final_data"
final_data_pattern = re.compile(r"matched_gps_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})\.txt")

# Telegraf listener endpoint
telegraf_url = "http://localhost:8186/telegraf"

def find_closest_file(directory, pattern):
    now = datetime.now()
    closest_file = None
    smallest_diff = None

    for filename in os.listdir(directory):
        match = pattern.match(filename)
        if match:
            date_str, time_str = match.groups()
            #file_time_str = date_str + time_str
            file_time_str = date_str + ' ' + time_str
            try:
                file_datetime = datetime.strptime(file_time_str, "%Y-%m-%d %H-%M-%S")
            except ValueError:
                continue

            time_diff = abs((now - file_datetime).total_seconds())
            if smallest_diff is None or time_diff < smallest_diff:
                smallest_diff = time_diff
                closest_file = filename

    return closest_file

# Find latest matched file
matched_file = find_closest_file(final_data_dir, final_data_pattern)
if not matched_file:
    print("No matched file found.")
    exit()

file_path = os.path.join(final_data_dir, matched_file)
print("Sending file:", file_path)

# Read and send
with open(file_path, 'r') as file:
    data = file.read()

response = requests.post(
    telegraf_url,
    headers={"Content-Type": "text/plain"},
    data=data
)

# Check response
if response.status_code == 204:
    print("Data sent successfully to Telegraf.")
else:
    print(f"Failed to send data: {response.status_code}")
    print(response.text)

