import subprocess
import time
from datetime import datetime
import threading
import os
import sys
import platform
import shutil
import configparser

# Read config
config = configparser.ConfigParser()
config.read('config/settings.cfg')
VERBOSE = config.getboolean('Logging', 'VERBOSE')
PRINT_TERMINAL = config.getboolean('Logging', 'PRINT_TERMINAL')
LOG_DEBUG = config.getboolean('Logging', 'LOG_DEBUG')
LOG_SUCCESS = config.getboolean('Logging', 'LOG_SUCCESS')
AUTO_ARCHIVE_INTERVAL = config.getint('Intervals', 'AUTO_ARCHIVE_INTERVAL')
PING_INTERVAL = config.getint('Intervals', 'PING_INTERVAL')
ARCHIVE_DELETION_INTERVAL = config.getint('Intervals', 'ARCHIVE_DELETION_INTERVAL')
BYTE_COUNT = config.getint('Payload', 'BYTE_COUNT')
LATENCY_THRESHOLD = config.getint('Other','LATENCY_THRESHOLD')
AUTO_DELETION_SIZE_THRESHOLD = config.getint('Files', 'AUTO_DELETION_SIZE_THRESHOLD')
LOG_DIR = config.get('Files', 'LOG_DIR')
ARCHIVE_DIR = config.get('Files', 'ARCHIVE_DIR')
SUCCESS_LOG = config.get('Files', 'SUCCESS_LOG')
FAULT_LOG = config.get('Files', 'FAULT_LOG')
DEBUG_LOG = config.get('Files', 'DEBUG_LOG')

# State variables
running = True
successes = 0
latency_faults = 0
timeout_faults = 0
packet_loss_faults = 0
unknown_host_faults = 0
other_faults = 0
start_time = time.time()
ping_count = 0
total_latency = 0.0


def toggle_verbose():
    global VERBOSE
    VERBOSE = not VERBOSE

def toggle_print_terminal():
    global PRINT_TERMINAL
    PRINT_TERMINAL = not PRINT_TERMINAL

def toggle_log_debug():
    global LOG_DEBUG
    LOG_DEBUG = not LOG_DEBUG

def toggle_log_success():
    global LOG_SUCCESS
    LOG_SUCCESS = not LOG_SUCCESS

def ensure_log_file_exists(file_path):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    if not os.path.exists(file_path):
        open(file_path, 'a').close()

def log_debug(message):
    if LOG_DEBUG:
        ensure_log_file_exists(DEBUG_LOG)
        with open(DEBUG_LOG, "a") as f:
            f.write(f"{datetime.now()} - {message}\n")

def log_fault(reason, fault):
    ensure_log_file_exists(FAULT_LOG)
    with open(FAULT_LOG, "a") as f:
        f.write(f"{datetime.now()} - {reason}\n")

    global latency_faults, timeout_faults, other_faults, packet_loss_faults, unknown_host_faults

    if fault == "l":
        latency_faults += 1
    elif fault == "t":
        timeout_faults += 1
    elif fault == "p":
        packet_loss_faults += 1
    elif fault == "u":
        unknown_host_faults += 1
    else:
        other_faults += 1

def log_success(message):
    if LOG_SUCCESS:
        ensure_log_file_exists(SUCCESS_LOG)
        with open(SUCCESS_LOG, "a") as f:
            f.write(f"{datetime.now()} - {message}\n")
    global successes
    successes += 1

def print_term(message):
    if PRINT_TERMINAL:
        print(message)

