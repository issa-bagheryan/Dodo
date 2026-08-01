# bash

# mkdir -p /home/claude/dodo-repo/fonts
# cat > /home/claude/dodo-repo/dodo.py << 'PYEOF'
"""
Dodo - a tiny terminal-styled AI todo app.

Talk to it naturally ("add buy milk", "I finished the report") and it uses a
local Ollama model to figure out what you mean. Click [ ] to complete a task,
or click [x] to delete it.
"""

import atexit
import json
import re
import sys
import os
import threading
from pathlib import Path

import requests
import tkinter as tk
from tkinter import font as tkfont

# ================= CONFIG =================

TODO_FILE = Path.home() / ".todo_data.json"
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = os.environ.get("OLLAMA_MODEL", "phi4-mini:latest") #CHANGE TO YOUR PREFERRED MODEL

SCHEMA_HINT = """
You must respond ONLY with valid JSON.

Format:

{
  "action": "add | complete | remove | list | unclear",
  "item_text": "task text if adding",
  "target_index": "number if completing/removing",
  "reply": "short friendly response"
}

Rules:

add:
- create a new task

complete:
- user finished a task

remove:
- user wants a task deleted

list:
- user wants to see tasks

unclear:
- you cannot understand

Match naturally:
"bought milk" matches "Buy milk"

Return JSON only.
"""

# ================= STORAGE =================


def load_tasks():
    if TODO_FILE.exists():
        return json.loads(TODO_FILE.read_text())
    return []


def save_tasks(tasks):
    TODO_FILE.write_text(json.dumps(tasks, indent=2))


# ================= OLLAMA =================


def ask_ollama(user_input, tasks):
    current = "\n".join(
        f"{i+1}. {t['text']}" for i, t in enumerate(tasks)
    ) or "(empty)"

    system = f"""
{SCHEMA_HINT}

CURRENT TASKS:

{current}
"""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "format": "json",
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_input},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()

    raw = response.json()["message"]["content"]

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {"action": "unclear", "reply": "I didn't understand."}

    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return {"action": "unclear", "reply": "JSON parsing failed."}


# ================= BUNDLED FONT LOADING =================
# Loads any .ttf/.otf files in ./fonts privately, for this process only, so
# the custom font works without the user installing it system-wide. This is
# the standard trick for "embedding" a font in a desktop app on Windows.

_LOADED_FONT_RESOURCES = []


def load_bundled_fonts():
    fonts_dir = Path(__file__).resolve().parent / "fonts"
    if not fonts_dir.exists():
        return

    font_files = list(fonts_dir.glob("*.ttf")) + list(fonts_dir.glob("*.otf"))
    if not font_files:
        return

    if sys.platform.startswith("win"):
        import ctypes
        FR_PRIVATE = 0x10
        gdi32 = ctypes.windll.gdi32
        for f in font_files:
            added = gdi32.AddFontResourceExW(str(f), FR_PRIVATE, 0)
            if added:
                _LOADED_FONT_RESOURCES.append(str(f))

        def _cleanup():
            for f in _LOADED_FONT_RESOURCES:
                gdi32.RemoveFontResourceExW(f, FR_PRIVATE, 0)

        atexit.register(_cleanup)
    # macOS/Linux use different private-loading APIs; simplest path there is
    # to just install the font normally (see README).


# ================= THEME =================

BG = "#0b0906"
FG = "#ffb570"           # light orange
FG_DIM = "#8a5a35"        # muted orange-brown, borders/secondary text
FG_TITLE = "#ffd9a8"      # pale peach, title
FG_DEL = "#ff8a5c"        # warm coral, delete marks

STATUS_COLORS = {
    "ready": "#5fd97a",
    "thinking": "#ffb570",
    "thinking_dim": "#a8703f",
    "error": "#ff5c5c",
}

INNER_WIDTH = 30
TEXT_FIELD_WIDTH = INNER_WIDTH - 12

CONTENT_WIDTH = 320
BOX_HEIGHT = 380
REPLY_HEIGHT = 60

GREETING = "Tell me what you need to get done."


def pick_font(root, candidates, size, weight="normal"):
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return tkfont.Font(family=name, size=size, weight=weight)
    return tkfont.Font(family=candidates[-1], size=size, weight=weight)


def truncate(text, width):
    if len(text) <= width:
        return text
    return text[: width - 1] + "\u2026"


class DodoApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Dodo")
        self.configure(bg=BG)
        self.resizable(False, False)

        self.tasks = load_tasks()
        self.busy = False
        self.pulse_job = None
        self.pulse_on = False

        self.mono = pick_font(
            self, ["Cascadia Mono", "Consolas", "Courier New", "DejaVu Sans Mono", "Menlo", "Courier"], 13
        )
        self.serif_title = pick_font(
            self, ["Instrument Serif", "Georgia", "Times New Roman", "Constantia", "Cambria", "Garamond"], 22
        )
        self.serif = pick_font(
            self, ["Instrument Serif", "Georgia", "Times New Roman", "Constantia", "Cambria", "Garamond"], 15
        )
        self.serif_small = pick_font(
            self, ["Instrument Serif", "Georgia", "Times New Roman", "Constantia", "Cambria", "Garamond"], 12
        )

        self._build_ui()
        self.render()
        self.set_status("ready")

        self.bind("<Return>", lambda e: self.on_send())
        self.entry.focus_set()

    # ---------- layout ----------

    def _build_ui(self):
        outer = tk.Frame(self, bg=BG)
        outer.pack(padx=16, pady=16)

        heading = tk.Label(outer, text="Dodo", font=self.serif_title, bg=BG, fg=FG_TITLE)
        heading.pack(pady=(0, 10))

        # ---- task box ----
        box_container = tk.Frame(outer, bg=BG, width=CONTENT_WIDTH, height=BOX_HEIGHT)
        box_container.pack_propagate(False)
        box_container.pack()

        self.text = tk.Text(
            box_container, bg=BG, fg=FG, insertbackground=FG, font=self.mono,
            bd=0, highlightthickness=0, wrap="none", cursor="arrow",
        )
        self.text.pack(fill="both", expand=True)
        self.text.bind("<MouseWheel>", lambda e: self.text.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        self.text.bind("<Button-4>", lambda e: self.text.yview_scroll(-2, "units"))
        self.text.bind("<Button-5>", lambda e: self.text.yview_scroll(2, "units"))

        self.text.tag_configure("title", foreground=FG_TITLE)
        self.text.tag_configure("dim", foreground=FG_DIM)
        self.text.tag_configure("chk", foreground=FG)
        self.text.tag_configure("del", foreground=FG_DEL)

        # ---- reply area  ----
        reply_container = tk.Frame(outer, bg=BG, width=CONTENT_WIDTH, height=REPLY_HEIGHT)
        reply_container.pack_propagate(False)
        reply_container.pack(pady=(12, 4))

        self.reply_var = tk.StringVar(value=GREETING)
        reply_label = tk.Label(
            reply_container, textvariable=self.reply_var, font=self.serif,
            bg=BG, fg=FG, wraplength=CONTENT_WIDTH - 10, justify="center", anchor="n"
        )
        reply_label.pack(fill="both", expand=True)

        # ---- status indicator ----
        status_row = tk.Frame(outer, bg=BG)
        status_row.pack(pady=(0, 10))

        self.status_dot = tk.Label(status_row, text="\u25cf", font=self.serif_small, bg=BG, fg=STATUS_COLORS["ready"])
        self.status_dot.pack(side="left")

        self.status_word_var = tk.StringVar(value="ready")
        status_word = tk.Label(
            status_row, textvariable=self.status_word_var, font=self.serif_small, bg=BG, fg=FG_DIM
        )
        status_word.pack(side="left", padx=(6, 0))

        # ---- input ----
        entry_frame = tk.Frame(outer, bg=BG)
        entry_frame.pack()

        self.entry = tk.Entry(
            entry_frame, font=self.serif, bg=BG, fg=FG, insertbackground=FG,
            relief="flat", highlightthickness=1, highlightbackground=FG_DIM,
            highlightcolor=FG, justify="center", width=24
        )
        self.entry.pack(ipady=6)
        self._set_placeholder()
        self.entry.bind("<FocusIn>", self._clear_placeholder)
        self.entry.bind("<FocusOut>", self._restore_placeholder)

    # ---------- placeholder ----------

    PLACEHOLDER = "add a task, or say what's done"

    def _set_placeholder(self):
        self.entry.insert(0, self.PLACEHOLDER)
        self.entry.config(fg=FG_DIM)
        self._placeholder_active = True

    def _clear_placeholder(self, event=None):
        if getattr(self, "_placeholder_active", False):
            self.entry.delete(0, "end")
            self.entry.config(fg=FG)
            self._placeholder_active = False

    def _restore_placeholder(self, event=None):
        if not self.entry.get().strip():
            self._set_placeholder()

    # ---------- status indicator ----------

    def set_status(self, state):
        self._stop_pulse()
        if state == "ready":
            self.status_dot.config(fg=STATUS_COLORS["ready"])
            self.status_word_var.set("ready")
        elif state == "thinking":
            self.status_word_var.set("thinking")
            self._start_pulse()
        elif state == "error":
            self.status_dot.config(fg=STATUS_COLORS["error"])
            self.status_word_var.set("error")

    def _start_pulse(self):
        self.pulse_on = True
        self._pulse_tick()

    def _pulse_tick(self):
        if not self.pulse_on:
            return
        current = self.status_dot.cget("fg")
        next_color = (
            STATUS_COLORS["thinking_dim"]
            if current == STATUS_COLORS["thinking"]
            else STATUS_COLORS["thinking"]
        )
        self.status_dot.config(fg=next_color)
        self.pulse_job = self.after(450, self._pulse_tick)

    def _stop_pulse(self):
        self.pulse_on = False
        if self.pulse_job is not None:
            self.after_cancel(self.pulse_job)
            self.pulse_job = None

    # ---------- rendering ----------

    def render(self):
        t = self.text
        t.config(state="normal")
        t.delete("1.0", "end")

        border_top = "\u250c" + ("\u2500" * INNER_WIDTH) + "\u2510"
        border_mid = "\u251c" + ("\u2500" * INNER_WIDTH) + "\u2524"
        border_bot = "\u2514" + ("\u2500" * INNER_WIDTH) + "\u2518"

        t.insert("end", border_top + "\n", "dim")
        t.insert("end", "\u2502" + "TASKS".center(INNER_WIDTH) + "\u2502\n", "title")
        t.insert("end", border_mid + "\n", "dim")

        if not self.tasks:
            empty = "(nothing here yet)"
            t.insert("end", "\u2502" + empty.center(INNER_WIDTH) + "\u2502\n", "dim")
        else:
            for i, task in enumerate(self.tasks):
                row = int(t.index("end-1c").split(".")[0])

                t.insert("end", "\u2502 ")

                col_start = len(t.get(f"{row}.0", "insert"))
                t.insert("end", "[ ]", "chk")
                col_end = col_start + 3
                t.tag_add(f"chk{i}", f"{row}.{col_start}", f"{row}.{col_end}")

                t.insert("end", " ")

                field = truncate(task["text"], TEXT_FIELD_WIDTH).ljust(TEXT_FIELD_WIDTH)
                t.insert("end", field)

                t.insert("end", " ")

                col_start2 = len(t.get(f"{row}.0", "insert"))
                t.insert("end", "[x]", "del")
                col_end2 = col_start2 + 3
                t.tag_add(f"del{i}", f"{row}.{col_start2}", f"{row}.{col_end2}")

                t.insert("end", " \u2502\n")

                t.tag_bind(f"chk{i}", "<Button-1>", lambda e, idx=i: self.complete_task(idx))
                t.tag_bind(f"chk{i}", "<Enter>", lambda e: t.config(cursor="hand2"))
                t.tag_bind(f"chk{i}", "<Leave>", lambda e: t.config(cursor="arrow"))

                t.tag_bind(f"del{i}", "<Button-1>", lambda e, idx=i: self.remove_task(idx))
                t.tag_bind(f"del{i}", "<Enter>", lambda e: t.config(cursor="hand2"))
                t.tag_bind(f"del{i}", "<Leave>", lambda e: t.config(cursor="arrow"))

        t.insert("end", border_bot, "dim")
        t.config(state="disabled")

    # ---------- actions ----------

    def complete_task(self, idx):
        if 0 <= idx < len(self.tasks):
            self.tasks.pop(idx)
            save_tasks(self.tasks)
            self.reply_var.set("Nice, checked that off.")
            self.render()

    def remove_task(self, idx):
        if 0 <= idx < len(self.tasks):
            self.tasks.pop(idx)
            save_tasks(self.tasks)
            self.reply_var.set("Removed.")
            self.render()

    def on_send(self):
        if self.busy:
            return
        text = self.entry.get().strip()
        if not text or getattr(self, "_placeholder_active", False):
            return

        self.entry.delete(0, "end")
        self.busy = True
        self.set_status("thinking")

        thread = threading.Thread(target=self._ask_worker, args=(text,), daemon=True)
        thread.start()

    def _ask_worker(self, text):
        try:
            result = ask_ollama(text, self.tasks)
            error = None
        except requests.exceptions.ConnectionError:
            result = None
            error = "Can't reach Ollama. Run 'ollama serve' and try again."
        except requests.exceptions.Timeout:
            result = None
            error = "Ollama timed out. Is the model still loading?"
        except Exception as e:
            result = None
            error = f"Something went wrong: {e}"

        self.after(0, self._apply_result, result, error)

    def _apply_result(self, result, error):
        self.busy = False

        if error:
            self.set_status("error")
            self.reply_var.set(error)
            return

        action = result.get("action")

        if action == "add":
            item_text = result.get("item_text")
            if item_text:
                self.tasks.append({"text": item_text})
                save_tasks(self.tasks)

        elif action in ("remove", "complete"):
            index = result.get("target_index", -1)
            if isinstance(index, int) and 1 <= index <= len(self.tasks):
                self.tasks.pop(index - 1)
                save_tasks(self.tasks)

        reply = str(result.get("reply", "Done.")).strip()
        if reply:
            reply = reply[0].upper() + reply[1:]
        self.reply_var.set(reply or "Done.")

        self.set_status("ready")
        self.render()


if __name__ == "__main__":
    load_bundled_fonts()
    app = DodoApp()
    app.mainloop()
# PYEOF
# cd /home/claude/dodo-repo && python3 -m py_compile dodo.py && echo "SYNTAX OK"