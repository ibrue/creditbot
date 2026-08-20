"""Lab check-in kiosk with facial recognition.

Big touch-friendly buttons: press CHECK IN or CHECK OUT, look at the
camera, and the kiosk recognizes your (enrolled) face and checks you
in/out through the creditbot API on the NAS.

Enrollment is opt-in: members enroll themselves at the kiosk with the
ENROLL FACE button (their Discord ID + name + a few face samples).

Configuration (environment variables or a .env file in this folder):
  KIOSK_API_URL   e.g. http://192.168.1.50:8765   (required)
  KIOSK_API_KEY   must match KIOSK_API_KEY on the NAS (required)
  KIOSK_CAMERA    camera index, default 0
  KIOSK_FULLSCREEN  set to 1 to start fullscreen

Keys: F11 toggles fullscreen, Esc leaves fullscreen, Ctrl+Q quits.
"""
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
from PIL import Image, ImageTk

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from api_client import ApiClient
from face_engine import FaceEngine

API_URL = os.getenv("KIOSK_API_URL", "http://localhost:8765")
API_KEY = os.getenv("KIOSK_API_KEY", "")
CAMERA_INDEX = int(os.getenv("KIOSK_CAMERA", "0"))
START_FULLSCREEN = os.getenv("KIOSK_FULLSCREEN", "0") == "1"

SCAN_SECONDS = 10        # how long to look for a face after a button press
MATCH_VOTES = 3          # consecutive-ish frame matches required
ENROLL_SAMPLES = 5       # face samples captured during enrollment
RESULT_SECONDS = 6       # how long the result screen shows

BG = "#101418"
FG = "#e8eaed"
GREEN = "#2e7d32"
RED = "#c62828"
BLUE = "#1565c0"
GRAY = "#37474f"


class KioskApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Lab Check-In Kiosk")
        root.configure(bg=BG)
        root.geometry("1024x640")
        if START_FULLSCREEN:
            root.attributes("-fullscreen", True)
        root.bind("<F11>", lambda e: root.attributes(
            "-fullscreen", not root.attributes("-fullscreen")))
        root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))
        root.bind("<Control-q>", lambda e: self.quit())
        root.protocol("WM_DELETE_WINDOW", self.quit)

        self.api = ApiClient(API_URL, API_KEY)
        self.engine = FaceEngine()
        self.known_faces = []

        self.cap = cv2.VideoCapture(CAMERA_INDEX)
        if not self.cap.isOpened():
            raise RuntimeError(
                f"Could not open camera {CAMERA_INDEX}. "
                "Set KIOSK_CAMERA to the right index (try 0, 1, 2...)."
            )
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        # State: idle | scanning | enrolling | busy | result
        self.state = "idle"
        self.scan_action = None          # "checkin" or "checkout"
        self.scan_deadline = 0.0
        self.match_votes = {}            # discord_id -> (name, votes)
        self.enroll_target = None        # (discord_id, name)
        self.enroll_collected = []
        self.last_enroll_capture = 0.0
        self.result_until = 0.0
        self.api_queue = queue.Queue()

        self._build_ui()
        self._refresh_faces_async()
        self._tick()

    # ---------- UI ----------

    def _build_ui(self):
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=16, pady=16)

        self.video_label = tk.Label(main, bg="black")
        self.video_label.pack(side="left", fill="both", expand=True)

        panel = tk.Frame(main, bg=BG)
        panel.pack(side="right", fill="y", padx=(16, 0))

        tk.Label(
            panel, text="🔬 Lab Kiosk", font=("DejaVu Sans", 26, "bold"),
            bg=BG, fg=FG,
        ).pack(pady=(0, 20))

        btn_font = ("DejaVu Sans", 22, "bold")
        self.checkin_btn = tk.Button(
            panel, text="✅  CHECK IN", font=btn_font, bg=GREEN, fg="white",
            activebackground="#1b5e20", activeforeground="white",
            height=2, width=14, bd=0, cursor="hand2",
            command=lambda: self.start_scan("checkin"),
        )
        self.checkin_btn.pack(pady=8)

        self.checkout_btn = tk.Button(
            panel, text="👋  CHECK OUT", font=btn_font, bg=RED, fg="white",
            activebackground="#8e0000", activeforeground="white",
            height=2, width=14, bd=0, cursor="hand2",
            command=lambda: self.start_scan("checkout"),
        )
        self.checkout_btn.pack(pady=8)

        self.cancel_btn = tk.Button(
            panel, text="Cancel", font=("DejaVu Sans", 14), bg=GRAY, fg="white",
            bd=0, cursor="hand2", command=self.cancel_scan,
        )

        small_font = ("DejaVu Sans", 13)
        tk.Button(
            panel, text="📷 Enroll Face", font=small_font, bg=BLUE, fg="white",
            bd=0, cursor="hand2", command=self.open_enroll_dialog,
        ).pack(pady=(40, 6), fill="x")
        tk.Button(
            panel, text="🔄 Refresh Faces", font=small_font, bg=GRAY, fg="white",
            bd=0, cursor="hand2", command=self._refresh_faces_async,
        ).pack(pady=6, fill="x")

        self.status_var = tk.StringVar(value="Connecting...")
        tk.Label(
            panel, textvariable=self.status_var, font=("DejaVu Sans", 12),
            bg=BG, fg="#9aa0a6", wraplength=240, justify="center",
        ).pack(side="bottom", pady=8)

        self.message_var = tk.StringVar(value="")
        self.message_label = tk.Label(
            self.root, textvariable=self.message_var,
            font=("DejaVu Sans", 20, "bold"), bg=BG, fg=FG,
            wraplength=900, justify="center",
        )
        self.message_label.pack(side="bottom", pady=(0, 14))

    def set_message(self, text, color=FG):
        self.message_var.set(text)
        self.message_label.configure(fg=color)

    # ---------- Actions ----------

    def start_scan(self, action):
        if self.state not in ("idle", "result"):
            return
        if not self.known_faces:
            self.set_message("No faces enrolled yet — press Enroll Face first!", "#ffb300")
            return
        self.state = "scanning"
        self.scan_action = action
        self.scan_deadline = time.time() + SCAN_SECONDS
        self.match_votes = {}
        self.cancel_btn.pack(pady=8)
        verb = "check in" if action == "checkin" else "check out"
        self.set_message(f"Look at the camera to {verb}...", "#80cbc4")

    def cancel_scan(self):
        self.state = "idle"
        self.cancel_btn.pack_forget()
        self.set_message("")

    def open_enroll_dialog(self):
        if self.state not in ("idle", "result"):
            return
        EnrollDialog(self)

    def start_enrollment(self, discord_id, name):
        self.state = "enrolling"
        self.enroll_target = (discord_id, name)
        self.enroll_collected = []
        self.last_enroll_capture = 0.0
        self.cancel_btn.pack(pady=8)
        self.set_message(
            f"Enrolling {name} — look at the camera and move your head slightly...",
            "#80cbc4",
        )

    # ---------- Background API calls ----------

    def _run_async(self, fn, tag):
        def worker():
            try:
                result = fn()
                self.api_queue.put((tag, result, None))
            except Exception as e:
                self.api_queue.put((tag, None, e))
        threading.Thread(target=worker, daemon=True).start()

    def _refresh_faces_async(self):
        self._run_async(self.api.get_faces, "faces")

    # ---------- Main loop ----------

    def _tick(self):
        self._drain_api_queue()

        ok, frame = self.cap.read()
        if ok:
            frame = cv2.flip(frame, 1)  # mirror view feels natural
            self._process_frame(frame)
            self._draw_frame(frame)

        if self.state == "result" and time.time() > self.result_until:
            self.state = "idle"
            self.set_message("")

        self.root.after(30, self._tick)

    def _process_frame(self, frame):
        if self.state == "scanning":
            if time.time() > self.scan_deadline:
                self.cancel_btn.pack_forget()
                self._show_result("😕 No recognized face. Try again or enroll.", "#ffb300")
                return
            face = self.engine.detect_best_face(frame)
            if face is None:
                return
            self._draw_face_box(frame, face)
            embedding = self.engine.embed(frame, face)
            discord_id, name, score = self.engine.match(embedding, self.known_faces)
            if discord_id is None:
                return
            entry = self.match_votes.get(discord_id, (name, 0))
            self.match_votes[discord_id] = (name, entry[1] + 1)
            if self.match_votes[discord_id][1] >= MATCH_VOTES:
                self._finish_scan(discord_id, name)

        elif self.state == "enrolling":
            face = self.engine.detect_best_face(frame)
            if face is None:
                return
            self._draw_face_box(frame, face)
            now = time.time()
            if now - self.last_enroll_capture < 0.7:
                return
            self.last_enroll_capture = now
            self.enroll_collected.append(self.engine.embed(frame, face))
            captured = len(self.enroll_collected)
            self.set_message(
                f"Capturing face samples... {captured}/{ENROLL_SAMPLES}", "#80cbc4"
            )
            if captured >= ENROLL_SAMPLES:
                self._finish_enrollment()

    def _finish_scan(self, discord_id, name):
        self.state = "busy"
        self.cancel_btn.pack_forget()
        self.set_message(f"Hi {name}! One moment...", "#80cbc4")
        if self.scan_action == "checkin":
            self._run_async(lambda: self.api.checkin(discord_id, name), "checkin")
        else:
            self._run_async(lambda: self.api.checkout(discord_id), "checkout")
        self._recognized_name = name

    def _finish_enrollment(self):
        discord_id, name = self.enroll_target
        samples = list(self.enroll_collected)
        self.state = "busy"
        self.cancel_btn.pack_forget()
        self.set_message("Saving enrollment...", "#80cbc4")

        def do_enroll():
            for emb in samples:
                self.api.enroll_face(discord_id, name, emb)
            return self.api.get_faces()

        self._run_async(do_enroll, "enrolled")
        self._recognized_name = name

    def _drain_api_queue(self):
        try:
            while True:
                tag, result, error = self.api_queue.get_nowait()
                self._handle_api_result(tag, result, error)
        except queue.Empty:
            pass

    def _handle_api_result(self, tag, result, error):
        if error is not None:
            if tag == "faces":
                self.status_var.set(f"⚠️ Can't reach API at {API_URL}")
            else:
                self._show_result(f"⚠️ Error talking to the NAS: {error}", "#ef5350")
            return

        if tag == "faces":
            self.known_faces = result
            people = len({f["discord_id"] for f in result})
            self.status_var.set(f"Connected · {people} enrolled · {API_URL}")

        elif tag == "checkin":
            name = getattr(self, "_recognized_name", "")
            if result["status"] == "already_checked_in":
                mins = result.get("minutes_so_far", 0)
                self._show_result(
                    f"ℹ️ {name}, you're already checked in ({mins} min ago).", "#ffb300"
                )
            else:
                bonus = "\n".join(result.get("bonuses", []))
                text = f"✅ Welcome, {name} — checked in!"
                if bonus:
                    text += f"\n{bonus}"
                self._show_result(text, "#81c784")

        elif tag == "checkout":
            name = getattr(self, "_recognized_name", "")
            if result["status"] == "not_checked_in":
                self._show_result(f"ℹ️ {name}, you weren't checked in.", "#ffb300")
            else:
                text = (
                    f"👋 Bye {name}! {result['duration']} in the lab, "
                    f"+{result['credits_earned']} credits"
                )
                if result.get("weekend_bonus"):
                    text += " (weekend bonus!)"
                self._show_result(text, "#81c784")

        elif tag == "enrolled":
            self.known_faces = result
            name = getattr(self, "_recognized_name", "")
            people = len({f["discord_id"] for f in result})
            self.status_var.set(f"Connected · {people} enrolled · {API_URL}")
            self._show_result(f"🎉 {name} enrolled! You can now check in by face.", "#81c784")

    def _show_result(self, text, color):
        self.state = "result"
        self.result_until = time.time() + RESULT_SECONDS
        self.set_message(text, color)

    # ---------- Drawing ----------

    @staticmethod
    def _draw_face_box(frame, face):
        x, y, w, h = [int(v) for v in face[:4]]
        cv2.rectangle(frame, (x, y), (x + w, y + h), (128, 203, 196), 3)

    def _draw_frame(self, frame):
        label_w = max(self.video_label.winfo_width(), 320)
        label_h = max(self.video_label.winfo_height(), 240)
        fh, fw = frame.shape[:2]
        scale = min(label_w / fw, label_h / fh)
        frame = cv2.resize(frame, (int(fw * scale), int(fh * scale)))
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.video_label.configure(image=img)
        self.video_label.image = img  # keep a reference

    def quit(self):
        try:
            self.cap.release()
        except Exception:
            pass
        self.root.destroy()