def get_network_name():

    system = platform.system().lower()

    if system == "darwin":  # macOS
        try:
            # Step 1: Get Wi-Fi interface (e.g., en0 or en1)
            result = subprocess.run(
                ["networksetup", "-listallhardwareports"],
                capture_output=True,
                text=True
            )
            wifi_device = None
            lines = result.stdout.splitlines()
            for i in range(len(lines)):
                if "Wi-Fi" in lines[i]:
                    for j in range(i, i+3):  # next lines should include Device
                        if "Device" in lines[j]:
                            wifi_device = lines[j].split(":")[1].strip()
                            break
                    break

            if not wifi_device:
                return "Wi-Fi interface not found"

            # Step 2: Get SSID from that interface
            result = subprocess.run(
                ["networksetup", "-getairportnetwork", wifi_device],
                capture_output=True,
                text=True
            )
            output = result.stdout.strip()
            # print(f"DEBUG: interface={wifi_device}, output={output}")  # Optional debug

            if "You are not associated" in output:
                return "SSID Unknown/Not connected"
            if "Current Wi-Fi Network:" in output:
                return output.split(":", 1)[1].strip()
            return f"Unknown (unmatched output: {output})"

        except Exception as e:
            return f"Unknown (macOS error: {e})"

    elif system == "windows":
        try:
            result = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True,
                text=True
            )
            for line in result.stdout.splitlines():
                if "SSID" in line and "BSSID" not in line:
                    return line.split(":")[1].strip()
        except Exception as e:
            return f"Unknown (Windows error: {e})"

    else:
        return "Unsupported OS"
    
def archive_logs():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    # Ensure archive directory exists
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    # Archive each log file if it exists
    for filename, label in [
        (SUCCESS_LOG, "success"),
        (FAULT_LOG, "fault"),
        (DEBUG_LOG, "debug")
    ]:
        if os.path.exists(filename):
            archived_name = f"{label}_{timestamp}.log"
            archived_path = os.path.join(ARCHIVE_DIR, archived_name)

            # Move to archive
            shutil.copy2(filename, archived_path)
            print(f"Archived {label} log to {archived_path}")

            # Clear original log
            open(filename, "w").close()
        else:
            print(f"{label} log not found; skipping.")

    print("All available logs archived and cleared.")

def delete_small_logs(directory, size_threshold):
    if not os.path.exists(directory):
        print(f"Directory '{directory}' does not exist.")
        return

    deleted_files = 0
    now = time.time()

    for filename in os.listdir(directory):
        if filename.endswith(".log"):
            file_path = os.path.join(directory, filename)
            try:
                # Expecting format like: success_2025-07-18_21-07-12.log
                parts = filename.rsplit("_", 2)
                if len(parts) < 3:
                    print(f"Skipping unrecognized filename format: {filename}")
                    continue

                timestamp_str = f"{parts[-2]}_{parts[-1].replace('.log', '')}"
                file_time = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")
                file_age_days = (now - file_time.timestamp()) / 86400

                file_size = os.path.getsize(file_path)

                if file_size < size_threshold and file_age_days >= ARCHIVE_DELETION_INTERVAL:
                    os.remove(file_path)
                    print(f"Deleted: {filename} (size: {file_size} bytes, age: {file_age_days:.1f} days)")
                    deleted_files += 1

            except Exception as e:
                print(f"Error handling {filename}: {e}")

    if deleted_files == 0:
        log_debug(f"No log files matched deletion criteria.")
    else:
        print(f"{deleted_files} old/small log file(s) deleted.")

