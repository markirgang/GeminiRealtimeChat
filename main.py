"""
## Documentation
Quickstart: https://github.com/google-gemini/cookbook/blob/main/quickstarts/Get_started_LiveAPI.py

## Setup

To install the dependencies for this script, run:

```
pip install google-genai opencv-python pyaudio pillow mss python-dotenv
```
"""

import os
import asyncio
import base64
import io
import traceback
import socket

import cv2
import pyaudio
import PIL.Image

import argparse
from dotenv import load_dotenv

from google import genai
from google.genai import types

load_dotenv()

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None


class TelloController:
    def __init__(self, ip="192.168.10.1", port=8889):
        self.drone_address = (ip, port)
        self.sock = None
        self.sdk_enabled = False
        self.simulated = False  # Start by trying real connection, fallback on failure

    def init_socket(self):
        if self.sock is None and not self.simulated:
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sock.bind(('', 0))
                self.sock.settimeout(3.0)  # 3-second timeout for quick detection
            except Exception as e:
                print(f"[Tello] Failed to initialize socket: {e}. Switching to simulation.")
                self.simulated = True

    def send_cmd(self, command: str) -> dict:
        if self.simulated:
            print(f"\n[Simulated Tello] Executing command: {command}")
            return {"status": "success", "response": "ok (simulated)"}
            
        self.init_socket()
        try:
            # Automatically enable SDK mode if not already done
            if not self.sdk_enabled and command != "command":
                print("[Tello] Enabling SDK mode...")
                self.sock.settimeout(3.0)  # Short timeout for initial SDK check
                self.sock.sendto(b"command", self.drone_address)
                response, _ = self.sock.recvfrom(1024)
                print(f"[Tello] SDK response: {response.decode('utf-8').strip()}")
                self.sdk_enabled = True

            print(f"[Tello] Sending command: {command}")
            # Use a longer timeout for control commands as actions like takeoff, land, or flip take time
            self.sock.settimeout(15.0)
            self.sock.sendto(command.encode('utf-8'), self.drone_address)
            response, _ = self.sock.recvfrom(1024)
            res_str = response.decode('utf-8').strip()
            print(f"[Tello] Response: {res_str}")
            return {"status": "success", "response": res_str}
        except (socket.timeout, socket.error) as e:
            if not self.sdk_enabled:
                # Fall back to simulation if the physical drone isn't reachable initially
                print(f"[Tello] Initial connection/SDK activation failed ({e}). Falling back to simulation mode.")
                self.simulated = True
                return {"status": "success", "response": "ok (simulated fallback)", "simulated": True}
            else:
                # If SDK mode was already enabled, it's a temporary timeout or connection glitch
                print(f"[Tello] Command '{command}' failed or timed out ({e}). Retaining connection.")
                return {"status": "error", "message": f"Command execution failed: {e}"}


class LevitonController:
    def __init__(self, email=None, password=None):
        self.email = email or os.environ.get("LEVITON_EMAIL")
        self.password = password or os.environ.get("LEVITON_PASSWORD")
        self.session = None
        # Start in simulated mode if no email or password is provided or if they are placeholders
        self.simulated = not (self.email and self.password) or "placeholder" in self.email or "placeholder" in self.password
        if self.simulated:
            print("[Leviton] Running in simulated mode (no valid credentials in .env).")

    def login(self):
        if self.simulated:
            return True
        if self.session is not None:
            return True
        try:
            from decora_wifi import DecoraWiFiSession
            self.session = DecoraWiFiSession()
            self.session.login(self.email, self.password)
            print("[Leviton] Successfully authenticated with Leviton Cloud Services.")
            return True
        except Exception as e:
            print(f"[Leviton] Failed to authenticate: {e}. Switching to simulation.")
            self.simulated = True
            return True

    def set_light_state(self, switch_name: str, state: bool, brightness: int = None) -> dict:
        if self.simulated:
            state_str = "ON" if state else "OFF"
            bright_str = f" at {brightness}%" if brightness is not None else ""
            print(f"\n[Simulated Leviton] Set switch '{switch_name}' to {state_str}{bright_str}")
            return {"status": "success", "switch_name": switch_name, "state": state_str, "brightness": brightness, "simulated": True}

        self.login()
        if self.simulated:
            return self.set_light_state(switch_name, state, brightness)

        try:
            from decora_wifi.models.residential_account import ResidentialAccount
            
            perms = self.session.user.get_residential_permissions()
            found_switch = None
            
            for permission in perms:
                acct = ResidentialAccount(self.session, permission.residentialAccountId)
                residences = acct.get_residences()
                for residence in residences:
                    switches = residence.get_iot_switches()
                    for switch in switches:
                        if switch_name.lower() in switch.name.lower():
                            found_switch = switch
                            break
                    if found_switch:
                        break
                if found_switch:
                    break

            if not found_switch:
                print(f"[Leviton] Switch '{switch_name}' not found.")
                return {"status": "error", "message": f"Switch '{switch_name}' not found."}

            attribs = {'power': 'ON' if state else 'OFF'}
            if brightness is not None:
                attribs['brightness'] = brightness

            found_switch.update_attributes(attribs)
            found_switch.refresh()
            print(f"[Leviton] Updated switch '{found_switch.name}' to power={found_switch.power}, brightness={found_switch.brightness}")
            return {
                "status": "success",
                "switch_name": found_switch.name,
                "state": found_switch.power,
                "brightness": found_switch.brightness
            }
        except Exception as e:
            print(f"[Leviton] Error updating switch '{switch_name}': {e}")
            return {"status": "error", "message": str(e)}


class EwelinkController:
    def __init__(self, username=None, password=None, region="us"):
        self.username = username or os.environ.get("EWELINK_USERNAME")
        self.password = password or os.environ.get("EWELINK_PASSWORD")
        self.region = region or os.environ.get("EWELINK_REGION", "us")
        self.client = None
        # Start in simulated mode if no credentials or placeholders are present
        self.simulated = not (self.username and self.password) or "placeholder" in self.username or "placeholder" in self.password
        if self.simulated:
            print("[eWeLink] Running in simulated mode (no valid credentials in .env).")

    def login(self):
        if self.simulated:
            return True
        if self.client is not None:
            return True
        try:
            import sonoff
            import random
            import time
            import requests

            # Patch update_devices to work with modern eWeLink API and query parameters
            def patched_update_devices(self_sonoff):
                if not self_sonoff._wshost:
                    return []
                
                # Check skipped login / grace period
                if self_sonoff._skipped_login and self_sonoff.is_grace_period():
                    return self_sonoff._devices

                nonce = ''.join([str(random.randint(0, 9)) for _ in range(15)])
                params = {
                    'appid': 'oeVkj2lYFGnJu5XUtWisfW4utiN4u9Mq',
                    'ts': int(time.time()),
                    'nonce': nonce,
                    'version': '6'
                }

                try:
                    url = f'https://{self_sonoff._api_region}-api.coolkit.cc:8080/api/user/device'
                    r = requests.get(url, headers=self_sonoff._headers, params=params)
                    resp = r.json()
                    
                    if 'error' in resp and resp['error'] != 0:
                        print(f"[eWeLink] API error response: {resp}")
                        if resp['error'] in [400, 401]:
                            return self_sonoff._devices

                    if isinstance(resp, dict) and 'devicelist' in resp:
                        self_sonoff._devices = resp['devicelist']
                    else:
                        self_sonoff._devices = resp
                except Exception as e:
                    print(f"[eWeLink] Error updating devices: {e}")
                
                return self_sonoff._devices

            sonoff.Sonoff.update_devices = patched_update_devices

            self.client = sonoff.Sonoff(self.username, self.password, self.region)
            print("[eWeLink] Successfully authenticated with eWeLink Cloud Services.")
            return True
        except Exception as e:
            print(f"[eWeLink] Failed to authenticate: {e}. Switching to simulation.")
            self.simulated = True
            return True

    def set_device_state(self, device_name: str, state: bool) -> dict:
        if self.simulated:
            state_str = "ON" if state else "OFF"
            print(f"\n[Simulated eWeLink] Set device '{device_name}' to {state_str}")
            return {"status": "success", "device_name": device_name, "state": state_str, "simulated": True}

        self.login()
        if self.simulated:
            return self.set_device_state(device_name, state)

        try:
            devices = self.client.get_devices()
            found_device = None
            if devices:
                for device in devices:
                    name = device.get('name', '')
                    if device_name.lower() in name.lower():
                        found_device = device
                        break

            if not found_device:
                print(f"[eWeLink] Device '{device_name}' not found.")
                return {"status": "error", "message": f"Device '{device_name}' not found."}

            device_id = found_device['deviceid']
            state_str = 'on' if state else 'off'
            self.client.switch(state_str, device_id, None)
            print(f"[eWeLink] Updated device '{found_device.get('name')}' state to {state_str}")
            return {
                "status": "success",
                "device_name": found_device.get('name'),
                "state": "ON" if state else "OFF"
            }
        except Exception as e:
            print(f"[eWeLink] Error updating device '{device_name}': {e}")
            return {"status": "error", "message": str(e)}


FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

MODEL = "models/gemini-3.1-flash-live-preview"

DEFAULT_MODE = "camera"

client = genai.Client(
    http_options={"api_version": "v1beta"},
    api_key=os.environ.get("GEMINI_API_KEY"),
)


def get_config(voice_name="Zephyr", enable_esp32=True):
    tools = []
    
    # Common tools list
    function_declarations = []
    
    if enable_esp32:
        function_declarations.append(
            types.FunctionDeclaration(
                name="set_led_state",
                description="Controls the onboard LED of the ESP32 dev module. State should be True to turn the LED on, or False to turn it off.",
                parameters={
                    "type": "object",
                    "properties": {
                        "state": {
                            "type": "boolean",
                            "description": "True to turn on the LED (red), False to turn it off."
                        }
                    },
                    "required": ["state"]
                }
            )
        )
        function_declarations.append(
            types.FunctionDeclaration(
                name="pulse_led",
                description="Pulses (blinks) a specific GPIO pin on the ESP32 module on and off a specified number of times. When finger gestures are shown (1 finger -> GPIO 1, 2 fingers -> GPIO 2, 3 fingers -> GPIO 3, 4 fingers -> GPIO 4), pulse target GPIO pin N on and off 1 time.",
                parameters={
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "integer",
                            "description": "The number of times to pulse/blink the pin (defaults to 1)."
                        },
                        "gpio": {
                            "type": "integer",
                            "description": "The target ESP32 GPIO pin number to pulse (e.g. 1 for 1 finger, 2 for 2 fingers, 3 for 3 fingers, 4 for 4 fingers)."
                        },
                        "duration_ms": {
                            "type": "integer",
                            "description": "Optional duration in milliseconds for the ON and OFF state of each pulse. Defaults to 500ms."
                        }
                    },
                    "required": ["gpio"]
                }
            )
        )
        
    # Add Tello drone control tool
    function_declarations.append(
        types.FunctionDeclaration(
            name="send_tello_command",
            description=(
                "Sends a control command to the Tello drone over UDP. "
                "Supported commands include:\n"
                "- 'takeoff': Take off from the ground\n"
                "- 'land': Land on the ground\n"
                "- 'up x': Fly up (x = 20-200 cm)\n"
                "- 'down x': Fly down (x = 20-200 cm)\n"
                "- 'left x': Fly left (x = 20-200 cm)\n"
                "- 'right x': Fly right (x = 20-200 cm)\n"
                "- 'forward x': Fly forward (x = 20-200 cm)\n"
                "- 'back x': Fly back (x = 20-200 cm)\n"
                "- 'cw x': Rotate x degrees clockwise (x = 1-360)\n"
                "- 'ccw x': Rotate x degrees counter-clockwise (x = 1-360)\n"
                "- 'flip x': Flip in direction x ('l', 'r', 'f', 'b')\n"
                "- 'emergency': Stop motors immediately"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The SDK command to send to the drone."
                    }
                },
                "required": ["command"]
            }
        )
    )

    # Add Leviton switch control tool
    function_declarations.append(
        types.FunctionDeclaration(
            name="set_leviton_light_state",
            description=(
                "Controls Leviton Decora Smart Wi-Fi switches and dimmers in the user's home. "
                "Allows turning lights on/off and optionally setting brightness levels (for dimmers)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "switch_name": {
                        "type": "string",
                        "description": "The name of the light switch to control (e.g. 'Kitchen', 'Living Room')."
                    },
                    "state": {
                        "type": "boolean",
                        "description": "True to turn the light on, False to turn it off."
                    },
                    "brightness": {
                        "type": "integer",
                        "description": "Optional brightness level as a percentage (0 to 100). Only applicable to dimmable switches."
                    }
                },
                "required": ["switch_name", "state"]
            }
        )
    )

    # Add eWeLink switch control tool
    function_declarations.append(
        types.FunctionDeclaration(
            name="set_ewelink_device_state",
            description=(
                "Controls eWeLink (Sonoff) smart plugs, switches, and other devices in the user's home. "
                "Allows turning devices on or off."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "device_name": {
                        "type": "string",
                        "description": "The name of the device to control (e.g. 'Fan', 'Desk Light')."
                    },
                    "state": {
                        "type": "boolean",
                        "description": "True to turn the device on, False to turn it off."
                    }
                },
                "required": ["device_name", "state"]
            }
        )
    )
    
    if function_declarations:
        tools.append(types.Tool(function_declarations=function_declarations))

    system_instruction = (
        "You are a helpful real-time multimodal voice assistant running on the user's local computer. "
        "You have direct access to local hardware and smart devices: an onboard LED of an ESP32 microcontroller, "
        "a Tello drone, Leviton smart lights, and eWeLink (Sonoff) devices.\n\n"
        "1. VISUAL MODALITY AWARENESS:\n"
        "   - You are receiving a continuous, real-time video stream (from the user's webcam or screen share).\n"
        "   - Pay close attention to what you see. You MUST proactively notice, react to, and comment on objects, gestures, text, or visual changes shown in the video feed. Do NOT wait for the user to prompt you or say they are showing you something; describe what you see naturally as part of the conversation.\n"
        "   - For example, if you see the user holding a coffee cup, showing a phone, or displaying any object, refer to it and ask about it or comment on it immediately.\n\n"
        "2. TIME PERCEPTION CALIBRATION:\n"
        "   - The video stream is sent to you at exactly 1 frame per second (1 FPS). Each frame you receive represents exactly 1 second of real time.\n"
        "   - When estimating time or counting seconds (e.g., if the user asks you to wait 5 seconds, count seconds, or track time), use the number of incoming frames as your clock (e.g. 5 frames = 5 seconds). Do not rush or estimate time based on text-generation speeds; wait for the appropriate amount of time to pass.\n\n"
        "3. HARDWARE CONTROL:\n"
        "   - ESP32 LED: You MUST use the `set_led_state` tool to turn the LED on or off. If the user asks you to pulse, blink, or flash the LED a certain number of times (e.g., to match the count of fingers you see in the frame), you MUST use the `pulse_led` tool with the appropriate count.\n"
        "   - Tello Drone: You MUST use the `send_tello_command` tool to control the Tello drone when the user asks you to perform actions like takeoff, landing, moving, flipping, or rotating.\n"
        "   - Leviton Lights: You MUST use the `set_leviton_light_state` tool when the user asks you to turn smart home lights on, off, or change their brightness level.\n"
        "   - eWeLink Devices: You MUST use the `set_ewelink_device_state` tool when the user asks you to turn eWeLink or Sonoff devices (plugs, switches, fans, etc.) on or off.\n\n"
        "If a physical device is not connected or configured, the application will automatically run "
        "the command in simulated/fallback mode, so always call the tools anyway. "
        "Never tell the user that you cannot control the hardware, as you are fully equipped with tools to do so."
    )

    return types.LiveConnectConfig(
        response_modalities=[
            "AUDIO",
        ],
        media_resolution="MEDIA_RESOLUTION_MEDIUM",
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
            )
        ),
        tools=tools,
        system_instruction=system_instruction,
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=104857,
            sliding_window=types.SlidingWindow(target_tokens=52428),
        ),
    )

pya = pyaudio.PyAudio()


def count_fingers(hand_landmarks) -> int:
    """
    Counts the number of extended fingers using MediaPipe hand landmarks.
    """
    landmarks = hand_landmarks.landmark
    fingers_open = 0

    # Index, Middle, Ring, Pinky
    tips = [8, 12, 16, 20]
    pips = [6, 10, 14, 18]
    for tip, pip in zip(tips, pips):
        if landmarks[tip].y < landmarks[pip].y:
            fingers_open += 1

    # Thumb
    # index mcp (5), pinky mcp (17), thumb tip (4), thumb ip (3)
    if landmarks[5].x > landmarks[17].x:
        # Left hand or right hand back
        if landmarks[4].x > landmarks[3].x:
            fingers_open += 1
    else:
        # Right hand or left hand back
        if landmarks[4].x < landmarks[3].x:
            fingers_open += 1

