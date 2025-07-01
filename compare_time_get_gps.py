import json
from datetime import datetime
from dateutil import parser as date_parser
import os
import re

stats_dir =  "/home/lab5/Desktop/inference_statistics"
gps_dir =  "/home/lab5/Desktop/gps_data"
stats_pattern = re.compile(r"inference_statistics_(\d{8})_(\d{6})\.json")
gps_pattern = re.compile(r"esp32_data_(\d{8})_(\d{6})\.txt")

def find_closest_file(stats_dir, pattern):
    pattern = re.compile(r"inference_statistics_(\d{8})_(\d{6})\.json")
    now = datetime.now()

    closest_file = None
    smallest_diff = None

    for filename in os.listdir(stats_dir):
        match = pattern.match(filename)
        if match:
            date_str, time_str = match.groups()
            file_time_str = date_str + time_str
            try:
                file_datetime = datetime.strptime(file_time_str, "%Y%m%d%H%M%S")
            except ValueError:
                continue

            time_diff = abs((now - file_datetime).total_seconds())
            if smallest_diff is None or time_diff < smallest_diff:
                smallest_diff = time_diff
                closest_file = filename

    return closest_file

# Load ESP32 GPS data
def load_gps_data(file_path):
    gps_data = []
    with open(file_path, 'r') as f:
        for line in f:
            if "Lat" in line and "Lon" in line and "Time" in line:
                parts = line.strip().split(',')
                lat = float(parts[0].split(':')[1].strip())
                lon = float(parts[1].split(':')[1].strip())
                time_str = parts[2].split(':', 1)[1].strip()
                time_obj = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                gps_data.append({'lat': lat, 'lon': lon, 'time': time_obj})
    return gps_data

# Load defective tracks
def load_defective_tracks(file_path):
    with open(file_path, 'r') as f:
        data = json.load(f)
    return data[0]['defective_tracks']

# Find closest GPS entry for a given detection time
def find_nearest_gps(detection_time, gps_data):
    min_diff = None
    nearest_entry = None
    for entry in gps_data:
        # Only consider entries with time AFTER detection_time
        if entry['time'] > detection_time:
            time_diff = (entry['time'] - detection_time).total_seconds()
            if (min_diff is None) or (time_diff < min_diff):
                min_diff = time_diff
                nearest_entry = entry
    return nearest_entry

# Main comparison logic
def main():
    # gps data file
    gps_txt_file = find_closest_file(stats_dir, gps_pattern)
    gps_txt_file_path = os.path.join(gps_dir, gps_txt_file)
    gps_data = load_gps_data(gps_txt_file_path)

    #inference stats file
    file_name = find_closest_file(stats_dir, stats_pattern)
    file_path = os.path.join(stats_dir, file_name)
    defective_tracks = load_defective_tracks(file_path)
    

    for track in defective_tracks:
        track_id = track['track_id']
        detection_time = date_parser.parse(track['detection_time'])
        nearest_gps = find_nearest_gps(detection_time, gps_data)

        if nearest_gps:
            print(f"Track ID: {track_id}")
            print(f"Detection Time: {detection_time}")
            print(f"Nearest GPS Time: {nearest_gps['time']}")
            print(f"Lat: {nearest_gps['lat']}, Lon: {nearest_gps['lon']}")
            print("-" * 40)

if __name__ == "__main__":
    main()

