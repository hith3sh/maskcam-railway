from .config import config
from gi.repository import GLib
import socket

ADDRESS_UNKNOWN_LABEL = "<device-address-not-configured>"

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def get_ip_address():
    result_value = config["maskcam"]["device-address"].strip()
    if not result_value or result_value == "0":
        result_value = get_local_ip()        
    return result_value


def get_streaming_address(host_address, rtsp_port, rtsp_path):
    return f"rtsp://{host_address}:{rtsp_port}{rtsp_path}"


def format_tdelta(time_delta):
    # Format to show timedelta objects as string
    if time_delta is None:
        return "N/A"
    return f"{time_delta}".split(".")[0]  # Remove nanoseconds


def glib_cb_restart(t_restart):
    # Timer to avoid GLoop locking infinitely
    # We want to run g_context.iteration(may_block=True)
    # since may_block=False will use high CPU,
    # and adding sleeps lags event processing.
    # But we want to check periodically for other events
    GLib.timeout_add(t_restart, glib_cb_restart, t_restart)


def load_udp_ports_filesaving(config, udp_ports_pool):
    for port in config["maskcam"]["udp-ports-filesave"].split(","):
        udp_ports_pool.add(int(port))
    return udp_ports_pool
