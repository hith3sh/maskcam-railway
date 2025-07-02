#!/usr/bin/env python3

import os
import sys
import json
import shutil
import signal
import threading
import multiprocessing as mp
from rich.console import Console
from datetime import datetime, timedelta
import queue
import threading

from maskcam.prints import print_run as print
from maskcam.config import config, print_config_overrides
from maskcam.common import USBCAM_PROTOCOL, RASPICAM_PROTOCOL
from maskcam.common import (
    CMD_FILE_SAVE,
    CMD_STREAMING_START,
    CMD_STREAMING_STOP,
    CMD_INFERENCE_RESTART,
    CMD_FILESERVER_RESTART,
    CMD_STATUS_REQUEST,
)
from maskcam.utils import (
    get_ip_address,
    ADDRESS_UNKNOWN_LABEL,
    load_udp_ports_filesaving,
    get_streaming_address,
    format_tdelta,
)
from maskcam.maskcam_inference import main as inference_main
from maskcam.maskcam_filesave import main as filesave_main
from maskcam.maskcam_fileserver import main as fileserver_main
from maskcam.maskcam_streaming import main as streaming_main
from maskcam.save_serial import main as save_serial_main

udp_ports_pool = set()
console = Console()
# Use threading.Event instead of mp.Event() for sigint_handler, see:
# https://bugs.python.org/issue41606
e_interrupt = threading.Event()
q_commands = mp.Queue(maxsize=4)
active_filesave_processes = []

P_INFERENCE = "inference"
P_STREAMING = "streaming"
P_FILESERVER = "file-server"
P_FILESAVE_PREFIX = "file-save-"
P_SAVESERIAL = "save-serial"

processes_info = {}
all_grass_statistics = [] # New list to store grass events from the queue

def write_statistics_async(stats_dir, data, stats_file_name):
    try:
        stats_file = os.path.join(stats_dir, stats_file_name)
        if os.path.exists(stats_file):
            with open(stats_file, 'r') as f:
                existing_data = json.load(f)
        else:
            existing_data = []
        existing_data.extend(data)
        print('Writing statistics to the json file')
        with open(stats_file, 'w') as f:
            json.dump(existing_data, f, indent=2, default=str)
    except Exception as e:
        print(f"Async write error: {str(e)}")

def sigint_handler(sig, frame):
    print("[red]Ctrl+C pressed. Interrupting all processes...[/red]")
    e_interrupt.set()


def start_process(name, target_function, config, **kwargs):
    e_interrupt_process = mp.Event()
    process = mp.Process(
        name=name,
        target=target_function,
        kwargs=dict(
            e_external_interrupt=e_interrupt_process,
            config=config,
            **kwargs,
        ),
    )
    processes_info[name] = {"started": datetime.now(), "running": True}
    process.start()
    print(f"Process [yellow]{name}[/yellow] started with PID: {process.pid}")
    return process, e_interrupt_process


def terminate_process(name, process, e_interrupt_process, delete_info=False):
    print(f"Sending interrupt to {name} process")
    e_interrupt_process.set()
    print(f"Waiting for process [yellow]{name}[/yellow] to terminate...")
    process.join(timeout=10)
    if process.is_alive():
        print(
            f"[red]Forcing termination of process:[/red] [bold]{name}[/bold]",
            warning=True,
        )
        process.terminate()
    if name in processes_info:
        if delete_info:
            del processes_info[name]  # Sequential processes, avoid filling memory
        else:
            processes_info[name].update({"ended": datetime.now(), "running": False})
    print(f"Process terminated: [yellow]{name}[/yellow]\n")


def new_command(command):
    if q_commands.full():
        print(f"Command {command} IGNORED. Queue is full.", error=True)
        return
    print(f"Received command: [yellow]{command}[/yellow]")
    q_commands.put_nowait(command)