def load_esp32_button_config():
    """
    Loads button functions and GPIO mappings for ESP32 Left and ESP32 Right.
    Tries to read 'Birds On_Off Buttons ESP32.xlsx' if present; otherwise uses fallback data.
    """
    fallback_config = {
        "left": [
            {"name": "L Parrot Mouth", "gpio": 0},
            {"name": "L Parrot Eyes", "gpio": 1},
            {"name": "L Parrot Body", "gpio": 2},
            {"name": "L Parrot Light", "gpio": 3},
            {"name": "L Parrot Mouth Select", "gpio": 4},
            {"name": "L Rear Bird Rear Move", "gpio": 5},
            {"name": "L Rear Bird Rear Light", "gpio": 12},
            {"name": "L Front Bird Move", "gpio": 13},
            {"name": "L Front Bird Light", "gpio": 14},
            {"name": "L Bird Front Chirp", "gpio": 15},
            {"name": "Center Bird Move", "gpio": 16},
        ],
        "right": [
            {"name": "R Parrot Mouth", "gpio": 0},
            {"name": "R Parrot Eyes", "gpio": 1},
            {"name": "R Parrot Body", "gpio": 2},
            {"name": "R Parrot Light", "gpio": 3},
            {"name": "R Parrot Mouth Select", "gpio": 4},
            {"name": "R Rear Bird Rear Move", "gpio": 5},
            {"name": "R Rear Bird Rear Light", "gpio": 12},
            {"name": "R Front Bird Move", "gpio": 13},
            {"name": "R Front Bird Light", "gpio": 14},
            {"name": "R Bird Front Chirp", "gpio": 15},
            {"name": "Center Bird Move", "gpio": 16},
        ],
    }

    excel_path = "Birds On_Off Buttons ESP32.xlsx"
    if not os.path.exists(excel_path):
        return fallback_config

    try:
        import zipfile
        import xml.etree.ElementTree as ET

        z = zipfile.ZipFile(excel_path)
        shared_strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            tree = ET.fromstring(z.read('xl/sharedStrings.xml'))
            for elem in tree.iter():
                if elem.tag.endswith('t') and elem.text:
                    shared_strings.append(elem.text)

        sf = 'xl/worksheets/sheet1.xml'
        if sf not in z.namelist():
            return fallback_config

        tree = ET.fromstring(z.read(sf))
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

        left_items = []
        right_items = []
        current_section = None

        for row in tree.findall('.//s:row', ns):
            cells = {}
            for cell in row.findall('s:c', ns):
                r = cell.attrib.get('r')
                t = cell.attrib.get('t')
                v_elem = cell.find('s:v', ns)
                val = ""
                if v_elem is not None and v_elem.text:
                    if t == 's':
                        idx = int(v_elem.text)
                        val = shared_strings[idx] if idx < len(shared_strings) else ""
                    else:
                        val = v_elem.text
                col = ''.join([c for c in r if c.isalpha()])
                cells[col] = val.strip()

            val_a = cells.get('A', '')
            val_b = cells.get('B', '')

            if 'ESP32 Left' in val_a:
                current_section = 'left'
                continue
            elif 'ESP32 Right' in val_a:
                current_section = 'right'
                continue

            if val_a and val_b and val_a not in ('Outputs', 'Bottango Driver ESP 32s'):
                try:
                    gpio_val = int(float(val_b))
                    item = {"name": val_a, "gpio": gpio_val}
                    if current_section == 'left':
                        left_items.append(item)
                    elif current_section == 'right':
                        right_items.append(item)
                except ValueError:
                    pass

        if left_items or right_items:
            return {"left": left_items or fallback_config["left"], "right": right_items or fallback_config["right"]}
    except Exception as e:
        print(f"[Spreadsheet] Notice: Using default button configuration ({e})")

    return fallback_config


def scan_and_autodetect_esp32_ports():
    """
    Scans system serial ports for connected ESP32 or USB-to-UART bridge devices.
    Returns:
      display_options: list of human-readable labels for Comboboxes
      device_map: dict mapping label -> raw device name (e.g. 'COM3')
      detected_left_label: label for auto-detected Left ESP32
      detected_right_label: label for auto-detected Right ESP32
    """
    esp32_keywords = [
        "cp210", "ch340", "ch341", "ft232", "esp32", "usb-serial", "usb serial",
        "silicon labs", "uart", "serial port", "prolific"
    ]

    display_options = ["None (Simulation Mode)"]
    device_map = {"None (Simulation Mode)": None}
    esp32_candidate_labels = []

    try:
        import serial.tools.list_ports
        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            dev = p.device
            desc = p.description or ""
            label = f"{dev} ({desc})" if desc and desc != dev else dev
            display_options.append(label)
            device_map[label] = dev

            combined = f"{dev} {desc}".lower()
            if any(k in combined for k in esp32_keywords):
                esp32_candidate_labels.append(label)
            else:
                esp32_candidate_labels.append(label)
    except Exception as e:
        print(f"[COM Scan Error] {e}")

    detected_left_label = esp32_candidate_labels[0] if len(esp32_candidate_labels) > 0 else "None (Simulation Mode)"
    detected_right_label = esp32_candidate_labels[1] if len(esp32_candidate_labels) > 1 else "None (Simulation Mode)"

    return display_options, device_map, detected_left_label, detected_right_label


