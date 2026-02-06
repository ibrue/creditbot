import tkinter as tk
import vlc
import queue
import time
import os
import platform
from datetime import datetime

import database


class KioskApp:
    """Fullscreen kiosk with video player, lab check-in panel, and status bar."""

    MODES = {
        "subway_surfers": "Subway Surfers",
        "minecraft_parkour": "Minecraft Parkour",
        "ragebait": "Ragebait",
    }

    def __init__(self, command_queue: queue.Queue, video_dir: str, fullscreen: bool = True):
        self.command_queue = command_queue
        self.video_dir = video_dir
        self.current_mode = None
        self.start_time = time.time()
        self.bot_connected = False
        self.checkin_buttons = {}  # discord_id -> button widget
        self.checkin_users = []  # list of dicts from database

        # Tkinter setup
        self.root = tk.Tk()
        self.root.title("Subway Surfers Pi")
        self.root.configure(bg="black")
        if fullscreen:
            self.root.attributes("-fullscreen", True)
        else:
            self.root.geometry("800x480")
        self.root.config(cursor="none")

        # --- Video area (top ~70%) ---
        self.video_frame = tk.Frame(self.root, bg="black")
        self.video_frame.place(relx=0, rely=0, relwidth=1, relheight=0.65)

        # Idle screen label (shown when no video playing)
        self.idle_label = tk.Label(
            self.video_frame,
            text="SUBWAY SURFERS PI\n\nUse /play in Discord",
            font=("monospace", 28, "bold"),
            fg="#00ff00",
            bg="black",
        )
        self.idle_label.place(relx=0.5, rely=0.5, anchor="center")

        # --- Check-in panel (middle ~25%) ---
        self.checkin_frame = tk.Frame(self.root, bg="#1a1a2e")
        self.checkin_frame.place(relx=0, rely=0.65, relwidth=1, relheight=0.27)

        self.checkin_title = tk.Label(
            self.checkin_frame,
            text="LAB CHECK-IN",
            font=("monospace", 14, "bold"),
            fg="#e94560",
            bg="#1a1a2e",
        )
        self.checkin_title.pack(pady=(5, 2))

        # Button grid
        self.button_frame = tk.Frame(self.checkin_frame, bg="#1a1a2e")
        self.button_frame.pack(pady=2)

        # Currently checked in label
        self.checked_in_label = tk.Label(
            self.checkin_frame,
            text="",
            font=("monospace", 10),
            fg="#aaaaaa",
            bg="#1a1a2e",
            wraplength=780,
        )
        self.checked_in_label.pack(pady=(2, 5))

        # --- Status bar (bottom ~8%) ---
        self.status_label = tk.Label(
            self.root,
            text="  Mode: IDLE  |  Bot: Disconnected  |  Uptime: 00:00:00",
            font=("monospace", 12),
            fg="white",
            bg="#16213e",
            anchor="w",
            padx=10,
        )
        self.status_label.place(relx=0, rely=0.92, relwidth=1, relheight=0.08)

        # --- VLC setup ---
        vlc_args = ["--quiet"]
        if platform.system() == "Linux":
            vlc_args.append("--no-xlib")
        self.vlc_instance = vlc.Instance(*vlc_args)
        self.media_player = self.vlc_instance.media_player_new()

        # Attach VLC to the video frame (platform-specific)
        self.root.update_idletasks()
        if platform.system() == "Windows":
            self.media_player.set_hwnd(self.video_frame.winfo_id())
        elif platform.system() == "Linux":
            self.media_player.set_xwindow(self.video_frame.winfo_id())
        elif platform.system() == "Darwin":
            self.media_player.set_nsobject(self.video_frame.winfo_id())

        # Auto-loop on video end
        events = self.media_player.event_manager()
        events.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_video_end)

        # Load check-in buttons
        self._refresh_checkin_buttons()

        # Start polling loops
        self._poll_queue()
        self._update_status()
        self._refresh_checked_in_display()
        # Refresh button list every 5 minutes
        self._schedule_button_refresh()

    def _poll_queue(self):
        """Check for commands from the Discord cog."""
        try:
            while True:
                cmd = self.command_queue.get_nowait()
                self._handle_command(cmd)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_command(self, cmd: str):
        if cmd.startswith("mode:"):
            mode = cmd.split(":", 1)[1]
            if mode == "stop":
                self._stop_playback()
            elif mode in self.MODES:
                self._play_mode(mode)
        elif cmd == "bot_connected":
            self.bot_connected = True

    def _play_mode(self, mode: str):
        video_path = os.path.join(self.video_dir, f"{mode}.mp4")
        self.current_mode = mode
        self.idle_label.place_forget()
        media = self.vlc_instance.media_new(video_path)
        self.media_player.set_media(media)
        self.media_player.play()

    def _stop_playback(self):
        self.media_player.stop()
        self.current_mode = None
        self.idle_label.place(relx=0.5, rely=0.5, anchor="center")

    def _on_video_end(self, event):
        """Loop the current video (called from VLC thread)."""
        if self.current_mode:
            self.root.after(100, lambda: self._play_mode(self.current_mode))

    def _update_status(self):
        """Update the status bar every second."""
        uptime = int(time.time() - self.start_time)
        hours, remainder = divmod(uptime, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        mode_str = self.MODES.get(self.current_mode, "IDLE")
        bot_str = "Connected" if self.bot_connected else "Disconnected"
        self.status_label.config(
            text=f"  Mode: {mode_str}  |  Bot: {bot_str}  |  Uptime: {uptime_str}"
        )
        self.root.after(1000, self._update_status)

    # --- Check-in panel ---

    def _refresh_checkin_buttons(self):
        """Rebuild the 8 quick check-in buttons from the database."""
        # Clear existing buttons
        for widget in self.button_frame.winfo_children():
            widget.destroy()
        self.checkin_buttons.clear()

        self.checkin_users = database.get_top_checkin_users(8)

        for i, user in enumerate(self.checkin_users):
            row = i // 4
            col = i % 4
            discord_id = user["discord_id"]
            username = user["username"]

            # Check if currently checked in
            active = database.get_active_checkin(discord_id)
            if active:
                bg_color = "#e94560"  # Red = checked in
                text = f"{username}\n[IN]"
            else:
                bg_color = "#0f3460"  # Blue = available
                text = f"{username}\n[OUT]"

            btn = tk.Button(
                self.button_frame,
                text=text,
                font=("monospace", 11, "bold"),
                fg="white",
                bg=bg_color,
                activebackground="#533483",
                activeforeground="white",
                width=16,
                height=2,
                relief="flat",
                command=lambda did=discord_id, uname=username: self._toggle_checkin(did, uname),
            )
            btn.grid(row=row, column=col, padx=4, pady=2)
            self.checkin_buttons[discord_id] = btn

    def _toggle_checkin(self, discord_id: str, username: str):
        """Toggle check-in / check-out for a user."""
        active = database.get_active_checkin(discord_id)

        if active:
            # Check out
            database.end_checkin(discord_id)
            print(f"Kiosk checkout: {username}")
        else:
            # Check in
            database.start_checkin(discord_id, username, "kiosk")
            database.update_streak(discord_id, username)
            print(f"Kiosk checkin: {username}")

        # Update just this button's appearance
        self._update_button(discord_id, username)

    def _update_button(self, discord_id: str, username: str):
        """Update a single button's color and text."""
        btn = self.checkin_buttons.get(discord_id)
        if not btn:
            return

        active = database.get_active_checkin(discord_id)
        if active:
            btn.config(bg="#e94560", text=f"{username}\n[IN]")
        else:
            btn.config(bg="#0f3460", text=f"{username}\n[OUT]")

    def _refresh_checked_in_display(self):
        """Update the 'currently in' text every 30 seconds."""
        checked_in = database.get_all_checked_in()

        if checked_in:
            parts = []
            for person in checked_in:
                checkin_time = datetime.fromisoformat(person["checkin_time"])
                minutes = int((datetime.now() - checkin_time).total_seconds() / 60)
                hours = minutes // 60
                mins = minutes % 60
                time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
                parts.append(f"{person['username']} ({time_str})")
            self.checked_in_label.config(text=f"Currently in: {', '.join(parts)}")
        else:
            self.checked_in_label.config(text="Nobody currently checked in")

        # Also update button states
        for user in self.checkin_users:
            self._update_button(user["discord_id"], user["username"])

        self.root.after(30000, self._refresh_checked_in_display)

    def _schedule_button_refresh(self):
        """Refresh the user button list every 5 minutes."""
        self._refresh_checkin_buttons()
        self.root.after(300000, self._schedule_button_refresh)

    # --- Public API for the Discord cog ---

    def get_status(self) -> dict:
        """Get current kiosk status (called by the cog)."""
        uptime = int(time.time() - self.start_time)
        return {
            "mode": self.MODES.get(self.current_mode, "Idle"),
            "mode_key": self.current_mode,
            "uptime_seconds": uptime,
            "bot_connected": self.bot_connected,
        }

    def run(self):
        self.root.mainloop()