def is_alert_condition(statistics, config):
    # Thresholds config
    max_total_tracks = int(config["maskcam"]["alert-max-total-tracks"])
    min_visible_tracks = int(config["maskcam"]["alert-min-visible-tracks"])
    max_defective = float(config["maskcam"]["alert-defective-fraction"])

    # Calculate visible tracks
    defective = int(statistics["tracks_defective"])
    non_defective = int(statistics["tracks_non_defective"])
    visible_tracks = non_defective + defective
    is_alert = False
    if statistics["tracks_total"] > max_total_tracks:
        is_alert = True
    elif visible_tracks >= min_visible_tracks:
        defective_fraction = float(statistics["tracks_defective"]) / visible_tracks
        is_alert = defective_fraction > max_defective

    print(f"[yellow]ALERT condition: {is_alert}[/yellow]")
    return is_alert

# handle_statistics gets called every 0.1 seconds
def handle_statistics(stats_queue, config, is_live_input, all_statistics):    
    while not stats_queue.empty():
        try:
            statistics = stats_queue.get_nowait() # get stats in a non blocking way
            all_statistics.append(statistics) # add them

            # if is_live_input:
            #     # Alert conditions detection
            #     raise_alert = is_alert_condition(statistics, config)
            #     if raise_alert:
            #         print("Alert condition met, flagging current files")
            #         flag_keep_current_files()
        except queue.Empty:
            print("Queue is empty, breaking loop")
            break
        except Exception as e:
            print(f"Error processing statistics: {str(e)}")
            print(f"Error type: {type(e)}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")


def handle_grass_statistics(grass_stats_queue, all_grass_events_list):
    """Handles grass statistics from the queue and appends them to a list."""
    while not grass_stats_queue.empty():
        try:
            grass_event = grass_stats_queue.get_nowait()
            all_grass_events_list.append(grass_event)
        except queue.Empty:
            break # Queue is empty
        except Exception as e:
            print(f"Error processing grass statistics: {str(e)}")

def allocate_free_udp_port():
    new_port = udp_ports_pool.pop()
    print(f"Allocating UDP port: {new_port}")
    return new_port


def release_udp_port(port_number):
    print(f"Releasing UDP port: {port_number}")
    udp_ports_pool.add(port_number)


def handle_file_saving(
    video_period, video_duration, ram_dir, hdd_dir, force_save
):
    period = timedelta(seconds=video_period)
    duration = timedelta(seconds=video_duration)
    latest_start = None
    latest_number = 0

    # Handle termination of previous file-saving processes and move files RAM->HDD
    terminated_idxs = []
    for idx, active_process in enumerate(active_filesave_processes):
        if datetime.now() - active_process["started"] >= duration:
            finish_filesave_process(active_process, hdd_dir, force_save)
            terminated_idxs.append(idx)
        if latest_start is None or active_process["started"] > latest_start:
            latest_start = active_process["started"]
            latest_number = active_process["number"]

    # Remove terminated processes from list in a separated loop
    for idx in sorted(terminated_idxs, reverse=True):
        del active_filesave_processes[idx]

    # Start new file-saving process if time has elapsed
    if latest_start is None or (datetime.now() - latest_start >= period):
        print(
            "[green]Time to start a new video file [/green]"
            f" (latest started at: {format_tdelta(latest_start)})"
        )
        new_process_number = latest_number + 1
        new_process_name = f"{P_FILESAVE_PREFIX}{new_process_number}"
        new_filename = f"{datetime.today().strftime('%Y%m%d_%H%M%S')}_{new_process_number}.mp4"
        new_filepath = f"{ram_dir}/{new_filename}"
        new_udp_port = allocate_free_udp_port()
        process_handler, e_interrupt_process = start_process(
            new_process_name,
            filesave_main,
            config,
            output_filename=new_filepath,
            udp_port=new_udp_port,
        )
        active_filesave_processes.append(
            dict(
                number=new_process_number,
                name=new_process_name,
                filepath=new_filepath,
                filename=new_filename,
                started=datetime.now(),
                process_handler=process_handler,
                e_interrupt=e_interrupt_process,
                flag_keep_file=False,
                udp_port=new_udp_port,
            )
        )