def ping_loop():
    global running
    while running:
        try:
            if platform.system().lower() == "windows":
                # -l sets the data size to x bytes
                PING_CMD = ["ping", "-n", "1", "-l", str(BYTE_COUNT), "google.com"]
            else:
                # -s sets payload size to x bytes (excluding 8-byte ICMP header)
                PING_CMD = ["ping", "-c", "1", "-s", str(BYTE_COUNT), "google.com"]

            result = subprocess.run(PING_CMD, capture_output=True, text=True)

            log_debug(result)

            if VERBOSE:
                print_term(result)

            match result.returncode:
                case 0:
                    try:
                        target_content = result.stdout.split("\n")[1]
                        if not VERBOSE:
                            print_term(target_content)
                        time_ms = float(result.stdout.split("time=")[1].split(" ")[0])
                        global total_latency
                        total_latency += time_ms
                        if time_ms > LATENCY_THRESHOLD:
                            log_fault(f"HIGH LATENCY - {get_network_name()} | {target_content}", "l")
                            log_success(f"{get_network_name()} | {target_content}\t\tHIGH LATENCY")
                        else:
                            log_success(f"{get_network_name()} | {target_content}")
                    except Exception as e:
                        log_fault(f"UNEXPECTED FORMAT - {e}", "o")
                        log_debug(f"Parse error - {e}")
                case 2:
                    log_fault(f"COMPLETE PACKET LOSS - {get_network_name()}", "p")
                case 68:
                    log_fault(f"UNKNOWN HOST - {get_network_name()}", "u")
                case _:
                    log_fault(f"TIMEOUT/NO RESPONSE - {get_network_name()}", "t")
            
            delete_small_logs(ARCHIVE_DIR, AUTO_DELETION_SIZE_THRESHOLD)
            
        except Exception as e:
            log_fault(f"EXCEPTION - {e} - {get_network_name()} | {result}", "o")
            log_debug(f"Exception - {e} - {get_network_name()} | {result}")

        global ping_count
        ping_count += 1
        if AUTO_ARCHIVE_INTERVAL <= (ping_count * PING_INTERVAL):
            ping_count = 0
            archive_logs()
        time.sleep(PING_INTERVAL)

def command_loop():
    global running
    while running:
        try:
            cmd = input().strip().lower()
            if cmd == "q":
                running = False
                print("Exiting...")
                print("")
            elif cmd == "clear -s":
                open(SUCCESS_LOG, "w").close()
                print("Success log cleared.")
                print("")
            elif cmd == "clear -f":
                open(FAULT_LOG, "w").close()
                print("Faults log cleared.")
                print("")
            elif cmd == "clear -d":
                open(DEBUG_LOG, "w").close()
                print("Debug log cleared.")
                print("")
            elif cmd == "clear -a":
                delete_small_logs(ARCHIVE_DIR, AUTO_DELETION_SIZE_THRESHOLD)
                print("Small archived logs cleared.")
                print("")
            elif cmd == "log -t":
                # toggle_print_terminal()
                print("Terminal logging not implemented and not recommended")
                print("")
            elif cmd == "log -d":
                toggle_log_debug()
                print("Debug logging enabled?" + str(LOG_DEBUG))
                print("")
            elif cmd == "log -s":
                toggle_log_success()
                print("Success logging enabled?" + str(LOG_SUCCESS))
                print("")
            elif cmd == "log -v":
                toggle_verbose()
                print("Verbose logging enabled?" + str(VERBOSE))
                print("")
            elif cmd == "archive":
                archive_logs()
                print("")
            elif cmd == "report":
                uptime = time.time() - start_time
                hours, rem = divmod(int(uptime), 3600)
                minutes, seconds = divmod(rem, 60)
                print("===== Report =====")
                print(f"Uptime:    {hours}h {minutes}m {seconds}s")
                print(f"Successes: {successes}")
                print(f"Faults:    {latency_faults + timeout_faults + other_faults + packet_loss_faults + unknown_host_faults}")
                print(f"     Latency:     {latency_faults}")
                print(f"     Timeout:     {timeout_faults}")
                print(f"     Packet Loss: {packet_loss_faults}")
                print(f"     Unkown Host: {unknown_host_faults}")
                print(f"     Other:       {other_faults}")
                print(f"Avg Ltncy: {(total_latency / ping_count):.2f}")
                print("")
            elif cmd == "?":
                print("===== Command List =====")
                print("q = Quit Application")
                print("clear -s = Clear success log")
                print("clear -f = Clear faults log")
                print("clear -d = Clear debug log")
                print("clear -a = Clear small archived logs")
                print("log -t = Toggle terminal logging (NOT IMPLEMENTED)")
                print("log -d = Toggle debug logging")
                print("log -s = Toggle success logging")
                print("log -v = Toggle verbose logging")
                print("archive = Archive running logs")
                print("report = Print current uptime report")
                print("")
            else:
                print("Unknown command. Enter '?' for a list of commands")
                print("")
        except EOFError:
            break

# Start threads
threading.Thread(target=ping_loop, daemon=True).start()
command_loop()