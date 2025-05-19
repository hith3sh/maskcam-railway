import requests

# Path to your line protocol text file
file_path = "/home/lab5/Desktop/inference_statistics/inference_stats.json"

# Telegraf listener endpoint
telegraf_url = "http://localhost:8186/telegraf"

# Read the file and send its contents
with open(file_path, 'r') as file:
    data = file.read()

response = requests.post(
    telegraf_url,
    headers={"Content-Type": "text/plain"},
    data=data
)

# Check response
if response.status_code == 204:
    print(" Data sent successfully to Telegraf.")
else:
    print(f" Failed to send data: {response.status_code}")
    print(response.text)

