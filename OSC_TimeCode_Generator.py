# Standard Library Imports
import math
import os
import re
import sys
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
import tkinter.font as tkFont

# Third-Party Imports
from pythonosc import udp_client

# --- Constants ---
DEFAULT_OSC_OUT_IP = "127.0.0.1"
DEFAULT_OSC_OUT_PORT = 9001
DEFAULT_OSC_ADDRESS = "/timecode"
GITHUB_URL = "https://github.com/BitloMedia"
BRAND_NAME = "BitloMedia"

# Precise SMPTE Framerates (Dictionary for clarity and lookup)
# Using standard representations where applicable for comments
FRAMERATES = {
    "30": 30.0,
    "29.97": 29.97002997002997,  # 30000/1001 NDF (Non-Drop Frame)
    "25": 25.0,
    "24": 24.0,
    "23.976": 23.976023976023976, # 24000/1001 NDF (Non-Drop Frame)
}
DEFAULT_FPS_LABEL = "30" # Default selection in the Combobox
DEFAULT_FPS_VALUE = FRAMERATES[DEFAULT_FPS_LABEL] # Corresponding precise value

# --- Helper Functions ---

def frames_to_tc_string(total_frames, fps):
    """Converts total frames to a 'HH:MM:SS:FF' timecode string.

    Args:
        total_frames (int): The total number of frames elapsed.
        fps (float): The precise framerate (used to determine display_fps).

    Returns:
        str: The timecode string in HH:MM:SS:FF format.
    """
    # Use the nominal integer frame rate for display calculation (e.g., 30 for 29.97)
    # This is standard practice for timecode display.
    display_fps = int(round(fps))
    if display_fps <= 0:
        return "00:00:00:00" # Avoid division by zero or invalid FPS

    # Handle potential negative frames if offset logic somehow allows it
    total_frames = max(0, total_frames)

    frame_number = total_frames % display_fps
    total_seconds = total_frames // display_fps
    seconds = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frame_number:02d}"

