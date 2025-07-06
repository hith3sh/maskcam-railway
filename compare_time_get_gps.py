import json
from datetime import datetime
from dateutil import parser as date_parser
import os
import re

stats_dir ="/home/lab5/Desktop/inference_statistics"
gps_dir ="/home/lab5/Desktop/gps_data"
stats_pattern = re.compile(r"inference_statistics_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})\.json")
gps_pattern = re.compile(r"esp32_data_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})\.txt")

def find_closest_file(directory, pattern):
    now = datetime.now()
    closest_file = None
    smallest_diff = None

    for filename in os.listdir(directory):
        match = pattern.match(filename)
        if match:
            date_str, time_str = match.groups()
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


def load_gps_data(file_path):
    gps_data = []
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            if '@' not in line:
                continue  # skip invalid lines

            try:
                prefix, rest = line.split(':', 1)
                coords_part, time_part = rest.split('@')
                lat_str, lon_str = coords_part.strip().split(',')
                time_str = time_part.strip()

                lat = float(lat_str.strip())
                lon = float(lon_str.strip())
                time_obj = datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S")

                gps_data.append({
                    'lat': lat,
                    'lon': lon,
                    'time': time_obj
                })
            except (ValueError, IndexError):
                continue  # skip lines that don't match the expected format
    return gps_data


def load_defective_tracks(file_path):
    """
    Load tracks from JSON, flattening any level of nested lists.
    Supports:
      - { 'defective_tracks': [...] }
      - [ {...}, {...} ]
      - nested lists like [ [ {...} ], [ {...} ] ]
    """
    with open(file_path, 'r') as f:
        data = json.load(f)

    # Extract the list under key if present
    if isinstance(data, dict) and 'defective_tracks' in data:
        data_list = data['defective_tracks']
    else:
        data_list = data

    # Recursively flatten lists to a list of dicts
    def flatten(obj):
        flat = []
        if isinstance(obj, list):
            for item in obj:
                flat.extend(flatten(item))
        elif isinstance(obj, dict):
            flat.append(obj)
        return flat

    return flatten(data_list)

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

def main():
    # gps data file
    gps_txt_file = find_closest_file(gps_dir, gps_pattern)
    # check if there is no file
    if gps_txt_file is None:
        print(f"Error: No GPS data file found in {gps_dir}")
        return
    gps_txt_file_path = os.path.join(gps_dir, gps_txt_file)
    
    #  Check if the GPS file is empty
    if os.stat(gps_txt_file_path).st_size == 0:
        print(f"GPS file {gps_txt_file} is empty. Aborting.")
        return
    gps_data = load_gps_data(gps_txt_file_path)

    #inference stats file
    file_name = find_closest_file(stats_dir, stats_pattern)
    if file_name is None:
        print(f"Error: No inference statistics file found in directory: {stats_dir}")
        return
    file_path = os.path.join(stats_dir, file_name)
    defective_tracks = load_defective_tracks(file_path)
    
    # prepare output directory and file
    output_dir = "/home/lab5/Desktop/final_data"
    os.makedirs(output_dir, exist_ok=True)
    output_filename = f"matched_gps_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    output_path = os.path.join(output_dir, output_filename)
    
    with open(output_path, 'w') as out_file:
        for track in defective_tracks:
            detection_time = date_parser.parse(track['detection_time'])
            nearest_gps = find_nearest_gps(detection_time, gps_data)

            if nearest_gps:
                measurement = "inference_result"
                tag_part = f"track_id={track['track_id']}"
                field_part = (
                    f"confidence={track.get('confidence', 0)},"
                    f"matched_lat={nearest_gps['lat']},"
                    f"matched_lon={nearest_gps['lon']}"
                )
                timestamp_ns = int(nearest_gps['time'].timestamp() * 1e9)

                output_text = f"{measurement},{tag_part} {field_part} {timestamp_ns}\n"
                out_file.write(output_text)
            else:
                print("No matching GPS found for current track")           

    print(f"File {output_filename} is written to {output_dir} successfully!")

if __name__ == "__main__":
    main()

