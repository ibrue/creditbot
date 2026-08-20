"""Lab check-in kiosk with facial recognition (Windows or Linux).

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
import base64
import os
import queue
import sys
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

import face_log
from api_client import ApiClient
from face_engine import FaceEngine

# The auto-updater lives at the repo root (one level up)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import updater

API_URL = os.getenv("KIOSK_API_URL", "http://localhost:8765")
API_KEY = os.getenv("KIOSK_API_KEY", "")
CAMERA_INDEX = int(os.getenv("KIOSK_CAMERA", "0"))
START_FULLSCREEN = os.getenv("KIOSK_FULLSCREEN", "0") == "1"
# Send the check-in photo to the server so the bot posts it to Discord
SEND_PHOTO = os.getenv("KIOSK_SEND_PHOTO", "1") == "1"

SCAN_SECONDS = 12        # how long to look for a face after a button press
MATCH_VOTES = 3          # frontal frame matches required to identify someone
VOTE_SCORE = 0.38        # min cosine score for a frame to count as a vote
                         # (slightly stricter than the raw match threshold)
RESULT_SECONDS = 6       # how long the result screen shows

# Liveness check: after being identified, the person must slowly turn
# their head to both sides — a photo held up to the camera can't do that.
LIVENESS_ENABLED = os.getenv("KIOSK_LIVENESS", "1") == "1"
LIVENESS_SECONDS = 10    # extra time allowed for the head-turn challenge
YAW_TURN = 0.30          # yaw ratio that counts as "turned to a side"
YAW_FRONTAL = 0.18       # |yaw| below this counts as "facing the camera"

# Pose-guided enrollment: samples are captured facing the camera AND
# turned to each side, so recognition stays robust at an angle.
ENROLL_BINS = {"center": 3, "side1": 2, "side2": 2}

FONT = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"

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

        if sys.platform == "win32":
            # DirectShow opens much faster than the default MSMF backend
            self.cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
        else:
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
        self.candidate = None            # (discord_id, name) after phase 1
        self.candidate_frame = None      # clean frontal frame for the photo
        self.yaw_min = 0.0               # liveness: extremes seen so far
        self.yaw_max = 0.0
        self.enroll_target = None        # (discord_id, name)
        self.enroll_collected = []
        self.enroll_bins = dict.fromkeys(ENROLL_BINS, 0)
        self.last_enroll_capture = 0.0
        self.result_until = 0.0
        self.api_queue = queue.Queue()
        self.last_face_refresh = time.time()
        self.failed_reads = 0
        self.update_ready = False
        self._start_update_checker()

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
            panel, text="🔬 Lab Kiosk", font=(FONT, 26, "bold"),
            bg=BG, fg=FG,
        ).pack(pady=(0, 20))

        btn_font = (FONT, 22, "bold")
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
            panel, text="Cancel", font=(FONT, 14), bg=GRAY, fg="white",
            bd=0, cursor="hand2", command=self.cancel_scan,
        )

        small_font = (FONT, 13)
        tk.Button(
            panel, text="👤 Add Person / Enroll", font=small_font, bg=BLUE, fg="white",
            bd=0, cursor="hand2", command=self.open_enroll_dialog,
        ).pack(pady=(40, 6), fill="x")
        tk.Button(
            panel, text="🔄 Refresh Faces", font=small_font, bg=GRAY, fg="white",
            bd=0, cursor="hand2", command=self._refresh_faces_async,
        ).pack(pady=6, fill="x")

        self.status_var = tk.StringVar(value="Connecting...")
        tk.Label(
            panel, textvariable=self.status_var, font=(FONT, 12),
            bg=BG, fg="#9aa0a6", wraplength=240, justify="center",
        ).pack(side="bottom", pady=8)

        self.message_var = tk.StringVar(value="")
        self.message_label = tk.Label(
            self.root, textvariable=self.message_var,
            font=(FONT, 20, "bold"), bg=BG, fg=FG,
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
            self.set_message("No faces enrolled yet — press Add Person / Enroll first!", "#ffb300")
            return
        self.state = "scanning"
        self.scan_action = action
        self.scan_deadline = time.time() + SCAN_SECONDS
        self.match_votes = {}
        self.candidate = None
        self.candidate_frame = None
        self.yaw_min = 0.0
        self.yaw_max = 0.0
        self.cancel_btn.pack(pady=8)
        verb = "check in" if action == "checkin" else "check out"
        self.set_message(f"Look straight at the camera to {verb}...", "#80cbc4")

    def cancel_scan(self):
        self.state = "idle"
        self.cancel_btn.pack_forget()
        self.set_message("")

    def open_enroll_dialog(self):
        if self.state not in ("idle", "result"):
            return
        AddPersonDialog(self)

    def start_enrollment(self, discord_id, name):
        self.state = "enrolling"
        self.enroll_target = (discord_id, name)
        self.enroll_collected = []
        self.enroll_bins = dict.fromkeys(ENROLL_BINS, 0)
        self.last_enroll_capture = 0.0
        self.cancel_btn.pack(pady=8)
        self.set_message(
            f"Enrolling {name} — look straight at the camera...",
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
            self.failed_reads = 0
            frame = cv2.flip(frame, 1)  # mirror view feels natural
            self._process_frame(frame)
            self._draw_frame(frame)
        else:
            # Webcams occasionally drop out (USB hiccup, another app grabbed
            # it). After ~3s of failed reads, reopen the camera so the kiosk
            # recovers on its own instead of freezing until someone reboots it
            self.failed_reads += 1
            if self.failed_reads >= 100:
                self.failed_reads = 0
                print("⚠️ Camera stopped responding — reopening...")
                self._reopen_camera()

        # Refresh enrolled faces every 5 minutes so new enrollments from
        # other kiosks appear, and a dropped connection self-heals
        if self.state == "idle" and time.time() - self.last_face_refresh > 300:
            self.last_face_refresh = time.time()
            self._refresh_faces_async()

        # Apply a downloaded update only while idle — never mid-check-in.
        # The start script's restart loop relaunches on the new code.
        if self.update_ready and self.state == "idle":
            print("🔄 Kiosk update applied — restarting on the new version.")
            os._exit(updater.RESTART_EXIT_CODE)

        if self.state == "result" and time.time() > self.result_until:
            self.state = "idle"
            self.set_message("")

        self.root.after(30, self._tick)

    def _start_update_checker(self):
        """Pull merged changes from GitHub in the background; the restart
        (to run the new code) waits until the kiosk is idle."""
        if not updater.AUTO_UPDATE or not updater.is_available():
            return

        def loop():
            while True:
                time.sleep(updater.UPDATE_INTERVAL_MIN * 60)
                result = updater.check_and_update()
                if result["updated"]:
                    print(f"🔄 Kiosk: {result['reason']} — will restart when idle.")
                    self.update_ready = True
                    return  # code on disk is new; wait for idle restart

        threading.Thread(target=loop, daemon=True, name="kiosk-updater").start()
        print(f"🔄 Auto-update on: following origin/{updater.UPDATE_BRANCH}")

    def _reopen_camera(self):
        try:
            self.cap.release()
        except Exception:
            pass
        if sys.platform == "win32":
            self.cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
        else:
            self.cap = cv2.VideoCapture(CAMERA_INDEX)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    def _process_frame(self, frame):
        if self.state == "scanning":
            if time.time() > self.scan_deadline:
                self.cancel_btn.pack_forget()
                if self.candidate:
                    self._show_result(
                        "😕 Liveness check timed out — try again and slowly "
                        "turn your head to both sides.", "#ffb300"
                    )
                else:
                    self._show_result("😕 No recognized face. Try again or enroll.", "#ffb300")
                return
            face = self.engine.detect_best_face(frame)
            if face is None:
                return
            clean = frame.copy()  # keep a copy without the face box overlay
            self._draw_face_box(frame, face)
            yaw = self.engine.yaw_ratio(face)

            if self.candidate is None:
                self._scan_identify(clean, face, yaw)
            else:
                self._scan_liveness(clean, face, yaw)

        elif self.state == "enrolling":
            face = self.engine.detect_best_face(frame)
            if face is None:
                return
            clean = frame.copy()
            self._draw_face_box(frame, face)
            yaw = self.engine.yaw_ratio(face)

            # Which pose bin does this frame fall into?
            if abs(yaw) <= YAW_FRONTAL:
                pose = "center"
            elif yaw <= -0.22:
                pose = "side1"
            elif yaw >= 0.22:
                pose = "side2"
            else:
                pose = None  # in-between angle, ignore

            self.set_message(self._enroll_prompt(), "#80cbc4")

            if pose is None or self.enroll_bins[pose] >= ENROLL_BINS[pose]:
                return
            now = time.time()
            if now - self.last_enroll_capture < 0.7:
                return
            self.last_enroll_capture = now

            self.enroll_collected.append(self.engine.embed(clean, face))
            self.enroll_bins[pose] += 1
            discord_id, name = self.enroll_target
            face_log.save_capture(
                discord_id, name, self.engine.crop_face(clean, face),
                event=f"enroll_{pose}",
            )
            if all(self.enroll_bins[b] >= ENROLL_BINS[b] for b in ENROLL_BINS):
                self._finish_enrollment()

    def _enroll_prompt(self) -> str:
        """Guide the person through the enrollment poses."""
        captured = sum(self.enroll_bins.values())
        total = sum(ENROLL_BINS.values())
        if self.enroll_bins["center"] < ENROLL_BINS["center"]:
            ask = "look straight at the camera"
        elif self.enroll_bins["side1"] < ENROLL_BINS["side1"]:
            ask = "turn your head to one side ↔️"
        else:
            ask = "now turn your head to the other side ↔️"
        return f"Capturing {captured}/{total} — {ask}"

    def _scan_identify(self, clean, face, yaw):
        """Scan phase 1: identify the person from frontal frames only."""
        if abs(yaw) > YAW_FRONTAL:
            return  # profile shots are unreliable for matching — wait

        embedding = self.engine.embed(clean, face)
        discord_id, name, score = self.engine.match(embedding, self.known_faces)
        if discord_id is None or score < VOTE_SCORE:
            return

        # Log the capture locally so retune_faces.py can improve
        # this person's stored embeddings later
        face_log.save_capture(
            discord_id, name, self.engine.crop_face(clean, face),
            event=self.scan_action, score=score,
        )

        entry = self.match_votes.get(discord_id, (name, 0))
        self.match_votes[discord_id] = (name, entry[1] + 1)

        # Robustness: the winner needs MATCH_VOTES frontal votes AND a
        # clear margin over any other candidate seen during this scan
        votes = self.match_votes[discord_id][1]
        rival_votes = max(
            (v for other, (_, v) in self.match_votes.items() if other != discord_id),
            default=0,
        )
        if votes < MATCH_VOTES or votes < 2 * rival_votes:
            return

        self.candidate = (discord_id, name)
        self.candidate_frame = clean
        if not LIVENESS_ENABLED:
            self._finish_scan(discord_id, name, clean)
            return
        self.yaw_min = 0.0
        self.yaw_max = 0.0
        self.scan_deadline = time.time() + LIVENESS_SECONDS
        self.set_message(
            f"Hi {name}! Now slowly turn your head to one side, "
            f"then the other ↔️", "#80cbc4"
        )

    def _scan_liveness(self, clean, face, yaw):
        """Scan phase 2: head-turn challenge (a held-up photo can't pass)."""
        self.yaw_min = min(self.yaw_min, yaw)
        self.yaw_max = max(self.yaw_max, yaw)
        side1_done = self.yaw_min <= -YAW_TURN
        side2_done = self.yaw_max >= YAW_TURN

        discord_id, name = self.candidate

        # On frontal frames, keep verifying it's still the same person —
        # if someone else clearly took over the frame, restart the scan
        if abs(yaw) <= YAW_FRONTAL:
            embedding = self.engine.embed(clean, face)
            seen_id, _, score = self.engine.match(embedding, self.known_faces)
            if seen_id == discord_id:
                self.candidate_frame = clean  # freshest frontal photo
            elif seen_id is not None and score >= VOTE_SCORE + 0.05:
                self.candidate = None
                self.match_votes = {}
                self.set_message("Hmm, lost you — look straight at the camera...", "#ffb300")
                return

        if side1_done and side2_done:
            self._finish_scan(discord_id, name, self.candidate_frame)
            return

        done = "✓" if side1_done or side2_done else "…"
        self.set_message(
            f"Hi {name}! Slowly turn your head side to side ↔️  "
            f"(one side {done}, other side …)", "#80cbc4"
        )

    def _finish_scan(self, discord_id, name, clean_frame=None):
        self.state = "busy"
        self.cancel_btn.pack_forget()
        self.set_message(f"Hi {name}! One moment...", "#80cbc4")
        if self.scan_action == "checkin":
            photo_b64 = None
            if SEND_PHOTO and clean_frame is not None:
                ok, jpeg = cv2.imencode(
                    ".jpg", clean_frame, [cv2.IMWRITE_JPEG_QUALITY, 85]
                )
                if ok:
                    photo_b64 = base64.b64encode(jpeg.tobytes()).decode("ascii")
            self._run_async(
                lambda: self.api.checkin(discord_id, name, photo_b64), "checkin"
            )
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
                if result.get("photo_queued"):
                    text += "\n📸 Your photo is heading to Discord!"
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


class AddPersonDialog(tk.Toplevel):
    """Add a person to the system and link their Discord account.

    Search the Discord server by name (the API pulls member data straight
    from Discord), pick from members already in the system, or enter an ID
    manually. Then add them — with or without capturing their face.
    """

    def __init__(self, app: KioskApp):
        super().__init__(app.root)
        self.app = app
        self.title("Add Person")
        self.configure(bg=BG)
        self.geometry("520x600")
        self.transient(app.root)
        self.grab_set()

        tk.Label(
            self, text="👤 Add / Enroll a Person", font=(FONT, 16, "bold"),
            bg=BG, fg=FG,
        ).pack(pady=(14, 6))

        # Search row
        search_row = tk.Frame(self, bg=BG)
        search_row.pack(fill="x", padx=14)
        self.search_var = tk.StringVar()
        search_entry = tk.Entry(
            search_row, textvariable=self.search_var, font=(FONT, 12)
        )
        search_entry.pack(side="left", fill="x", expand=True, ipady=3)
        search_entry.bind("<Return>", lambda e: self._search())
        tk.Button(
            search_row, text="🔍 Search Discord", font=(FONT, 11),
            bg=BLUE, fg="white", bd=0, cursor="hand2", command=self._search,
        ).pack(side="right", padx=(6, 0))

        self.status_var = tk.StringVar(
            value="Type a Discord name and press Search — or pick from the "
                  "members below."
        )
        tk.Label(
            self, textvariable=self.status_var, font=(FONT, 10),
            bg=BG, fg="#9aa0a6", wraplength=480, justify="center",
        ).pack(pady=(4, 2))

        # Results list
        list_frame = tk.Frame(self, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=14, pady=6)
        self.result_list = tk.Listbox(
            list_frame, font=(FONT, 12), bg="#1c2126", fg=FG,
            selectbackground=BLUE, height=9,
        )
        self.result_list.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame, command=self.result_list.yview)
        scroll.pack(side="right", fill="y")
        self.result_list.configure(yscrollcommand=scroll.set)
        self.result_list.bind("<<ListboxSelect>>", self._on_pick)

        # Selected person
        form = tk.Frame(self, bg=BG)
        form.pack(padx=14, fill="x")
        tk.Label(form, text="Discord ID:", bg=BG, fg=FG,
                 font=(FONT, 11)).grid(row=0, column=0, sticky="w")
        self.id_entry = tk.Entry(form, font=(FONT, 12), width=24)
        self.id_entry.grid(row=0, column=1, padx=6, pady=3, sticky="we")
        tk.Label(form, text="Name:", bg=BG, fg=FG,
                 font=(FONT, 11)).grid(row=1, column=0, sticky="w")
        self.name_entry = tk.Entry(form, font=(FONT, 12), width=24)
        self.name_entry.grid(row=1, column=1, padx=6, pady=3, sticky="we")
        form.columnconfigure(1, weight=1)

        tk.Label(
            self,
            text="Face enrollment is opt-in — only enroll your own face.",
            font=(FONT, 10), bg=BG, fg="#9aa0a6", justify="center",
        ).pack(pady=(6, 2))

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=(4, 14))
        tk.Button(
            btn_row, text="➕ Add Person", font=(FONT, 13, "bold"),
            bg=GRAY, fg="white", bd=0, cursor="hand2",
            command=lambda: self._add(enroll=False),
        ).pack(side="left", padx=6, ipadx=10, ipady=6)
        tk.Button(
            btn_row, text="📷 Add + Enroll Face", font=(FONT, 13, "bold"),
            bg=BLUE, fg="white", bd=0, cursor="hand2",
            command=lambda: self._add(enroll=True),
        ).pack(side="left", padx=6, ipadx=10, ipady=6)

        self.results = []
        self._search()  # empty query -> show members already in the system

    def _set_results(self, results, status):
        if not self.winfo_exists():
            return
        self.results = results
        self.result_list.delete(0, "end")
        for r in results:
            tag = "in system" if r.get("source") == "system" else "Discord"
            self.result_list.insert(
                "end",
                f"{r['display_name']}  (@{r['username']})  [{tag}]"
            )
        self.status_var.set(status)

    def _search(self):
        query = self.search_var.get().strip()
        self.status_var.set("Searching...")

        def worker():
            try:
                if not query:
                    members = self.app.api.get_members()
                    results = [{
                        "discord_id": m["discord_id"],
                        "username": m["username"],
                        "display_name": m["username"],
                        "source": "system",
                    } for m in members]
                    status = f"{len(results)} members already in the system"
                elif query.isdigit():
                    user = self.app.api.discord_user(query)
                    user["source"] = "discord"
                    results = [user]
                    status = "Found by Discord ID"
                else:
                    results = self.app.api.discord_search(query)
                    for r in results:
                        r["source"] = "discord"
                    status = (f"{len(results)} Discord members match "
                              f"“{query}”" if results
                              else f"No Discord members match “{query}”")
            except Exception as e:
                detail = getattr(getattr(e, "response", None), "text", "") or str(e)
                results, status = [], f"Search failed: {detail[:120]}"
            self.app.root.after(0, lambda: self._set_results(results, status))

        threading.Thread(target=worker, daemon=True).start()

    def _on_pick(self, _event):
        sel = self.result_list.curselection()
        if not sel or sel[0] >= len(self.results):
            return
        person = self.results[sel[0]]
        self.id_entry.delete(0, "end")
        self.id_entry.insert(0, person["discord_id"])
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, person["display_name"])

    def _add(self, enroll: bool):
        discord_id = self.id_entry.get().strip()
        name = self.name_entry.get().strip()
        if not discord_id.isdigit() or not name:
            messagebox.showerror(
                "Add Person",
                "Pick someone from the list (or enter a numeric Discord ID "
                "and a name).",
                parent=self,
            )
            return

        self.status_var.set(f"Adding {name}...")

        def worker():
            try:
                self.app.api.add_member(discord_id, name)
            except Exception as e:
                self.app.root.after(0, lambda: self.status_var.set(
                    f"Could not add: {e}"))
                return

            def done():
                if not self.winfo_exists():
                    return
                if enroll:
                    self.destroy()
                    self.app.start_enrollment(discord_id, name)
                else:
                    self.status_var.set(
                        f"✅ {name} added and linked to Discord ID {discord_id}"
                    )
            self.app.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()


def main():
    root = tk.Tk()
    if not API_KEY:
        root.withdraw()
        messagebox.showerror(
            "Kiosk not configured",
            "KIOSK_API_KEY is not set.\n\n"
            "Create a .env file in the kiosk folder with:\n"
            "  KIOSK_API_URL=http://<your-nas-ip>:8765\n"
            "  KIOSK_API_KEY=<same key as on the NAS>",
        )
        raise SystemExit(1)
    try:
        KioskApp(root)
    except Exception as e:
        root.withdraw()
        messagebox.showerror("Kiosk failed to start", str(e))
        raise SystemExit(1)
    root.mainloop()


if __name__ == "__main__":
    main()