def tc_string_to_frames(tc_string, fps):
    """Converts an 'HH:MM:SS:FF' string to total frames based on the nominal FPS.

    Args:
        tc_string (str): The timecode string to parse.
        fps (float): The precise framerate (used to determine display_fps).

    Returns:
        int or None: The total number of frames, or None if the format is invalid.
    """
    display_fps = int(round(fps))
    if display_fps <= 0:
        return None # Cannot calculate frames with invalid FPS

    # Regex to match HH:MM:SS:FF, allowing single digits and different separators
    match = re.match(r"(\d{1,2}):(\d{1,2}):(\d{1,2})[:;.](\d{1,2})", tc_string.strip())
    if not match:
        return None # Invalid format

    try:
        h = int(match.group(1))
        m = int(match.group(2))
        s = int(match.group(3))
        f = int(match.group(4))

        # Validate time components against standard limits and display FPS
        if f >= display_fps or m >= 60 or s >= 60 or h < 0 or m < 0 or s < 0 or f < 0:
            return None # Invalid time component values

        total_seconds = h * 3600 + m * 60 + s
        total_frames = total_seconds * display_fps + f
        return total_frames
    except ValueError:
        # Should not happen with regex validation, but good practice
        return None

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller.

    Args:
        relative_path (str): The relative path to the resource file.

    Returns:
        str: The absolute path to the resource file.
    """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        # This attribute only exists when running the bundled executable
        base_path = sys._MEIPASS
    except AttributeError:
        # If not running bundled, use the script's current directory
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

# --- Main Application Class ---

class OscTimecodeGeneratorApp(tk.Tk):
    """Main application class for the OSC Timecode Generator."""

    def __init__(self):
        """Initializes the application window, data, and GUI components."""
        super().__init__()
        self.title("OSC Timecode Generator")
        self.resizable(False, False) # Prevent window resizing

        # --- Application Data ---
        self.osc_client = None
        self.is_running = False # Flag to control the timecode generation thread
        self.timecode_thread = None
        self.start_frame = 0 # Frame count to start from (based on offset)
        self.current_frame = 0 # Current running frame count
        self.fps = DEFAULT_FPS_VALUE # Actual precise FPS for timing
        self.display_fps_label = DEFAULT_FPS_LABEL # Label for display/selection

        # --- Tkinter Variables ---
        self.osc_out_ip_var = tk.StringVar(value=DEFAULT_OSC_OUT_IP)
        self.osc_out_port_var = tk.StringVar(value=str(DEFAULT_OSC_OUT_PORT))
        self.osc_address_var = tk.StringVar(value=DEFAULT_OSC_ADDRESS)
        self.fps_var = tk.StringVar(value=self.display_fps_label)
        self.speed_var = tk.DoubleVar(value=100.0) # Speed percentage (0-200)
        self.offset_var = tk.StringVar(value="00:00:00:00") # User input for start offset

        # --- GUI Component Placeholders ---
        # Initialize to None; they will be created in setup_gui
        self.status_message_label = None
        self.play_pause_button = None
        self.reset_button = None
        self.fps_combobox = None
        self.speed_slider = None
        self.speed_label = None
        self.offset_entry = None
        self.timecode_label = None

        # --- Initialization Steps ---
        self._set_window_icon()
        self.update_osc_client() # Initialize OSC client (calls update_status)
        self.setup_gui()         # Create and arrange GUI elements
        self.reset_timecode()    # Apply initial offset and set display

        # --- Window Closing Protocol ---
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _set_window_icon(self):
        """Attempts to set the application window icon."""
        try:
            # Assumes icon file is named 'app_icon.ico' and bundled correctly
            icon_path = resource_path("osc_tc_gen2.ico")
            print(f"Attempting to load window icon from: {icon_path}")
            self.iconbitmap(icon_path)
        except tk.TclError as e: # Catch specific Tkinter error for missing bitmap
             print(f"Warning: Could not set window icon (bitmap not defined?): {e}")
        except Exception as e:
            # Catch other potential errors (e.g., file not found by resource_path)
            print(f"Warning: Could not set window icon: {e}")

    def setup_gui(self):
        """Creates and arranges all the GUI widgets."""
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- OSC Configuration Section ---
        self._setup_osc_config_frame(main_frame)

        # --- Timecode Control Section ---
        self._setup_timecode_control_frame(main_frame)

        # --- Playback Control Section ---
        self._setup_playback_frame(main_frame)

        # --- Timecode Display Section ---
        self._setup_timecode_display_frame(main_frame)

        # --- Status Bar Section ---
        self._setup_status_bar(main_frame)

    def _setup_osc_config_frame(self, parent):
        """Creates the OSC Output Configuration frame."""
        osc_frame = ttk.LabelFrame(parent, text="OSC Output Configuration", padding="10")
        osc_frame.pack(side=tk.TOP, fill=tk.X, pady=5)
        osc_frame.columnconfigure(1, weight=1) # Allow IP entry to expand slightly
        osc_frame.columnconfigure(3, weight=1) # Allow Address entry to expand

        # IP Address
        ttk.Label(osc_frame, text="Target IP:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        ttk.Entry(osc_frame, textvariable=self.osc_out_ip_var, width=15).grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)

        # Port
        ttk.Label(osc_frame, text="Target Port:").grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)
        ttk.Entry(osc_frame, textvariable=self.osc_out_port_var, width=7).grid(row=0, column=3, padx=5, pady=5, sticky=tk.W)

        # OSC Address
        ttk.Label(osc_frame, text="Address:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        ttk.Entry(osc_frame, textvariable=self.osc_address_var).grid(row=1, column=1, columnspan=3, padx=5, pady=5, sticky=tk.EW)

        # Apply Button
        ttk.Button(osc_frame, text="Apply IP/Port", command=self.update_osc_client).grid(row=0, column=4, rowspan=2, padx=5, pady=5, sticky="nsew") # Fill vertically

    def _setup_timecode_control_frame(self, parent):
        """Creates the Timecode Control frame."""
        tc_control_frame = ttk.LabelFrame(parent, text="Timecode Control", padding="10")
        tc_control_frame.pack(side=tk.TOP, fill=tk.X, pady=10)
        tc_control_frame.columnconfigure(1, weight=1) # Allow slider to expand

        # Framerate
        ttk.Label(tc_control_frame, text="Framerate (FPS):").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.fps_combobox = ttk.Combobox(
            tc_control_frame, textvariable=self.fps_var,
            values=list(FRAMERATES.keys()), state="readonly", width=10
        )
        self.fps_combobox.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        self.fps_combobox.bind("<<ComboboxSelected>>", self.on_fps_selected)

        # Speed Slider
        ttk.Label(tc_control_frame, text="Speed (%):").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        self.speed_slider = ttk.Scale(
            tc_control_frame, from_=0, to=200, orient=tk.HORIZONTAL,
            variable=self.speed_var, command=self.update_speed_label
        )
        self.speed_slider.grid(row=1, column=1, padx=5, pady=5, sticky=tk.EW)
        self.speed_label = ttk.Label(tc_control_frame, text=f"{self.speed_var.get():.0f}%", width=5, anchor=tk.W)
        self.speed_label.grid(row=1, column=2, padx=5, pady=5, sticky=tk.W)

        # Time Offset
        ttk.Label(tc_control_frame, text="Start Offset:").grid(row=2, column=0, padx=5, pady=5, sticky=tk.W)
        self.offset_entry = ttk.Entry(tc_control_frame, textvariable=self.offset_var, width=15)
        self.offset_entry.grid(row=2, column=1, padx=5, pady=5, sticky=tk.W)
        ttk.Label(tc_control_frame, text="(HH:MM:SS:FF, applied on Reset)").grid(row=2, column=2, columnspan=2, padx=5, pady=2, sticky=tk.W)

    def _setup_playback_frame(self, parent):
        """Creates the Playback control frame."""
        playback_frame = ttk.LabelFrame(parent, text="Playback", padding="10")
        playback_frame.pack(side=tk.TOP, fill=tk.X, pady=5)
        # Center the buttons within the frame
        playback_frame.columnconfigure(0, weight=1)

        button_subframe = ttk.Frame(playback_frame) # Subframe to hold buttons side-by-side
        button_subframe.grid(row=0, column=0) # Place subframe in the center column

        self.play_pause_button = ttk.Button(button_subframe, text="Play", width=10, command=self.toggle_play_pause)
        self.play_pause_button.pack(side=tk.LEFT, padx=10, pady=5)

        self.reset_button = ttk.Button(button_subframe, text="Reset", width=10, command=self.reset_timecode)
        self.reset_button.pack(side=tk.LEFT, padx=10, pady=5)

    def _setup_timecode_display_frame(self, parent):
        """Creates the frame for displaying the current timecode."""
        timecode_display_frame = ttk.LabelFrame(parent, text="Current Timecode", padding="10")
        timecode_display_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(5, 0))

        self.timecode_label = ttk.Label(
            timecode_display_frame, text="00:00:00:00",
            font=("Courier", 24, "bold"), anchor=tk.CENTER
        )
        self.timecode_label.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(5, 10))

    def _setup_status_bar(self, parent):
        """Creates the status bar frame with status message and branding."""
        status_bar_frame = ttk.Frame(parent, relief=tk.SUNKEN, padding=(2, 2))
        status_bar_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))

        # Status Message Label
        self.status_message_label = ttk.Label(status_bar_frame, text="Status: Initializing...", anchor=tk.W)
        self.status_message_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))

        # Branding Link Label
        link_label = ttk.Label(status_bar_frame, text=BRAND_NAME, foreground="blue", cursor="hand2")
        link_label.pack(side=tk.RIGHT, padx=(0, 5))

        # Configure font for the link (underline and size)
        try:
            link_font = tkFont.Font(link_label, link_label.cget("font"))
            # Set size=10 as requested, adjust if needed
            link_font.configure(underline=True, size=10)
            link_label.configure(font=link_font)
        except Exception as e:
            print(f"Warning: Could not configure link font - {e}")

        # Bind click event
        link_label.bind("<Button-1>", self.open_github_link)

    # --- Action Methods ---

    def open_github_link(self, event=None):
        """Opens the predefined GitHub URL in the default web browser."""
        print(f"Opening link: {GITHUB_URL}")
        try:
            webbrowser.open_new(GITHUB_URL)
            self.update_status("Opened GitHub page in browser")
        except Exception as e:
            print(f"Error opening URL {GITHUB_URL}: {e}")
            self.update_status(f"Error opening link: {e}")

    def update_status(self, message):
        """Safely updates the status message label in the status bar."""
        if self.status_message_label is not None:
            # Use after() to ensure GUI update happens in the main thread
            self.after(0, lambda: self.status_message_label.config(text=f"Status: {message}"))
        else:
            # Fallback if called before GUI is fully set up
            print(f"Status update (pre-GUI): {message}")

    def update_timecode_display(self, tc_string):
        """Safely updates the main timecode display label."""
        if self.timecode_label is not None:
            self.after(0, lambda: self.timecode_label.config(text=tc_string))

    def update_speed_label(self, value=None):
        """Updates the speed percentage label next to the slider."""
        # 'value' is passed by the Scale command but not strictly needed here
        if self.speed_label is not None:
             current_speed = self.speed_var.get()
             self.speed_label.config(text=f"{current_speed:.0f}%")
             # Update status bar if running to reflect speed change
             if self.is_running:
                 if abs(current_speed) < 0.1:
                     self.update_status("Playing (Speed 0%)")
                 else:
                     self.update_status(f"Playing ({current_speed:.0f}%)")

    def on_fps_selected(self, event=None):
        """Handles framerate selection change from the Combobox."""
        selected_label = self.fps_var.get()
        new_fps_value = FRAMERATES.get(selected_label)

        if new_fps_value:
            was_running = self.is_running
            if was_running:
                self.toggle_play_pause() # Stop playback before changing timing

            self.display_fps_label = selected_label
            self.fps = new_fps_value
            print(f"Framerate changed to: {self.display_fps_label} ({self.fps:.8f} FPS)")
            self.update_status(f"Framerate set to {self.display_fps_label}")

            # Reset timecode to apply new FPS to offset calculation and display
            self.reset_timecode()

            # Decide if you want to automatically restart if it was running
            # if was_running:
            #     self.toggle_play_pause() # Resume play
        else:
            # Should not happen with readonly Combobox, but good practice
            messagebox.showerror("Error", f"Internal error: Invalid FPS label selected: {selected_label}")

    def parse_and_set_offset(self):
        """Parses the offset string from the entry field and sets self.start_frame."""
        offset_str = self.offset_var.get()
        calculated_frames = tc_string_to_frames(offset_str, self.fps)

        if calculated_frames is None:
            messagebox.showerror(
                "Invalid Offset",
                f"Offset '{offset_str}' is not a valid HH:MM:SS:FF format or has "
                f"invalid values for the current FPS ({self.display_fps_label}).\n"
                f"Using 00:00:00:00."
            )
            self.start_frame = 0
            self.offset_var.set("00:00:00:00") # Correct the entry field
        else:
            self.start_frame = calculated_frames
            print(f"Offset set to {offset_str} ({self.start_frame} frames @ {self.display_fps_label} FPS)")

    def update_osc_client(self):
        """Updates the OSC client instance based on GUI IP/Port fields."""
        ip = self.osc_out_ip_var.get()
        port_str = self.osc_out_port_var.get()
        try:
            port = int(port_str)
            if not (0 < port < 65536):
                raise ValueError("Port must be between 1 and 65535")
            # Create or update the client
            self.osc_client = udp_client.SimpleUDPClient(ip, port)
            print(f"OSC Client updated: Sending to {ip}:{port}")
            self.update_status(f"OSC Client ready: {ip}:{port}")
        except ValueError as e:
            messagebox.showerror("OSC Config Error", f"Invalid OSC Output Port: {port_str}.\n{e}")
            self.osc_client = None # Ensure client is None on error
            self.update_status("OSC Client port error.")
        except Exception as e:
            # Catch other potential errors (e.g., DNS resolution, network issues)
            messagebox.showerror("OSC Config Error", f"Failed to create OSC client for {ip}:{port}.\n{e}")
            self.osc_client = None
            self.update_status(f"OSC Client error: {e}")

    def send_osc_message(self, tc_string):
        """Sends the given timecode string via OSC using the configured address."""
        if not self.osc_client:
            if self.is_running: # Only show status error if actively trying to play
                 print("OSC Error: Client not initialized or has error.")
                 self.update_status("OSC client not ready or error.")
            return # Cannot send without a client

        address = self.osc_address_var.get()
        if not address or not address.startswith('/'):
            self.update_status(f"Error: Invalid OSC Address '{address}'. Must start with '/'.")
            print(f"Invalid OSC Address: {address}")
            # Consider stopping playback if address becomes invalid while running
            # self.toggle_play_pause()
            return # Don't send with invalid address

        try:
            self.osc_client.send_message(address, tc_string)
            # print(f"OSC Sent: {address} '{tc_string}'") # Uncomment for verbose logging
        except Exception as e:
            print(f"Error sending OSC message to {address}: {e}")
            self.update_status(f"Error sending OSC: {e}")
            # Consider stopping playback if sending fails repeatedly
            # self.toggle_play_pause()

    def toggle_play_pause(self):
        """Starts or stops the timecode generation thread."""
        if self.is_running:
            # --- Pause ---
            self.is_running = False
            if self.play_pause_button: self.play_pause_button.config(text="Play")

            if self.timecode_thread and self.timecode_thread.is_alive():
                 print("Waiting for timecode thread to stop...")
                 self.timecode_thread.join(timeout=0.2) # Wait briefly
                 if self.timecode_thread.is_alive():
                     # This shouldn't happen often with the loop checks, but log if it does
                     print("Warning: Timecode thread did not stop gracefully.")
            self.timecode_thread = None
            self.update_status("Paused")
        else:
            # --- Play ---
            if not self.osc_client:
                messagebox.showerror("Error", "OSC Client is not configured correctly. Please check IP/Port and click Apply IP/Port.")
                return

            # Ensure the OSC address is valid before starting
            address = self.osc_address_var.get()
            if not address or not address.startswith('/'):
                 messagebox.showerror("Error", f"Invalid OSC Address '{address}'. Must start with '/'.")
                 return

            self.is_running = True
            if self.play_pause_button: self.play_pause_button.config(text="Pause")

            # Start thread if not already running (e.g., after reset or pause)
            if not self.timecode_thread or not self.timecode_thread.is_alive():
                # Pass the current frame count to start from
                self.timecode_thread = threading.Thread(
                    target=self.timecode_loop, args=(self.current_frame,), daemon=True
                )
                self.timecode_thread.start()

            # Update status and speed label immediately
            self.update_speed_label() # This will also call update_status

    def reset_timecode(self):
        """Resets the timecode to the offset and stops playback."""
        if self.is_running:
            self.toggle_play_pause() # Stop playback first

        self.parse_and_set_offset() # Read and validate offset entry
        self.current_frame = self.start_frame # Set counter to the start frame

        # Update display based on the new current_frame and base FPS
        tc_string = frames_to_tc_string(self.current_frame, self.fps)
        self.update_timecode_display(tc_string)

        # Optionally send one OSC message with the reset timecode if client is ready
        if self.osc_client:
            self.send_osc_message(tc_string)

        offset_str = self.offset_var.get() # Get potentially corrected offset string
        self.update_status(f"Reset to {offset_str}")

    def timecode_loop(self, initial_frame):
        """The main loop for generating and sending timecode in a separate thread."""
        self.current_frame = initial_frame
        print(f"Timecode thread started at frame {self.current_frame}")

        # Use time.perf_counter for higher resolution timing
        last_time = time.perf_counter()

        while self.is_running:
            # --- Read current settings (thread-safe for read) ---
            # Note: Accessing Tkinter variables (like speed_var) from a thread
            # is generally safe for reading, but writing should be done via self.after()
            current_speed_percent = self.speed_var.get()
            base_fps = self.fps # The actual precise FPS

            # --- Calculate effective speed and frame duration ---
            current_speed_multiplier = current_speed_percent / 100.0
            if current_speed_multiplier <= 0 or base_fps <= 0:
                # If speed is 0% or FPS is invalid, pause effectively
                time.sleep(0.05) # Sleep briefly to avoid high CPU usage
                last_time = time.perf_counter() # Reset timer to prevent jump on resume
                continue # Skip the rest of the loop iteration

            effective_fps = base_fps * current_speed_multiplier
            frame_duration = 1.0 / effective_fps

            # --- Calculate current timecode string (using base FPS for display format) ---
            # Ensure current_frame is an integer before passing
            tc_string = frames_to_tc_string(int(round(self.current_frame)), base_fps)

            # --- Update GUI display (via main thread) ---
            self.update_timecode_display(tc_string)

            # --- Send OSC Message ---
            self.send_osc_message(tc_string)

            # --- Increment frame ---
            # Frame count can be fractional if speed is not 100%, but display handles integer part
            # For accuracy, keep self.current_frame potentially float, but round for display/calculation
            self.current_frame += 1 # Increment by one logical frame per loop cycle

            # --- Accurate sleep calculation ---
            # Calculate the theoretical time the *next* frame should start
            next_frame_time = last_time + frame_duration
            current_time = time.perf_counter()

            sleep_time = next_frame_time - current_time

            if sleep_time > 0:
                time.sleep(sleep_time)
            # If sleep_time is negative, it means we're already behind schedule.
            # The loop will proceed immediately.

            # Update last_time for the next iteration *after* the sleep/work
            # Using the theoretical next_frame_time helps prevent drift over time
            last_time = next_frame_time

            # Check running flag again *after* potential sleep, before next loop
            if not self.is_running:
                break

        print("Timecode thread finished.")

    def on_closing(self):
        """Handles the window close event gracefully."""
        print("Closing application...")
        self.is_running = False # Signal the timecode thread to stop

        # Wait briefly for the thread to finish its current cycle
        if self.timecode_thread and self.timecode_thread.is_alive():
            print("Waiting for timecode thread to exit...")
            try:
                self.timecode_thread.join(timeout=0.5) # Increased timeout slightly
                if self.timecode_thread.is_alive():
                     print("Warning: Timecode thread did not exit gracefully.")
            except Exception as e:
                print(f"Error joining timecode thread: {e}")

        self.destroy() # Close the Tkinter window

# --- Main Execution Guard ---
if __name__ == "__main__":
    # Create and run the Tkinter application event loop
    app = OscTimecodeGeneratorApp()
    app.mainloop()
