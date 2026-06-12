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
                self.sock.sendto(b"command", self.drone_address)
                response, _ = self.sock.recvfrom(1024)
                print(f"[Tello] SDK response: {response.decode('utf-8').strip()}")
                self.sdk_enabled = True

            print(f"[Tello] Sending command: {command}")
            self.sock.sendto(command.encode('utf-8'), self.drone_address)
            response, _ = self.sock.recvfrom(1024)
            res_str = response.decode('utf-8').strip()
            print(f"[Tello] Response: {res_str}")
            return {"status": "success", "response": res_str}
        except (socket.timeout, socket.error) as e:
            # Fall back to simulation if the physical drone isn't reachable
            print(f"[Tello] Communication failed ({e}). Falling back to simulation for: {command}")
            self.simulated = True
            return {"status": "success", "response": "ok (simulated fallback)", "simulated": True}


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
        "You are a helpful real-time voice assistant running on the user's local computer. "
        "You have direct access to local hardware and smart devices: an onboard LED of an ESP32 microcontroller, "
        "a Tello drone, Leviton smart lights, and eWeLink (Sonoff) devices.\n\n"
        "1. ESP32 LED: You MUST use the `set_led_state` tool to control this LED whenever the user asks you to "
        "turn the LED on or off, make it blink, or change its state.\n"
        "2. Tello Drone: You MUST use the `send_tello_command` tool to control the Tello drone when the user asks you "
        "to perform actions like takeoff, landing, moving, flipping, or rotating.\n"
        "3. Leviton Lights: You MUST use the `set_leviton_light_state` tool when the user asks you to turn smart home "
        "lights on, off, or change their brightness level.\n"
        "4. eWeLink Devices: You MUST use the `set_ewelink_device_state` tool when the user asks you to turn eWeLink "
        "or Sonoff devices (plugs, switches, fans, etc.) on or off.\n\n"
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


class AudioLoop:
    def __init__(self, video_mode=DEFAULT_MODE, camera_idx=0, mic_idx=None, speaker_idx=None, voice_name="Zephyr", esp32_port=None):
        self.video_mode = video_mode
        self.camera_idx = camera_idx
        self.mic_idx = mic_idx
        self.speaker_idx = speaker_idx
        self.voice_name = voice_name
        self.esp32_port = esp32_port
        self.serial_conn = None
        self.tello = TelloController()
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

    def set_led_state(self, state: bool) -> dict:
        if self.serial_conn and self.serial_conn.is_open:
            try:
                cmd = b'1' if state else b'0'
                self.serial_conn.write(cmd)
                self.serial_conn.flush()
                status = "ON" if state else "OFF"
                print(f"\n[ESP32] Sent command to turn LED {status}")
                return {"status": "success", "led_state": status}
            except Exception as e:
                print(f"\n[ESP32] Error writing to serial: {e}")
                return {"status": "error", "message": str(e)}
        else:
            status = "ON" if state else "OFF"
            print(f"\n[Simulated ESP32] Sent command to turn LED {status} (No physical ESP32 connected)")
            return {"status": "success", "led_state": status, "simulated": True}

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
                
            current_time = time.time()
            if current_time - last_send_time >= 1.0:
                last_send_time = current_time
                
                # Convert BGR to RGB color space
                # OpenCV captures in BGR but PIL expects RGB format
                # This prevents the blue tint in the video feed
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
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
        if self.esp32_port:
            try:
                import serial
                self.serial_conn = serial.Serial(self.esp32_port, 115200, timeout=1)
                print(f"Connected to ESP32 on port {self.esp32_port}")
            except Exception as e:
                print(f"Failed to connect to ESP32 on port {self.esp32_port}: {e}")
                self.serial_conn = None
        else:
            print("No ESP32 port specified. LED control will run in simulation mode.")
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        type=str,
        default=DEFAULT_MODE,
        help="pixels to stream from",
        choices=["camera", "screen", "none"],
    )
    args = parser.parse_args()
    
    import sys
    print("Checking available devices...")
    
    mic_idx = choose_audio_device(pya, is_input=True)
    speaker_idx = choose_audio_device(pya, is_input=False)
    
    camera_idx = 0
    if args.mode == "camera":
        camera_idx = choose_camera()
        if camera_idx is None:
            print("No camera found. Exiting.")
            sys.exit(1)

    voice_name = choose_voice()
    esp32_port = choose_esp32_port()

    print("\nConnecting to Gemini...")
    main = AudioLoop(
        video_mode=args.mode,
        camera_idx=camera_idx,
        mic_idx=mic_idx,
        speaker_idx=speaker_idx,
        voice_name=voice_name,
        esp32_port=esp32_port
    )
    asyncio.run(main.run())