class EnrollDialog(tk.Toplevel):
    """Pick an existing member or type a Discord ID + name, then capture."""

    def __init__(self, app: KioskApp):
        super().__init__(app.root)
        self.app = app
        self.title("Enroll Face")
        self.configure(bg=BG)
        self.geometry("460x520")
        self.transient(app.root)
        self.grab_set()

        tk.Label(
            self, text="Who is enrolling?", font=("DejaVu Sans", 16, "bold"),
            bg=BG, fg=FG,
        ).pack(pady=(14, 6))

        tk.Label(
            self, text="Pick yourself from the list (members the bot knows):",
            font=("DejaVu Sans", 11), bg=BG, fg="#9aa0a6",
        ).pack()

        list_frame = tk.Frame(self, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=14, pady=6)
        self.member_list = tk.Listbox(
            list_frame, font=("DejaVu Sans", 12), bg="#1c2126", fg=FG,
            selectbackground=BLUE, height=8,
        )
        self.member_list.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame, command=self.member_list.yview)
        scroll.pack(side="right", fill="y")
        self.member_list.configure(yscrollcommand=scroll.set)
        self.member_list.bind("<<ListboxSelect>>", self._on_pick)

        tk.Label(
            self, text="…or enter manually (Discord ID + display name):",
            font=("DejaVu Sans", 11), bg=BG, fg="#9aa0a6",
        ).pack(pady=(8, 2))

        form = tk.Frame(self, bg=BG)
        form.pack(padx=14, fill="x")
        tk.Label(form, text="Discord ID:", bg=BG, fg=FG,
                 font=("DejaVu Sans", 11)).grid(row=0, column=0, sticky="w")
        self.id_entry = tk.Entry(form, font=("DejaVu Sans", 12), width=24)
        self.id_entry.grid(row=0, column=1, padx=6, pady=3, sticky="we")
        tk.Label(form, text="Name:", bg=BG, fg=FG,
                 font=("DejaVu Sans", 11)).grid(row=1, column=0, sticky="w")
        self.name_entry = tk.Entry(form, font=("DejaVu Sans", 12), width=24)
        self.name_entry.grid(row=1, column=1, padx=6, pady=3, sticky="we")
        form.columnconfigure(1, weight=1)

        tk.Label(
            self,
            text="Enrollment is opt-in — only enroll your own face.\n"
                 "(Discord ID: Settings → Advanced → Developer Mode,\n"
                 "then right-click your name → Copy User ID)",
            font=("DejaVu Sans", 10), bg=BG, fg="#9aa0a6", justify="center",
        ).pack(pady=6)

        tk.Button(
            self, text="📷 Start Capture", font=("DejaVu Sans", 14, "bold"),
            bg=BLUE, fg="white", bd=0, cursor="hand2", command=self._start,
        ).pack(pady=10, ipadx=12, ipady=6)

        self.members = []
        self._load_members()

    def _load_members(self):
        def worker():
            try:
                members = self.app.api.get_members()
            except Exception:
                members = []
            self.members = members
            def fill():
                if not self.winfo_exists():
                    return
                self.member_list.delete(0, "end")
                for m in members:
                    self.member_list.insert(
                        "end", f"{m['username']}  ({m['discord_id']})"
                    )
            self.app.root.after(0, fill)
        threading.Thread(target=worker, daemon=True).start()

    def _on_pick(self, _event):
        sel = self.member_list.curselection()
        if not sel or sel[0] >= len(self.members):
            return
        member = self.members[sel[0]]
        self.id_entry.delete(0, "end")
        self.id_entry.insert(0, member["discord_id"])
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, member["username"])

    def _start(self):
        discord_id = self.id_entry.get().strip()
        name = self.name_entry.get().strip()
        if not discord_id.isdigit() or not name:
            messagebox.showerror(
                "Enroll Face",
                "Please pick a member or enter a numeric Discord ID and a name.",
                parent=self,
            )
            return
        self.destroy()
        self.app.start_enrollment(discord_id, name)


def main():
    if not API_KEY:
        print("⚠️  KIOSK_API_KEY is not set. Create kiosk/.env with:")
        print("    KIOSK_API_URL=http://<your-nas-ip>:8765")
        print("    KIOSK_API_KEY=<same key as on the NAS>")
        raise SystemExit(1)
    root = tk.Tk()
    KioskApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
