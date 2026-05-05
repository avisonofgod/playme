import tkinter as tk
import math
import logging

logger = logging.getLogger(__name__)

class UI:
    def __init__(self, player):
        self.player = player
        self.player.on_play = self.on_play
        self.player.on_error = self.on_error
        self.player.on_queue_update = self.on_queue_update

        self.root = tk.Tk()
        self.root.title("PlayMe - Audio Player")

        # ---------- DISC ----------
        self.canvas = tk.Canvas(self.root, width=150, height=150, bg="black")
        self.canvas.pack(pady=10)

        self.angle = 0
        self.energy = 0.0

        self.outer = self.canvas.create_oval(10, 10, 140, 140, fill="#222222", outline="")
        self.mid   = self.canvas.create_oval(25, 25, 125, 125, fill="#2d2d2d", outline="")
        self.inner = self.canvas.create_oval(45, 45, 105, 105, fill="#3a3a3a", outline="")
        self.hub   = self.canvas.create_oval(68, 68, 82, 82, fill="#111111", outline="")

        self.spokes = []
        self.num_spokes = 16
        for _ in range(self.num_spokes):
            self.spokes.append(self.canvas.create_line(75, 75, 75, 20, fill="#555555"))

        # ---------- INPUT ----------
        self.entry = tk.Entry(self.root, width=50)
        self.entry.pack(pady=10)
        self.entry.bind("<Return>", lambda e: self.play())
        self.entry.bind("<space>", lambda e: self.toggle_pause())

        # ---------- STATUS ----------
        self.status = tk.Label(self.root, text="Ready - Audio Only", fg="gray", font=("Arial", 9))
        self.status.pack()

        # ---------- QUEUE (similar music results) ----------
        tk.Label(self.root, text="Queue (similar music):").pack()
        self.queue_list = tk.Listbox(self.root, height=6, width=50)
        self.queue_list.pack(pady=5)
        self.queue_list.bind("<Double-Button-1>", self.on_queue_select)

        # ---------- QUALITY ----------
        self.quality_var = tk.StringVar(value="best")
        quality_frame = tk.Frame(self.root)
        quality_frame.pack()
        tk.Label(quality_frame, text="Quality:").pack(side="left")
        tk.Radiobutton(quality_frame, text="Best", variable=self.quality_var, value="best").pack(side="left")
        tk.Radiobutton(quality_frame, text="High", variable=self.quality_var, value="high").pack(side="left")
        tk.Radiobutton(quality_frame, text="Med", variable=self.quality_var, value="medium").pack(side="left")
        tk.Radiobutton(quality_frame, text="Low", variable=self.quality_var, value="low").pack(side="left")

        # ---------- CONTROLS ----------
        self.controls = tk.Frame(self.root)
        self.controls.pack(pady=10)

        tk.Button(self.controls, text="Play", command=self.play).pack(side="left")
        tk.Button(self.controls, text="Stop", command=self.stop).pack(side="left")
        tk.Button(self.controls, text="Pause", command=self.toggle_pause).pack(side="left")
        tk.Button(self.controls, text="Next", command=self.play_next).pack(side="left")

        self.animate_disc()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- ENERGY ----------
    def update_energy(self):
        target = 1.0 if (self.player.playing and not self.player.paused) else 0.0
        self.energy = 0.85 * self.energy + 0.15 * target

    # ---------- ANIMATION ----------
    def animate_disc(self):
        self.update_energy()

        if self.energy > 0.01:
            self.angle = (self.angle + 2 + 6 * self.energy) % 360

        cx, cy = 75, 75
        rad_base = math.radians(self.angle)

        for i, line in enumerate(self.spokes):
            theta = rad_base + (2 * math.pi * i / self.num_spokes)

            r1 = 10
            r2 = 55 * self.energy

            x1 = cx + r1 * math.cos(theta)
            y1 = cy + r1 * math.sin(theta)
            x2 = cx + r2 * math.cos(theta)
            y2 = cy + r2 * math.sin(theta)

            self.canvas.coords(line, x1, y1, x2, y2)

        hub_size = 14 + 4 * self.energy
        self.canvas.coords(
            self.hub,
            cx - hub_size/2, cy - hub_size/2,
            cx + hub_size/2, cy + hub_size/2
        )

        self.root.after(30, self.animate_disc)

    # ---------- CALLBACKS ----------
    def on_play(self):
        self.status.config(text="Playing", fg="green")
        logger.info("Playback started successfully")
        self.update_queue_display()

    def on_error(self, msg):
        self.status.config(text="Error: " + msg, fg="red")
        logger.error("UI Error: " + msg)
        self.entry.config(bg="pink")
        self.root.after(1000, lambda: self.entry.config(bg="white"))

    def on_queue_update(self):
        self.update_queue_display()

    # ---------- QUEUE ----------
    def update_queue_display(self):
        self.queue_list.delete(0, tk.END)
        queue = self.player.get_queue()
        for i, item in enumerate(queue):
            prefix = "▶ " if i == self.player.current_index else "  "
            self.queue_list.insert(tk.END, prefix + item)

    def on_queue_select(self, event):
        selection = self.queue_list.curselection()
        if selection:
            idx = selection[0]
            if idx < len(self.player.queue):
                self.player.current_index = idx - 1
                self.player.play_next()

    # ---------- QUALITY ----------
    def apply_quality(self):
        quality = self.quality_var.get()
        if quality == "best":
            self.player.resolver.set_quality("bestaudio")
        elif quality == "high":
            self.player.resolver.set_quality("bestaudio[abr<=192]")
        elif quality == "medium":
            self.player.resolver.set_quality("bestaudio[abr<=128]")
        else:
            self.player.resolver.set_quality("bestaudio[abr<=96]")

    # ---------- BUTTON ACTIONS ----------
    def play(self):
        query = self.entry.get().strip()
        if query:
            logger.info("User searching for music: " + query)
            self.apply_quality()
            self.status.config(text="Searching...", fg="orange")
            self.player.play(query)

    def play_next(self):
        logger.info("User pressed Next")
        self.player.play_next()

    def stop(self):
        logger.info("User pressed stop")
        self.player.stop()
        self.status.config(text="Stopped", fg="gray")
        self.update_queue_display()

    def toggle_pause(self):
        self.player.toggle_pause()
        if self.player.paused:
            self.status.config(text="Paused", fg="yellow")
            logger.info("Playback paused")
        else:
            self.status.config(text="Playing", fg="green")
            logger.info("Playback resumed")

    # ---------- CLOSE ----------
    def on_close(self):
        logger.info("Application closing")
        self.player.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