class ESP32PulseWindow:
    def __init__(self, audio_loop_instance):
        self.audio_loop = audio_loop_instance
        self.root = None
        self.status_label = None
        self.config = load_esp32_button_config()
        self.left_combo = None
        self.right_combo = None
        self.button_states = {}
        self.buttons = {}
        self.port_device_map = {}

    def start_gui(self):
        import threading
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def _run(self):
        import tkinter as tk
        from tkinter import ttk
        import threading

        self.root = tk.Tk()
        self.root.title("Thinker Window - Birds Dual ESP32 Controller")
        self.root.geometry("880x660")
        self.root.configure(bg="#0f172a")

        # Styling
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#0f172a")
        style.configure("TLabelframe", background="#0f172a", foreground="#38bdf8", bordercolor="#334155")
        style.configure("TLabelframe.Label", background="#0f172a", foreground="#38bdf8", font=("Segoe UI", 11, "bold"))
        style.configure("TLabel", background="#0f172a", foreground="#f8fafc", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"), foreground="#38bdf8")
        style.configure("SubHeader.TLabel", font=("Segoe UI", 9), foreground="#94a3b8")

        # Header Frame
        header_frame = ttk.Frame(self.root, padding=12)
        header_frame.pack(fill="x")

        title_lbl = ttk.Label(header_frame, text="🦜 Thinker Window - Birds Dual ESP32 Controller", style="Header.TLabel")
        title_lbl.pack(anchor="w")

        # COM Ports Selection Bar
        ports_frame = ttk.Frame(header_frame, padding=(0, 8, 0, 0))
        ports_frame.pack(fill="x")

        # Left COM selector
        ttk.Label(ports_frame, text="ESP32 Left:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.left_combo = ttk.Combobox(ports_frame, state="readonly", width=22)
        self.left_combo.grid(row=0, column=1, sticky="w", padx=(0, 10))

        # Right COM selector
        ttk.Label(ports_frame, text="ESP32 Right:").grid(row=0, column=2, sticky="w", padx=(0, 5))
        self.right_combo = ttk.Combobox(ports_frame, state="readonly", width=22)
        self.right_combo.grid(row=0, column=3, sticky="w", padx=(0, 10))

        def refresh_com_ports(auto_connect=False):
            options, dev_map, auto_l, auto_r = scan_and_autodetect_esp32_ports()
            self.port_device_map = dev_map

            self.left_combo['values'] = options
            self.right_combo['values'] = options

            curr_l_dev = self.audio_loop.esp32_left_port
            curr_r_dev = self.audio_loop.esp32_right_port

            match_l = [lbl for lbl, dev in dev_map.items() if dev == curr_l_dev] if curr_l_dev else []
            if match_l:
                self.left_combo.set(match_l[0])
            elif auto_l in options:
                self.left_combo.set(auto_l)
            else:
                self.left_combo.set("None (Simulation Mode)")

            match_r = [lbl for lbl, dev in dev_map.items() if dev == curr_r_dev] if curr_r_dev else []
            if match_r:
                self.right_combo.set(match_r[0])
            elif auto_r in options:
                self.right_combo.set(auto_r)
            else:
                self.right_combo.set("None (Simulation Mode)")

            if auto_connect:
                on_update_ports()
            else:
                self.update_status(f"COM Ports scanned. Found {len(options)-1} serial port(s).")

        def on_update_ports():
            sel_l_lbl = self.left_combo.get()
            sel_r_lbl = self.right_combo.get()
            dev_l = self.port_device_map.get(sel_l_lbl, sel_l_lbl)
            dev_r = self.port_device_map.get(sel_r_lbl, sel_r_lbl)
            _, msg_l = self.audio_loop.connect_esp32("left", dev_l)
            _, msg_r = self.audio_loop.connect_esp32("right", dev_r)
            self.update_status(f"{msg_l} | {msg_r}")

        connect_btn = tk.Button(
            ports_frame,
            text="Connect Ports",
            font=("Segoe UI", 9, "bold"),
            bg="#0284c7",
            fg="#ffffff",
            activebackground="#0369a1",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
            padx=8,
            command=on_update_ports
        )
        connect_btn.grid(row=0, column=4, padx=3)

        scan_btn = tk.Button(
            ports_frame,
            text="🔄 Rescan Ports",
            font=("Segoe UI", 9, "bold"),
            bg="#334155",
            fg="#f8fafc",
            activebackground="#475569",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
            padx=8,
            command=lambda: refresh_com_ports(auto_connect=True)
        )
        scan_btn.grid(row=0, column=5, padx=3)

        # Status readout
        self.status_label = ttk.Label(header_frame, text="Status: Ready for commands", font=("Segoe UI", 10, "italic"), foreground="#a855f7")
        self.status_label.pack(anchor="w", pady=(6, 0))

        # Perform initial scan & auto-connect
        refresh_com_ports(auto_connect=True)
        self.status_label.pack(anchor="w", pady=(6, 0))

        # Divider
        ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=15, pady=2)

        # Main Container split into Left & Right columns
        main_container = ttk.Frame(self.root, padding=10)
        main_container.pack(fill="both", expand=True)

        main_container.columnconfigure(0, weight=1)
        main_container.columnconfigure(1, weight=1)

        # --- LEFT SIDE PANEL ---
        left_box = ttk.LabelFrame(main_container, text=" 👈 ESP32 Left Board (11 Functions) ", padding=10)
        left_box.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        left_box.columnconfigure((0, 1), weight=1)

        self.buttons = {}
        self.button_states = {}

        for idx, item in enumerate(self.config.get("left", [])):
            r = idx // 2
            c = idx % 2
            board = "left"
            gpio = item['gpio']
            name = item['name']
            self.button_states[(board, gpio)] = False
            btn_text = f"{name}\n(GPIO {gpio}) [OFF]"
            btn = tk.Button(
                left_box,
                text=btn_text,
                font=("Segoe UI", 9, "bold"),
                bg="#1e293b",
                fg="#38bdf8",
                activebackground="#0284c7",
                activeforeground="#ffffff",
                relief="flat",
                bd=1,
                cursor="hand2",
                height=2,
                command=lambda b=board, g=gpio, n=name: self.on_button_clicked(b, g, n)
            )
            btn.grid(row=r, column=c, padx=3, pady=3, sticky="nsew")
            self.buttons[(board, gpio)] = btn

        # --- RIGHT SIDE PANEL ---
        right_box = ttk.LabelFrame(main_container, text=" 👉 ESP32 Right Board (11 Functions) ", padding=10)
        right_box.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        right_box.columnconfigure((0, 1), weight=1)

        for idx, item in enumerate(self.config.get("right", [])):
            r = idx // 2
            c = idx % 2
            board = "right"
            gpio = item['gpio']
            name = item['name']
            self.button_states[(board, gpio)] = False
            btn_text = f"{name}\n(GPIO {gpio}) [OFF]"
            btn = tk.Button(
                right_box,
                text=btn_text,
                font=("Segoe UI", 9, "bold"),
                bg="#1e293b",
                fg="#a855f7",
                activebackground="#7e22ce",
                activeforeground="#ffffff",
                relief="flat",
                bd=1,
                cursor="hand2",
                height=2,
                command=lambda b=board, g=gpio, n=name: self.on_button_clicked(b, g, n)
            )
            btn.grid(row=r, column=c, padx=3, pady=3, sticky="nsew")
            self.buttons[(board, gpio)] = btn

        # Center window on screen
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        self.root.mainloop()

    def update_status(self, text):
        if self.status_label and self.root:
            try:
                self.root.after(0, lambda: self.status_label.config(text=f"Status: {text}"))
            except Exception:
                pass

    def on_button_clicked(self, board, gpio, name):
        import threading
        current_state = self.button_states.get((board, gpio), False)
        new_state = not current_state
        self.button_states[(board, gpio)] = new_state

        state_label = "ON" if new_state else "OFF"
        btn = self.buttons.get((board, gpio))

        if btn:
            if new_state:
                btn.config(
                    text=f"{name}\n(GPIO {gpio}) [ON]",
                    bg="#16a34a",
                    fg="#ffffff",
                    activebackground="#15803d",
                    activeforeground="#ffffff"
                )
            else:
                default_fg = "#38bdf8" if board == "left" else "#a855f7"
                default_active_bg = "#0284c7" if board == "left" else "#7e22ce"
                btn.config(
                    text=f"{name}\n(GPIO {gpio}) [OFF]",
                    bg="#1e293b",
                    fg=default_fg,
                    activebackground=default_active_bg,
                    activeforeground="#ffffff"
                )

        self.update_status(f"Turning {board.title()}: '{name}' (GPIO {gpio}) -> {state_label}...")

        def _execute():
            res = self.audio_loop.trigger_esp32_gpio(board, gpio, name, state=new_state)
            status_str = res.get("message", f"Triggered {name} -> {state_label}")
            self.update_status(status_str)

        threading.Thread(target=_execute, daemon=True).start()

    def on_pulse_clicked(self, count):
        pass

    def on_turn_on(self):
        pass

    def on_turn_off(self):
        pass


class AudioLoop:
    def __init__(self, video_mode=DEFAULT_MODE, camera_idx=0, mic_idx=None, speaker_idx=None, voice_name="Zephyr", esp32_port=None, esp32_left_port=None, esp32_right_port=None, tello_ip="192.168.10.1", tello_port=8889):
        self.video_mode = video_mode
        self.camera_idx = camera_idx
        self.mic_idx = mic_idx
        self.speaker_idx = speaker_idx
        self.voice_name = voice_name
        self.esp32_left_port = esp32_left_port or esp32_port
        self.esp32_right_port = esp32_right_port
        self.esp32_port = self.esp32_left_port
        self.serial_left = None
        self.serial_right = None
        self.serial_conn = None
        self.tello = TelloController(ip=tello_ip, port=tello_port)
        self.leviton = LevitonController()
        self.ewelink = EwelinkController()

        self.audio_in_queue = None
        self.out_queue = None

        self.session = None

        self.send_text_task = None
        self.receive_audio_task = None
        self.play_audio_task = None

        self.audio_stream = None
        self.playing_audio = False

    def connect_esp32(self, board: str, port_name: str):
        import serial
        board = board.lower()
        if port_name in (None, "None (Simulation Mode)", "None"):
            if board == "left":
                if self.serial_left and self.serial_left.is_open:
                    try:
                        self.serial_left.close()
                    except Exception:
                        pass
                self.serial_left = None
                self.esp32_left_port = None
                self.esp32_port = None
                self.serial_conn = None
            else:
                if self.serial_right and self.serial_right.is_open:
                    try:
                        self.serial_right.close()
                    except Exception:
                        pass
                self.serial_right = None
                self.esp32_right_port = None
            return True, f"ESP32 {board.title()} set to Simulation Mode"

        try:
            conn = serial.Serial()
            conn.port = port_name
            conn.baudrate = 115200
            conn.timeout = 1
            conn.dtr = False
            conn.rts = False
            conn.open()

            if board == "left":
                if self.serial_left and self.serial_left.is_open:
                    try:
                        self.serial_left.close()
                    except Exception:
                        pass
                self.serial_left = conn
                self.esp32_left_port = port_name
                self.esp32_port = port_name
                self.serial_conn = conn
            else:
                if self.serial_right and self.serial_right.is_open:
                    try:
                        self.serial_right.close()
                    except Exception:
                        pass
                self.serial_right = conn
                self.esp32_right_port = port_name
                if not self.serial_conn or not self.serial_conn.is_open:
                    self.serial_conn = conn
            return True, f"Connected ESP32 {board.title()} on {port_name}"
        except Exception as e:
            return False, f"Failed ESP32 {board.title()} ({port_name}): {e}"

    def trigger_esp32_gpio(self, board: str, gpio: int, function_name: str, state: bool = None) -> dict:
        board = board.lower()
        conn = self.serial_left if board == "left" else self.serial_right
        if not conn or not conn.is_open:
            conn = self.serial_conn

        if state is True or state == 1 or state == "1":
            cmd_str = f"{gpio}:1\r\n"
        elif state is False or state == 0 or state == "0":
            cmd_str = f"{gpio}:0\r\n"
        elif state == "PULSE" or state == "pulse":
            cmd_str = f"{gpio}:PULSE\r\n"
        else:
            cmd_str = f"{gpio}\r\n"

        if conn and conn.is_open:
            try:
                cmd = cmd_str.encode("utf-8")
                conn.write(cmd)
                conn.flush()
                state_desc = "PULSE" if (state == "PULSE" or state == "pulse") else ("ON" if (state is True or state == 1 or state == "1") else ("OFF" if (state is False or state == 0 or state == "0") else "TOGGLE"))
                msg = f"Sent trigger to ESP32 {board.title()}: '{function_name}' ({state_desc} GPIO {gpio})"
                print(f"\n[ESP32-{board.upper()}] {msg}")
                return {"status": "success", "board": board, "gpio": gpio, "message": msg, "state": state}
            except Exception as e:
                err_msg = f"Error writing to ESP32 {board.title()} (GPIO {gpio}): {e}"
                print(f"\n[ESP32-{board.upper()}] {err_msg}")
                return {"status": "error", "message": err_msg}
        else:
            state_desc = "PULSE" if (state == "PULSE" or state == "pulse") else ("ON" if (state is True or state == 1 or state == "1") else ("OFF" if (state is False or state == 0 or state == "0") else "TOGGLE"))
            sim_msg = f"[Simulated ESP32 {board.title()}] Triggered '{function_name}' ({state_desc}) on GPIO {gpio}"
            print(f"\n{sim_msg}")
            return {"status": "success", "board": board, "gpio": gpio, "simulated": True, "message": sim_msg, "state": state}

    def set_led_state(self, state: bool) -> dict:
        return self.trigger_esp32_gpio("left", 2, "Builtin LED ON" if state else "Builtin LED OFF")

    def pulse_led(self, count: int = 1, gpio: int = None, duration_ms: int = 500) -> dict:
        import time
        target_gpio = gpio if gpio is not None else 2
        for _ in range(count):
            self.trigger_esp32_gpio("left", target_gpio, f"Pulse GPIO {target_gpio}", state="PULSE")
        return {"status": "success", "pulse_count": count, "gpio": target_gpio}

    async def pulse_led_async(self, count: int = 1, gpio: int = None, duration_ms: int = 500) -> dict:
        return await asyncio.to_thread(self.pulse_led, count, gpio, duration_ms)

    async def send_tello_command(self, command: str) -> dict:
        return await asyncio.to_thread(self.tello.send_cmd, command)

    async def set_leviton_light_state(self, switch_name: str, state: bool, brightness: int = None) -> dict:
        return await asyncio.to_thread(self.leviton.set_light_state, switch_name, state, brightness)

    async def set_ewelink_device_state(self, device_name: str, state: bool) -> dict:
        return await asyncio.to_thread(self.ewelink.set_device_state, device_name, state)


    async def send_text(self):
        while True:
            text = await asyncio.to_thread(
                input,
                "message > ",
            )
            if text.lower() == "q":
                break
            if self.session is not None:
                await self.session.send(input=text or ".", end_of_turn=True)

    def _camera_thread_loop(self, loop):
        import time
        import mediapipe as mp
        
        mp_hands = mp.solutions.hands
        hands = mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        
        hand_detected_start_time = None
        hand_lost_start_time = None
        prompt_sent = False
        
        cap = cv2.VideoCapture(self.camera_idx)
        last_send_time = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            
            cv2.imshow("Camera", frame)
            # waitKey is required to update the cv2 window. 
            # If 'q' is pressed, it will break but we won't exit the program, 
            # just stop the camera thread.
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
            # Convert BGR to RGB color space for MediaPipe (and PIL later)
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Run hand detection
            results = hands.process(frame_rgb)
            hand_present = results.multi_hand_landmarks is not None
            
            current_time = time.time()
            if hand_present:
                hand_lost_start_time = None
                if hand_detected_start_time is None:
                    hand_detected_start_time = current_time
                
                # If hand has been detected continuously for 0.5 seconds and prompt hasn't been sent yet
                if not prompt_sent and (current_time - hand_detected_start_time >= 0.5):
                    finger_count = count_fingers(results.multi_hand_landmarks[0])
                    print(f"\n[Camera Thread] Hand detected with {finger_count} fingers! Pulsing GPIO {finger_count} on and off 1 time.")
                    
                    # Direct hardware pulse on target GPIO pin equal to finger_count
                    self.pulse_led(count=1, gpio=finger_count)

                    trigger_msg = {
                        "type": "text",
                        "data": f"System: The user just held up their hand to the camera showing exactly {finger_count} finger{'s' if finger_count != 1 else ''}. GPIO pin {finger_count} was pulsed on and off 1 time. Please confirm to the user that {finger_count} finger{'s' if finger_count != 1 else ''} was detected and GPIO pin {finger_count} was pulsed on and off 1 time."
                    }
                    if self.out_queue is not None and not self.out_queue.full():
                        loop.call_soon_threadsafe(self.out_queue.put_nowait, trigger_msg)
                    prompt_sent = True
                    last_send_time = 0  # Force an immediate frame send
            else:
                hand_detected_start_time = None
                if hand_lost_start_time is None:
                    hand_lost_start_time = current_time
                
                # If hand has been gone continuously for 1.0 seconds, reset prompt_sent
                if prompt_sent and (current_time - hand_lost_start_time >= 1.0):
                    print("\n[Camera Thread] Hand removed for 1.0 seconds. Resetting gesture trigger.")
                    prompt_sent = False
                    
            if current_time - last_send_time >= 1.0:
                last_send_time = current_time
                
                img = PIL.Image.fromarray(frame_rgb)
                img.thumbnail([1024, 1024])

                image_io = io.BytesIO()
                img.save(image_io, format="jpeg")
                image_io.seek(0)

                mime_type = "image/jpeg"
                image_bytes = image_io.read()
                msg = {"type": "video", "mime_type": mime_type, "data": image_bytes}
                
                if self.out_queue is not None and not self.out_queue.full():
                    loop.call_soon_threadsafe(self.out_queue.put_nowait, msg)

        cap.release()
        cv2.destroyAllWindows()

    async def get_frames(self):
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._camera_thread_loop, loop)

    def _get_screen(self):
        try:
            import mss  # pytype: disable=import-error # pylint: disable=g-import-not-at-top
        except ImportError as e:
            raise ImportError("Please install mss package using 'pip install mss'") from e
        sct = mss.mss()
        monitor = sct.monitors[0]

        i = sct.grab(monitor)

        mime_type = "image/jpeg"
        image_bytes = mss.tools.to_png(i.rgb, i.size)
        img = PIL.Image.open(io.BytesIO(image_bytes))

        image_io = io.BytesIO()
        img.save(image_io, format="jpeg")
        image_io.seek(0)

        image_bytes = image_io.read()
        return {"type": "video", "mime_type": mime_type, "data": image_bytes}

    async def get_screen(self):

        while True:
            frame = await asyncio.to_thread(self._get_screen)
            if frame is None:
                break

            await asyncio.sleep(1.0)

            if self.out_queue is not None:
                await self.out_queue.put(frame)

    async def send_realtime(self):
        while True:
            if self.out_queue is not None:
                msg = await self.out_queue.get()
                if self.session is not None:
                    if msg.get("type") == "audio":
                        await self.session.send_realtime_input(audio=types.Blob(data=msg["data"], mime_type=msg["mime_type"]))
                    elif msg.get("type") == "video":
                        await self.session.send_realtime_input(video=types.Blob(data=msg["data"], mime_type=msg["mime_type"]))
                    elif msg.get("type") == "text":
                        await self.session.send(input=msg["data"], end_of_turn=True)
                    else:
                        await self.session.send(input=msg)

    async def listen_audio(self):
        mic_info = pya.get_default_input_device_info()
        input_device_index = self.mic_idx if self.mic_idx is not None else mic_info["index"]
        self.audio_stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=input_device_index,
            frames_per_buffer=CHUNK_SIZE,
        )
        if __debug__:
            kwargs = {"exception_on_overflow": False}
        else:
            kwargs = {}
        while True:
            data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, **kwargs)
            if self.out_queue is not None and not self.playing_audio:
                await self.out_queue.put({"type": "audio", "data": data, "mime_type": "audio/pcm;rate=16000"})

    async def receive_audio(self):
        "Background task to reads from the websocket and write pcm chunks to the output queue"
        while True:
            if self.session is not None:
                turn = self.session.receive()
                async for response in turn:
                    if response.tool_call:
                        function_responses = []
                        for fc in response.tool_call.function_calls:
                            if fc.name == "set_led_state":
                                state = fc.args.get("state")
                                result = self.set_led_state(state)
                                function_responses.append(
                                    types.FunctionResponse(
                                        name=fc.name,
                                        response=result,
                                        id=fc.id
                                    )
                                )
                            elif fc.name == "send_tello_command":
                                command = fc.args.get("command")
                                result = await self.send_tello_command(command)
                                function_responses.append(
                                    types.FunctionResponse(
                                        name=fc.name,
                                        response=result,
                                        id=fc.id
                                    )
                                )
                            elif fc.name == "set_leviton_light_state":
                                switch_name = fc.args.get("switch_name")
                                state = fc.args.get("state")
                                brightness = fc.args.get("brightness")
                                result = await self.set_leviton_light_state(switch_name, state, brightness)
                                function_responses.append(
                                    types.FunctionResponse(
                                        name=fc.name,
                                        response=result,
                                        id=fc.id
                                    )
                                )
                            elif fc.name == "set_ewelink_device_state":
                                device_name = fc.args.get("device_name")
                                state = fc.args.get("state")
                                result = await self.set_ewelink_device_state(device_name, state)
                                function_responses.append(
                                    types.FunctionResponse(
                                        name=fc.name,
                                        response=result,
                                        id=fc.id
                                    )
                                )
                            elif fc.name == "pulse_led":
                                count = fc.args.get("count", 1)
                                gpio = fc.args.get("gpio", 2)
                                duration_ms = fc.args.get("duration_ms", 500)
                                result = await self.pulse_led_async(count=count, gpio=gpio, duration_ms=duration_ms)
                                function_responses.append(
                                    types.FunctionResponse(
                                        name=fc.name,
                                        response=result,
                                        id=fc.id
                                    )
                                )
                        if function_responses:
                            await self.session.send_tool_response(function_responses=function_responses)
                        continue
                    if data := response.data:
                        self.audio_in_queue.put_nowait(data)
                        continue
                    if text := response.text:
                        print(text, end="")

                # If you interrupt the model, it sends a turn_complete.
                # For interruptions to work, we need to stop playback.
                # So empty out the audio queue because it may have loaded
                # much more audio than has played yet.
                while not self.audio_in_queue.empty():
                    self.audio_in_queue.get_nowait()

    async def play_audio(self):
        output_device_index = self.speaker_idx
        kwargs = {"output_device_index": output_device_index} if output_device_index is not None else {}
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
            **kwargs
        )
        while True:
            if self.audio_in_queue is not None:
                bytestream = await self.audio_in_queue.get()
                self.playing_audio = True
                await asyncio.to_thread(stream.write, bytestream)
                if self.audio_in_queue.empty():
                    self.playing_audio = False

    async def run(self):
        if self.esp32_left_port:
            self.connect_esp32("left", self.esp32_left_port)
        if self.esp32_right_port:
            self.connect_esp32("right", self.esp32_right_port)

        if not self.esp32_left_port and not self.esp32_right_port:
            print("No ESP32 ports specified. Running in simulation mode.")

        # Launch Thinker GUI window
        try:
            gui_window = ESP32PulseWindow(self)
            gui_window.start_gui()
            print("[GUI] Thinker Window launched.")
        except Exception as gui_err:
            print(f"[GUI] Could not launch Thinker window: {gui_err}")

        try:
            enable_esp32 = True
            async with (
                client.aio.live.connect(model=MODEL, config=get_config(self.voice_name, enable_esp32=enable_esp32)) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session

                self.audio_in_queue = asyncio.Queue()
                self.out_queue = asyncio.Queue(maxsize=5)

                send_text_task = tg.create_task(self.send_text())
                tg.create_task(self.send_realtime())
                tg.create_task(self.listen_audio())
                if self.video_mode == "camera":
                    tg.create_task(self.get_frames())
                elif self.video_mode == "screen":
                    tg.create_task(self.get_screen())

                tg.create_task(self.receive_audio())
                tg.create_task(self.play_audio())

                await send_text_task
                raise asyncio.CancelledError("User requested exit")

        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            if self.audio_stream is not None:
                self.audio_stream.close()
                traceback.print_exception(EG)
        finally:
            if self.serial_conn and self.serial_conn.is_open:
                self.serial_conn.close()
                print("ESP32 serial connection closed.")


def choose_audio_device(pya_instance, is_input):
    devices = []
    for i in range(pya_instance.get_device_count()):
        info = pya_instance.get_device_info_by_index(i)
        if is_input and info.get('maxInputChannels') > 0:
            devices.append((i, info.get('name')))
        elif not is_input and info.get('maxOutputChannels') > 0:
            devices.append((i, info.get('name')))
            
    device_type = "microphone" if is_input else "speaker"
    print(f"\nAvailable {device_type}s:")
    for idx, name in devices:
        print(f"[{idx}] {name}")
        
    try:
        default_info = pya_instance.get_default_input_device_info() if is_input else pya_instance.get_default_output_device_info()
        default_idx = default_info["index"]
    except Exception:
        default_idx = devices[0][0] if devices else None

    if default_idx is not None:
        print(f"Default is [{default_idx}].")
        
    while True:
        try:
            choice = input(f"Select {device_type} by index [default: press Enter]: ").strip()
            if not choice:
                return default_idx
            choice = int(choice)
            if any(choice == d[0] for d in devices):
                return choice
            else:
                print("Invalid index, try again.")
        except ValueError:
            print("Please enter a valid number.")

def choose_camera():
    print("\nSearching for available cameras (this may take a moment)...")
    available_cameras = []
    # Test first 4 indices
    for i in range(4):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                available_cameras.append(i)
            cap.release()
            
    if not available_cameras:
        print("No cameras found.")
        return None
        
    print("Available cameras:")
    for idx in available_cameras:
        print(f"[{idx}] Camera {idx}")
        
    while True:
        try:
            choice = input("Select camera by index [default: press Enter]: ").strip()
            if not choice:
                return available_cameras[0]
            choice = int(choice)
            if choice in available_cameras:
                return choice
            else:
                print("Invalid index, try again.")
        except ValueError:
            print("Please enter a valid number.")

def choose_voice():
    voices = ["Aoede", "Charon", "Kore", "Puck", "Zephyr"]
    print("\nAvailable voices:")
    for idx, name in enumerate(voices):
        print(f"[{idx}] {name}")
    print("Default is [4] (Zephyr).")
    
    while True:
        try:
            choice = input("Select voice by index [default: press Enter]: ").strip()
            if not choice:
                return "Zephyr"
            choice = int(choice)
            if 0 <= choice < len(voices):
                return voices[choice]
            else:
                print("Invalid index, try again.")
        except ValueError:
            print("Please enter a valid number.")

def choose_esp32_port():
    try:
        import serial.tools.list_ports
        ports = list(serial.tools.list_ports.comports())
    except ImportError:
        print("pyserial is not installed or not available. Skipping ESP32 port selection.")
        return None

    print("\nAvailable serial ports for ESP32 dev module:")
    print("[N] None (Skip ESP32 connection)")
    for idx, p in enumerate(ports):
        print(f"[{idx}] {p.device} - {p.description}")
    
    while True:
        try:
            choice = input("Select ESP32 port by index [default: None]: ").strip()
            if not choice or choice.upper() == 'N':
                return None
            choice_idx = int(choice)
            if 0 <= choice_idx < len(ports):
                return ports[choice_idx].device
            else:
                print("Invalid index, try again.")
        except ValueError:
            print("Please enter a valid number or 'N'.")

def choose_tello_port():
    while True:
        port_input = input("Enter Tello drone port [default: 8889]: ").strip()
        if not port_input:
            return 8889
        try:
            port = int(port_input)
            if 1 <= port <= 65535:
                return port
            else:
                print("Invalid port number. Must be between 1 and 65535.")
        except ValueError:
            print("Please enter a valid integer port number.")

def choose_tello_ip(port=8889):
    print("\nScanning local network for active devices...")
    import socket
    import subprocess
    import re
    import concurrent.futures
    import time

    # Find local subnets
    local_ips = []
    try:
        hostname = socket.gethostname()
        local_ips = socket.gethostbyname_ex(hostname)[2]
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        primary_ip = s.getsockname()[0]
        s.close()
        if primary_ip not in local_ips:
            local_ips.append(primary_ip)
    except Exception:
        pass

    subnets = set()
    for ip in local_ips:
        if ip.startswith("127."):
            continue
        parts = ip.split(".")
        if len(parts) == 4:
            subnets.add(f"{parts[0]}.{parts[1]}.{parts[2]}.")

    # Common subnets to search
    subnets.add("192.168.10.")
    subnets.add("192.168.1.")
    subnets.add("192.168.0.")

    target_ips = []
    for subnet in subnets:
        for i in range(1, 255):
            target_ips.append(f"{subnet}{i}")

    def ping_udp(ip):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.1)
            sock.sendto(b'', (ip, 9))
            sock.close()
        except Exception:
            pass

    # Rapid UDP ping to populate OS ARP cache
    with concurrent.futures.ThreadPoolExecutor(max_workers=80) as executor:
        executor.map(ping_udp, target_ips)

    time.sleep(0.4)

    discovered = []
    tello_drones = []
    try:
        output = subprocess.check_output(["arp", "-a"]).decode('utf-8', errors='ignore')
        ip_mac_pattern = re.compile(
            r"^\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+([0-9a-fA-F:-]{17})\s+(\w+)",
            re.MULTILINE
        )

        def check_if_tello(ip):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(0.3)
                sock.sendto(b'command', (ip, port))
                data, _ = sock.recvfrom(1024)
                if b'ok' in data.lower():
                    return ip
            except Exception:
                pass
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
            return None

        candidate_ips = []
        for match in ip_mac_pattern.finditer(output):
            ip, mac, link_type = match.groups()
            if ip.startswith("224.") or ip.startswith("239.") or ip.endswith(".255") or ip == "255.255.255.255":
                continue

            in_subnet = False
            for subnet in subnets:
                if ip.startswith(subnet):
                    in_subnet = True
                    break
            if in_subnet:
                candidate_ips.append((ip, mac))

        tello_detected = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=30) as checker:
            check_results = checker.map(check_if_tello, [ip for ip, _ in candidate_ips])
            for res in check_results:
                if res:
                    tello_detected.append(res)

        for ip, mac in candidate_ips:
            if ip in tello_detected:
                discovered.append(f"{ip} ({mac}) [Tello Drone]")
                tello_drones.append(ip)
            else:
                discovered.append(f"{ip} ({mac})")
    except Exception as e:
        print(f"Error scanning network: {e}")

    if discovered:
        print("\nActive devices found on the local network:")
        for idx, dev in enumerate(discovered, 1):
            print(f"  {idx}. {dev}")
    else:
        print("\nNo active devices found on the local network.")

    while True:
        default_ip = tello_drones[0] if tello_drones else "192.168.10.1"
        ip_input = input(f"\nEnter Tello drone IP address [default: {default_ip}]: ").strip()
        if not ip_input:
            return default_ip
        # In case the user typed/selected something with a MAC address suffix
        match = re.match(r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", ip_input)
        if match:
            return match.group(1)
        else:
            print("Invalid IP address format. Please enter a valid IPv4 address (e.g., 192.168.10.1).")

def show_settings_dialog(pya_instance, default_mode="camera"):
    try:
        import tkinter as tk
        from tkinter import ttk
        import re
    except ImportError:
        print("Tkinter not available. Falling back to command-line prompts.")
        return "fallback"

    # 1. Microphones
    mic_devices = []
    default_mic_idx = None
    try:
        default_mic_info = pya_instance.get_default_input_device_info()
        default_mic_idx = default_mic_info["index"]
    except Exception:
        pass

    for i in range(pya_instance.get_device_count()):
        try:
            info = pya_instance.get_device_info_by_index(i)
            if info.get('maxInputChannels') > 0:
                mic_devices.append((i, info.get('name')))
        except Exception:
            pass

    # 2. Speakers
    speaker_devices = []
    default_speaker_idx = None
    try:
        default_speaker_info = pya_instance.get_default_output_device_info()
        default_speaker_idx = default_speaker_info["index"]
    except Exception:
        pass

    for i in range(pya_instance.get_device_count()):
        try:
            info = pya_instance.get_device_info_by_index(i)
            if info.get('maxOutputChannels') > 0:
                speaker_devices.append((i, info.get('name')))
        except Exception:
            pass

    # 3. Cameras
    print("Scanning for available cameras for the settings window...")
    available_cameras = []
    for i in range(4):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                available_cameras.append(i)
            cap.release()

    # 4. Voices
    voices = ["Aoede", "Charon", "Kore", "Puck", "Zephyr"]

    # 5. COM/Serial ports
    com_ports = []
    try:
        import serial.tools.list_ports
        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            com_ports.append(p.device)
    except Exception:
        pass

    result = {}
    started = False

    root = tk.Tk()
    root.title("Gemini Live Session Settings")
    root.geometry("480x500")
    root.resizable(False, False)

    # Use native style theme if available
    style = ttk.Style()
    try:
        style.theme_use('vista' if 'vista' in style.theme_names() else 'clam')
    except Exception:
        pass

    main_frame = ttk.Frame(root, padding="20 20 20 20")
    main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    title_label = ttk.Label(main_frame, text="Configure Live Session Preferences", font=("Helvetica", 14, "bold"))
    title_label.grid(row=0, column=0, columnspan=2, pady=(0, 15), sticky=tk.W)

    # Microphone Selector
    ttk.Label(main_frame, text="Microphone:").grid(row=1, column=0, sticky=tk.W, pady=8)
    mic_options = [f"{name} (Index {idx})" for idx, name in mic_devices]
    mic_combo = ttk.Combobox(main_frame, values=mic_options, state="readonly", width=42)
    mic_combo.grid(row=1, column=1, sticky=tk.W, pady=8)
    
    default_mic_str = ""
    for idx, name in mic_devices:
        if idx == default_mic_idx:
            default_mic_str = f"{name} (Index {idx})"
            break
    if default_mic_str:
        mic_combo.set(default_mic_str)
    elif mic_options:
        mic_combo.current(0)

    # Speaker Selector
    ttk.Label(main_frame, text="Speaker:").grid(row=2, column=0, sticky=tk.W, pady=8)
    speaker_options = [f"{name} (Index {idx})" for idx, name in speaker_devices]
    speaker_combo = ttk.Combobox(main_frame, values=speaker_options, state="readonly", width=42)
    speaker_combo.grid(row=2, column=1, sticky=tk.W, pady=8)
    
    default_speaker_str = ""
    for idx, name in speaker_devices:
        if idx == default_speaker_idx:
            default_speaker_str = f"{name} (Index {idx})"
            break
    if default_speaker_str:
        speaker_combo.set(default_speaker_str)
    elif speaker_options:
        speaker_combo.current(0)

    # Video Mode Selector
    ttk.Label(main_frame, text="Video Mode:").grid(row=3, column=0, sticky=tk.W, pady=8)
    video_modes = ["Camera", "Screen Share", "None"]
    mode_combo = ttk.Combobox(main_frame, values=video_modes, state="readonly", width=42)
    mode_combo.grid(row=3, column=1, sticky=tk.W, pady=8)
    
    if default_mode == "camera":
        mode_combo.set("Camera")
    elif default_mode == "screen":
        mode_combo.set("Screen Share")
    else:
        mode_combo.set("None")

    # Camera Selector
    ttk.Label(main_frame, text="Camera Feed:").grid(row=4, column=0, sticky=tk.W, pady=8)
    camera_options = [f"Camera {idx}" for idx in available_cameras]
    if not camera_options:
        camera_options = ["No cameras detected"]
    camera_combo = ttk.Combobox(main_frame, values=camera_options, state="readonly", width=42)
    camera_combo.grid(row=4, column=1, sticky=tk.W, pady=8)
    if available_cameras:
        camera_combo.current(0)
    else:
        camera_combo.current(0)
        camera_combo.configure(state="disabled")

    # Voice Selector
    ttk.Label(main_frame, text="Gemini Voice:").grid(row=5, column=0, sticky=tk.W, pady=8)
    voice_combo = ttk.Combobox(main_frame, values=voices, state="readonly", width=42)
    voice_combo.grid(row=5, column=1, sticky=tk.W, pady=8)
    if "Zephyr" in voices:
        voice_combo.set("Zephyr")
    else:
        voice_combo.current(0)

    # 5. COM/Serial ports (Auto-Detected)
    port_options, port_device_map, auto_left_lbl, auto_right_lbl = scan_and_autodetect_esp32_ports()

    # ESP32 Left COM Port Selector
    ttk.Label(main_frame, text="ESP32 Left COM Port:").grid(row=6, column=0, sticky=tk.W, pady=6)
    left_port_combo = ttk.Combobox(main_frame, values=port_options, state="readonly", width=42)
    left_port_combo.grid(row=6, column=1, sticky=tk.W, pady=6)
    left_port_combo.set(auto_left_lbl)

    # ESP32 Right COM Port Selector
    ttk.Label(main_frame, text="ESP32 Right COM Port:").grid(row=7, column=0, sticky=tk.W, pady=6)
    right_port_combo = ttk.Combobox(main_frame, values=port_options, state="readonly", width=42)
    right_port_combo.grid(row=7, column=1, sticky=tk.W, pady=6)
    right_port_combo.set(auto_right_lbl)

    # Tello Drone IP Entry
    ttk.Label(main_frame, text="Tello Drone IP:").grid(row=8, column=0, sticky=tk.W, pady=8)
    
    tello_ip_frame = ttk.Frame(main_frame)
    tello_ip_frame.grid(row=8, column=1, sticky=tk.W, pady=8)
    
    tello_ip_combo = ttk.Combobox(tello_ip_frame, width=28, state="normal")
    tello_ip_combo.pack(side=tk.LEFT, padx=(0, 5))
    tello_ip_combo.set("192.168.10.1")
    
    # Tello Drone Port Entry
    ttk.Label(main_frame, text="Tello Drone Port:").grid(row=9, column=0, sticky=tk.W, pady=8)
    tello_port_entry = ttk.Entry(main_frame, width=45)
    tello_port_entry.grid(row=9, column=1, sticky=tk.W, pady=8)
    tello_port_entry.insert(0, "8889")
    
    def on_scan_network():
        scan_btn.configure(state="disabled", text="Scanning...")
        
        tello_port_str = tello_port_entry.get().strip()
        try:
            scan_port = int(tello_port_str)
            if not (1 <= scan_port <= 65535):
                scan_port = 8889
        except ValueError:
            scan_port = 8889
        
        def run_scan():
            import socket
            import subprocess
            import re
            import concurrent.futures
            import time
            
            # Find local subnets
            local_ips = []
            try:
                hostname = socket.gethostname()
                local_ips = socket.gethostbyname_ex(hostname)[2]
            except Exception:
                pass
            
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                primary_ip = s.getsockname()[0]
                s.close()
                if primary_ip not in local_ips:
                    local_ips.append(primary_ip)
            except Exception:
                pass
                
            subnets = set()
            for ip in local_ips:
                if ip.startswith("127."):
                    continue
                parts = ip.split(".")
                if len(parts) == 4:
                    subnets.add(f"{parts[0]}.{parts[1]}.{parts[2]}.")
            
            # Common subnets to search
            subnets.add("192.168.10.")
            subnets.add("192.168.1.")
            subnets.add("192.168.0.")
            
            target_ips = []
            for subnet in subnets:
                for i in range(1, 255):
                    target_ips.append(f"{subnet}{i}")
                    
            def ping_udp(ip):
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.settimeout(0.1)
                    sock.sendto(b'', (ip, 9))
                    sock.close()
                except Exception:
                    pass

            with concurrent.futures.ThreadPoolExecutor(max_workers=80) as executor:
                executor.map(ping_udp, target_ips)
                
            time.sleep(0.4)
            
            discovered = []
            tello_drones = []
            try:
                output = subprocess.check_output(["arp", "-a"]).decode('utf-8', errors='ignore')
                ip_mac_pattern = re.compile(
                    r"^\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+([0-9a-fA-F:-]{17})\s+(\w+)",
                    re.MULTILINE
                )
                
                def check_if_tello(ip):
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        sock.settimeout(0.3)
                        sock.sendto(b'command', (ip, scan_port))
                        data, _ = sock.recvfrom(1024)
                        if b'ok' in data.lower():
                            return ip
                    except Exception:
                        pass
                    finally:
                        try:
                            sock.close()
                        except Exception:
                            pass
                    return None

                candidate_ips = []
                for match in ip_mac_pattern.finditer(output):
                    ip, mac, link_type = match.groups()
                    if ip.startswith("224.") or ip.startswith("239.") or ip.endswith(".255") or ip == "255.255.255.255":
                        continue
                    
                    in_subnet = False
                    for subnet in subnets:
                        if ip.startswith(subnet):
                            in_subnet = True
                            break
                    if in_subnet:
                        candidate_ips.append((ip, mac))

                tello_detected = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=30) as checker:
                    check_results = checker.map(check_if_tello, [ip for ip, _ in candidate_ips])
                    for res in check_results:
                        if res:
                            tello_detected.append(res)
                
                for ip, mac in candidate_ips:
                    if ip in tello_detected:
                        discovered.append(f"{ip} ({mac}) [Tello Drone]")
                        tello_drones.append(ip)
                    else:
                        discovered.append(f"{ip} ({mac})")
            except Exception as e:
                print(f"[Network Scan] Error: {e}")
                
            root.after(0, lambda: scan_complete(discovered, tello_drones))
            
        def scan_complete(discovered, tello_drones):
            scan_btn.configure(state="normal", text="Scan Network")
            if discovered:
                print(f"\n[Network Scan] Found {len(discovered)} devices on local network:")
                for d in discovered:
                    print(f"  - {d}")
                tello_ip_combo.configure(values=discovered)
                
                if tello_drones:
                    # Select the first Tello drone automatically
                    matching_opt = [d for d in discovered if tello_drones[0] in d]
                    if matching_opt:
                        tello_ip_combo.set(matching_opt[0])
                        from tkinter import messagebox
                        messagebox.showinfo("Drone IP Found", f"Successfully found Tello drone at {tello_drones[0]}!")
            else:
                from tkinter import messagebox
                messagebox.showwarning("Scan Complete", "No active devices detected on the local network.")
                
        import threading
        threading.Thread(target=run_scan, daemon=True).start()

    scan_btn = ttk.Button(tello_ip_frame, text="Scan Network", command=on_scan_network)
    scan_btn.pack(side=tk.LEFT)


    def on_mode_change(event):
        mode = mode_combo.get()
        if mode == "Camera" and available_cameras:
            camera_combo.configure(state="readonly")
        else:
            camera_combo.configure(state="disabled")

    mode_combo.bind("<<ComboboxSelected>>", on_mode_change)
    # Initialize correct camera dropdown state
    on_mode_change(None)

    # Buttons
    button_frame = ttk.Frame(main_frame, padding=(0, 25, 0, 0))
    button_frame.grid(row=9, column=0, columnspan=2, sticky=tk.E)

    def on_start():
        nonlocal started
        
        # Extract selections
        sel_mic = mic_combo.get()
        if sel_mic:
            match = re.search(r"\(Index (\d+)\)", sel_mic)
            result["mic_idx"] = int(match.group(1)) if match else None
        else:
            result["mic_idx"] = None

        sel_speaker = speaker_combo.get()
        if sel_speaker:
            match = re.search(r"\(Index (\d+)\)", sel_speaker)
            result["speaker_idx"] = int(match.group(1)) if match else None
        else:
            result["speaker_idx"] = None

        mode_str = mode_combo.get()
        if mode_str == "Camera":
            result["video_mode"] = "camera"
        elif mode_str == "Screen Share":
            result["video_mode"] = "screen"
        else:
            result["video_mode"] = "none"

        sel_cam = camera_combo.get()
        if sel_cam and "Camera" in sel_cam:
            try:
                result["camera_idx"] = int(sel_cam.split()[-1])
            except ValueError:
                result["camera_idx"] = 0
        else:
            result["camera_idx"] = 0

        result["voice_name"] = voice_combo.get()

        sel_l_lbl = left_port_combo.get()
        result["esp32_left_port"] = port_device_map.get(sel_l_lbl)

        sel_r_lbl = right_port_combo.get()
        result["esp32_right_port"] = port_device_map.get(sel_r_lbl)
        result["esp32_port"] = result["esp32_left_port"]

        tello_ip = tello_ip_combo.get().strip()
        match = re.match(r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", tello_ip)
        tello_ip = match.group(1) if match else "192.168.10.1"
        result["tello_ip"] = tello_ip

        tello_port_str = tello_port_entry.get().strip()
        try:
            tello_port = int(tello_port_str)
            if not (1 <= tello_port <= 65535):
                raise ValueError()
        except ValueError:
            from tkinter import messagebox
            messagebox.showerror("Invalid Port", "Tello Port must be an integer between 1 and 65535.")
            return
        result["tello_port"] = tello_port

        started = True
        root.destroy()

    def on_cancel():
        root.destroy()

    start_btn = ttk.Button(button_frame, text="Start Session", command=on_start)
    start_btn.grid(row=0, column=0, padx=5)

    cancel_btn = ttk.Button(button_frame, text="Cancel", command=on_cancel)
    cancel_btn.grid(row=0, column=1, padx=5)

    root.protocol("WM_DELETE_WINDOW", on_cancel)

    # Center window
    root.update_idletasks()
    width = root.winfo_width()
    height = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f'{width}x{height}+{x}+{y}')

    # Automatically start network scan at startup
    root.after(100, on_scan_network)

    root.mainloop()

    if started:
        return result
    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        type=str,
        default=DEFAULT_MODE,
        help="pixels to stream from",
        choices=["camera", "screen", "none"],
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Force CLI configuration prompts instead of GUI window",
    )
    args = parser.parse_args()
    
    import sys
    
    settings = None
    if not args.cli:
        print("Opening settings window...")
        settings = show_settings_dialog(pya, default_mode=args.mode)
        if settings is None:
            print("Session canceled by user.")
            sys.exit(0)
            
    if settings == "fallback" or args.cli:
        print("Checking available devices via CLI...")
        mic_idx = choose_audio_device(pya, is_input=True)
        speaker_idx = choose_audio_device(pya, is_input=False)
        
        camera_idx = 0
        video_mode = args.mode
        if video_mode == "camera":
            camera_idx = choose_camera()
            if camera_idx is None:
                print("No camera found. Exiting.")
                sys.exit(1)
        voice_name = choose_voice()
        esp32_left_port = choose_esp32_port()
        esp32_right_port = choose_esp32_port()
        tello_port = choose_tello_port()
        tello_ip = choose_tello_ip(tello_port)
    else:
        mic_idx = settings["mic_idx"]
        speaker_idx = settings["speaker_idx"]
        video_mode = settings["video_mode"]
        camera_idx = settings["camera_idx"]
        voice_name = settings["voice_name"]
        esp32_left_port = settings.get("esp32_left_port")
        esp32_right_port = settings.get("esp32_right_port")
        tello_ip = settings.get("tello_ip", "192.168.10.1")
        tello_port = settings.get("tello_port", 8889)

    print("\nConnecting to Gemini...")
    main = AudioLoop(
        video_mode=video_mode,
        camera_idx=camera_idx,
        mic_idx=mic_idx,
        speaker_idx=speaker_idx,
        voice_name=voice_name,
        esp32_left_port=esp32_left_port,
        esp32_right_port=esp32_right_port,
        tello_ip=tello_ip,
        tello_port=tello_port
    )
    asyncio.run(main.run())
