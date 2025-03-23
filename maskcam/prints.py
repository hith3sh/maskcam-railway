import logging
from rich.logging import RichHandler

logging.basicConfig(
    level="NOTSET",
    format="%(message)s",
    datefmt="|",  # Not needed w/balena, use [%X] otherwise
    handlers=[RichHandler(markup=True)],
)

log = logging.getLogger("rich")


def print_process(
    color, process_name, *args, error=False, warning=False, exception=False, **kwargs
):
    msg = " ".join([str(arg) for arg in args])  # Concatenate all incoming strings or objects
    rich_msg = f"[{color}]{process_name}[/{color}] | {msg}"
    if error:
        log.error(rich_msg)
    elif warning:
        log.warning(rich_msg)
    elif exception:
        log.exception(rich_msg)
    else:
        log.info(rich_msg)


def print_run(*args, **kwargs):
    print_process("blue", "maskcam-run", *args, **kwargs)


def print_fileserver(*args, **kwargs):
    print_process("dark_violet", "file-server", *args, **kwargs)


def print_filesave(*args, **kwargs):
    print_process("dark_magenta", "file-save", *args, **kwargs)


def print_streaming(*args, **kwargs):
    print_process("dark_green", "streaming", *args, **kwargs)


def print_inference(*args, **kwargs):
    print_process("bright_yellow", "inference", *args, **kwargs)


def print_mqtt(*args, **kwargs):
    print_process("bright_green", "mqtt", *args, **kwargs)


def print_common(*args, **kwargs):
    print_process("white", "common", *args, **kwargs)