def finish_filesave_process(active_process, hdd_dir, force_filesave):
    terminate_process(
        active_process["name"],
        active_process["process_handler"],
        active_process["e_interrupt"],
        delete_info=True,
    )
    release_udp_port(active_process["udp_port"])

    # Move file to its definitive place if flagged, otherwise remove it
    if active_process["flag_keep_file"] or force_filesave:
        definitive_filepath = f"{hdd_dir}/{active_process['filename']}"
        print(f"Force file saving: {bool(force_filesave)}")
        print(f"Permanent video file created: [green]{definitive_filepath}[/green]")
        # Must use shutil here to move RAM->HDD
        shutil.move(active_process["filepath"], definitive_filepath)
    else:
        print(f"Removing RAM video file: {active_process['filepath']}")
        os.remove(active_process["filepath"])


def flag_keep_current_files():
    print("Request to [green]save current video files[/green]")
    for process in active_filesave_processes:
        print(f"Set flag to keep: [green]{process['filename']}[/green]")
        process["flag_keep_file"] = True


if __name__ == "__main__":
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass

    process_inference = None
    process_fileserver = None
    process_streaming = None
    process_save_serial = None
    e_interrupt_save_serial = None

    if len(sys.argv) > 2:
        print(
            """Usage: python3 maskcam_run.py [ URI ]
        Examples:
        \t$ python3 maskcam_run.py
        \t$ python3 maskcam_run.py file:///absolute/path/to/file.mp4
        \t$ python3 maskcam_run.py v4l2:///dev/video1
        \t$ python3 maskcam_run.py argus://0

        Notes:
        \t - If no URI is provided, will use default-input defined in config_maskcam.txt
        \t - If a file:///path/file.mp4 is provided, the output will be ./output_file.mp4
        \t - If the input is a live camera, the output will be consecutive
        \t   video files under /dev/shm/date_time.mp4
        \t   according to the time interval defined in output-chunks-duration in config_maskcam.txt.
        """
        )
        sys.exit(0)
    try:
        # Print any ENV var config override to avoid confusions
        print_config_overrides()

        # Input source
        if len(sys.argv) > 1:
            input_filename = sys.argv[1]
            print(f"Provided input source: {input_filename}")
        else:
            input_filename = config["maskcam"]["default-input"]
            print(f"Using input from config file: {input_filename}")

        # Input type: file or live camera
        is_usbcamera = USBCAM_PROTOCOL in input_filename
        is_raspicamera = RASPICAM_PROTOCOL in input_filename
        is_live_input = is_usbcamera or is_raspicamera

        # Streaming enabled by default?
        streaming_autostart = int(config["maskcam"]["streaming-start-default"])

        # Fileserver: sequentially save videos (only for camera input)
        fileserver_enabled = is_live_input and int(config["maskcam"]["fileserver-enabled"])
        fileserver_period = int(config["maskcam"]["fileserver-video-period"])
        fileserver_duration = int(config["maskcam"]["fileserver-video-duration"])
        fileserver_force_save = int(config["maskcam"]["fileserver-force-save"])
        fileserver_ram_dir = config["maskcam"]["fileserver-ram-dir"]
        fileserver_hdd_dir = config["maskcam"]["fileserver-hdd-dir"]

        # Save serial: save serial data to a file
        save_serial_enabled = int(config["maskcam"]["save_serial"])

        # Inference restart timeout
        tout_inference_restart = int(config["maskcam"]["timeout-inference-restart"])
        if is_live_input and tout_inference_restart:
            tout_inference_restart = timedelta(seconds=tout_inference_restart)
        else:
            tout_inference_restart = 0

        # Filesave processes: load available ports
        load_udp_ports_filesaving(config, udp_ports_pool)

        # Should only have 1 element at a time unless this thread gets blocked
        stats_queue = mp.Queue(maxsize=5)
        grass_stats_queue = mp.Queue() # New queue for grass statistics

        # SIGINT handler (Ctrl+C)
        signal.signal(signal.SIGINT, sigint_handler)
        print("[green bold]Press Ctrl+C to stop all processes[/green bold]")

        process_inference = None
        process_streaming = None
        process_fileserver = None
        process_save_serial = None
        e_interrupt_save_serial = None
        e_inference_ready = mp.Event()

        if fileserver_enabled:
            process_fileserver, e_interrupt_fileserver = start_process(
                P_FILESERVER, fileserver_main, config, directory=fileserver_hdd_dir
            )

        if streaming_autostart:
            print("[yellow]Starting streaming (streaming-start-default is set)[/yellow]")
            new_command(CMD_STREAMING_START)

        # Inference process: If input is a file, also saves file
        output_filename = None if is_live_input else f"output_{input_filename.split('/')[-1]}"
        process_inference, e_interrupt_inference = start_process(
            P_INFERENCE,
            inference_main,
            config,
            input_filename=input_filename,
            output_filename=output_filename,
            stats_queue=stats_queue, #created stats queue is passed to inference process
            grass_stats_queue=grass_stats_queue, # Pass the new grass queue
            e_ready=e_inference_ready,
        )

        all_statistics = [] 
        statistics_saving_period = int(config["maskcam"]["statistics-to-json-period"])  # 60 seconds
        stats_dir = config["maskcam"]["statistics-directory"]  # home directory
        last_write_time = datetime.now()  # Track the last write time


        timestamp_for_json_file = last_write_time.strftime("%Y%m%d_%H%M%S")
        stats_file_name = os.path.join(stats_dir, f"inference_statistics_{timestamp_for_json_file}.json")
        print(f"Statistics will be saved to: {stats_file_name}")
        os.makedirs(os.path.dirname(stats_file_name), exist_ok=True)


        grass_dir = config["grass-detection"]["file-directory"]
        grass_events_log_file_name = os.path.join(grass_dir, f"grass_events_log{timestamp_for_json_file}.json")
        print(f"Grass events will be saved to: {grass_events_log_file_name}")
        os.makedirs(os.path.dirname(grass_events_log_file_name), exist_ok=True)


        while not e_interrupt.is_set():
            # handle_statistics gets called 0.1 seconds to check if there are any stats in the stats_queue
            # Retrieves statistics from stats_queue and appends them to all_statistics,
            handle_statistics(stats_queue, config, is_live_input, all_statistics)
            handle_grass_statistics(grass_stats_queue, all_grass_statistics) # Handle grass stats
            current_time = datetime.now()
            
            # Write statistics to JSON file every 60 seconds
            if (current_time - last_write_time).total_seconds() >= statistics_saving_period:
                if all_statistics:
                    threading.Thread(
                        target=write_statistics_async,
                        args=(stats_dir, all_statistics.copy(), stats_file_name),
                    ).start()
                    all_statistics.clear()
                    last_write_time = current_time

            # Handle sequential file saving processes, only after inference process is ready
            if e_inference_ready.is_set():
                if fileserver_enabled and is_live_input:  # server can be enabled via MQTT
                    handle_file_saving(
                        fileserver_period,
                        fileserver_duration,
                        fileserver_ram_dir,
                        fileserver_hdd_dir,
                        fileserver_force_save,
                    )

            if not q_commands.empty():
                command = q_commands.get_nowait()
                print(f"Processing command: [yellow]{command}[yellow]")
                if command == CMD_STREAMING_START:
                    if process_streaming is None or not process_streaming.is_alive():
                        process_streaming, e_interrupt_streaming = start_process(
                            P_STREAMING, streaming_main, config
                        )
                elif command == CMD_STREAMING_STOP:
                    if process_streaming is not None and process_streaming.is_alive():
                        terminate_process(P_STREAMING, process_streaming, e_interrupt_streaming)
                elif command == CMD_INFERENCE_RESTART:
                    if process_inference.is_alive():
                        terminate_process(P_INFERENCE, process_inference, e_interrupt_inference)
                    process_inference, e_interrupt_inference = start_process(
                        P_INFERENCE,
                        inference_main,
                        config,
                        input_filename=input_filename,
                        output_filename=output_filename,
                        stats_queue=stats_queue,
                    )
                elif command == CMD_FILESERVER_RESTART:
                    if process_fileserver is not None and process_fileserver.is_alive():
                        terminate_process(P_FILESERVER, process_fileserver, e_interrupt_fileserver)
                    process_fileserver, e_interrupt_fileserver = start_process(
                        P_FILESERVER,
                        fileserver_main,
                        config,
                        directory=fileserver_hdd_dir,
                    )
                    fileserver_enabled = True
                elif command == CMD_FILE_SAVE:
                    flag_keep_current_files()
                else:
                    print("[red]Command not recognized[/red]", error=True)
            else:
                e_interrupt.wait(timeout=15)

            # Routine check: finish loop if the inference process is dead
            if not process_inference.is_alive():
                e_interrupt.set()

            # Routine check: restart inference at given interval (only live_input)
            if tout_inference_restart:
                inference_runtime = datetime.now() - processes_info[P_INFERENCE]["started"]
                if inference_runtime > tout_inference_restart:
                    print(
                        "[yellow]Restarting inference due to timeout-inference-restart"
                        f"(inference runtime: {format_tdelta(inference_runtime)})[/yellow]"
                    )
                    new_command(CMD_INFERENCE_RESTART)

            # Check if save_serial process has died and restart if needed
            if save_serial_enabled:
                if process_save_serial is not None and not process_save_serial.is_alive():
                    print("[red]save_serial process died. Restarting...[/red]")
                    process_save_serial, e_interrupt_save_serial = start_process(
                        P_SAVESERIAL, save_serial_main, config
                    )

        # Start save_serial.py as a subprocess if enabled
        if save_serial_enabled:
            process_save_serial, e_interrupt_save_serial = start_process(
                P_SAVESERIAL, save_serial_main, config
            )

        # Write the PID to a unique file for access by other scripts
        pid = os.getpid()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pidfile = f"/tmp/maskcam_run_{timestamp}.pid"
        with open(pidfile, "w") as f:
            f.write(str(pid))

    except:  # noqa
        console.print_exception()

    # Terminate inference process first to stop adding to stats_queue
    if process_inference is not None and process_inference.is_alive():
        terminate_process(P_INFERENCE, process_inference, e_interrupt_inference)

    # Process any remaining statistics from the queue
    while not stats_queue.empty():
        try:
            statistics = stats_queue.get_nowait()
            all_statistics.append(statistics)
        except queue.Empty:
            break
    
    # Process any remaining grass statistics from the queue
    while not grass_stats_queue.empty():
        try:
            grass_event = grass_stats_queue.get_nowait()
            all_grass_statistics.append(grass_event)
        except queue.Empty:
            break

    # Write final statistics to JSON file
    if all_statistics:
        write_statistics_async(stats_dir, all_statistics, stats_file_name)
        print("Final statistics written to JSON file.")
        all_statistics.clear()

    # Write final grass events to JSON file
    if all_grass_statistics:
        print(f"Saving final grass events to: {grass_events_log_file_name}")
        try:
            with open(grass_events_log_file_name, 'w') as f:
                json.dump(all_grass_statistics, f, indent=2, default=str)
            print("Final grass events written to JSON file.")
        except Exception as e:
            print(f"Error writing final grass events: {str(e)}")
        all_grass_statistics.clear()

    # Terminate all running processes, avoid breaking on any exception
    for active_file_process in active_filesave_processes:
        try:
            finish_filesave_process(
                active_file_process,
                fileserver_hdd_dir,
                fileserver_force_save,
            )
        except:  # noqa
            console.print_exception()
    try:
        if process_inference is not None and process_inference.is_alive():
            terminate_process(P_INFERENCE, process_inference, e_interrupt_inference)
    except:  # noqa
        console.print_exception()
    try:
        if process_fileserver is not None and process_fileserver.is_alive():
            terminate_process(P_FILESERVER, process_fileserver, e_interrupt_fileserver)
    except:  # noqa
        console.print_exception()
    try:
        if process_streaming is not None and process_streaming.is_alive():
            terminate_process(P_STREAMING, process_streaming, e_interrupt_streaming)
    except:  # noqa
        console.print_exception()

    # Terminate save_serial process
    try:
        if process_save_serial is not None and process_save_serial.is_alive():
            terminate_process(P_SAVESERIAL, process_save_serial, e_interrupt_save_serial)
    except:  # noqa
        console.print_exception()
