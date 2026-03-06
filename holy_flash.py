"""Holy Flash: Background monitor that triggers a fullscreen punishment effect.

This module listens to speech and keyboard input in background daemon threads.
When a forbidden word is detected, it shows a 3-second fullscreen white window
with a centered image and plays an alert sound.
"""

from __future__ import annotations

import logging
import platform
import queue
import threading
import time
import ctypes
from pathlib import Path
from typing import Iterable

import pygame
import speech_recognition as sr
from PIL import Image, ImageTk
from pynput import keyboard
import tkinter as tk

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
FORBIDDEN_WORDS = [
    # Маты и распространенные вариации.
    "блять",
    "блядь",
    "бля",
    "блеать",
    "блджад",
    "сука",
    "суки",
    "сукаа",
    "пиздец",
    "пиздeц",
    "пздц",
    "нахуй",
    "нах",
    "нахер",
    "ебать",
    "ебан",
    "ебаный",
    "ебанут",
    "хуй",
    "хуйня",
    "хуево",
    "пизда",
    "пизду",
    "пиздой",
    "шлюха",
    "шлюхи",
    "шалава",
    "уебок",
    "уёбок",
    "уебан",
    "гандон",
    "гандони",
    "пидор",
    "пидр",
    "пидорас",
    "мудак",
    "мудак",
    "мудачье",
    "хер",
    "хрен",
    # Богохульство и запрещенка с вариациями.
    "сатана",
    "сатан",
    "сатанизм",
    "дьявол",
    "дявол",
    "дьяволь",
    "черт",
    "чёрт",
    "чорт",
    "черти",
    "люцифер",
    "люцик",
    "антихрист",
    "антихриста",
    "богохульство",
    "богохуль",
    "ад",
    "адский",
]

PHRASE_TIME_LIMIT_SECONDS = 2
KEYBOARD_BUFFER_MAX_AGE_SECONDS = 30
MIC_RECONNECT_DELAY_SECONDS = 5
FLASH_DURATION_MS = 3000
AUDIO_FILE = Path("flashbang.mp3")
IMAGE_FILE = Path("jesus.jpg")
KILL_SWITCH = "<ctrl>+<alt>+<shift>+p"


class HolyFlashApp:
    """Coordinates input monitoring, punishment UI, and lifecycle management."""

    def __init__(self, forbidden_words: Iterable[str]) -> None:
        # Pre-normalize forbidden words for case-insensitive comparisons.
        self.forbidden_words = {word.strip().lower() for word in forbidden_words if word.strip()}

        # App lifecycle and trigger management.
        self.stop_event = threading.Event()
        self.trigger_queue: queue.Queue[str] = queue.Queue()
        self._active_flash_lock = threading.Lock()
        self._flash_active = False

        # Keyboard state and lock for thread-safe buffer mutations.
        self._keyboard_buffer: list[str] = []
        self._keyboard_last_clear = time.monotonic()
        self._keyboard_lock = threading.Lock()

        # GUI references (initialized in the GUI thread).
        self._tk_root: tk.Tk | None = None

        # Listeners to stop gracefully.
        self._typing_listener: keyboard.Listener | None = None
        self._killswitch_listener: keyboard.GlobalHotKeys | None = None

    def run(self) -> None:
        """Start daemon threads and keep the process alive until stopped."""
        logging.info("Holy Flash starting...")

        # Start GUI processing thread.
        threading.Thread(target=self._gui_loop, daemon=True, name="gui-thread").start()

        # Start speech monitoring and keyboard monitoring threads.
        threading.Thread(target=self._speech_monitor_loop, daemon=True, name="speech-thread").start()
        threading.Thread(target=self._start_keyboard_listener, daemon=True, name="keyboard-thread").start()
        threading.Thread(target=self._keyboard_buffer_maintenance_loop, daemon=True, name="keyboard-maintenance-thread").start()

        # Hidden admin kill-switch listener.
        self._start_killswitch_listener()

        try:
            while not self.stop_event.is_set():
                time.sleep(0.25)
        except KeyboardInterrupt:
            logging.info("KeyboardInterrupt received; shutting down.")
            self.shutdown()

        logging.info("Holy Flash stopped.")

    def _speech_monitor_loop(self) -> None:
        """Continuously monitor microphone with auto-reconnect and safe retries."""
        recognizer = sr.Recognizer()

        while not self.stop_event.is_set():
            try:
                with sr.Microphone() as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.5)
                    logging.info("Microphone connected. Listening for speech...")

                    while not self.stop_event.is_set():
                        try:
                            audio = recognizer.listen(
                                source,
                                timeout=1,
                                phrase_time_limit=PHRASE_TIME_LIMIT_SECONDS,
                            )
                            transcript = recognizer.recognize_google(audio, language="ru-RU")
                            logging.debug("Speech transcript: %s", transcript)
                            self._check_text_forbidden(transcript)
                        except sr.WaitTimeoutError:
                            continue
                        except sr.UnknownValueError:
                            # No recognizable speech in this phrase window.
                            continue
                        except sr.RequestError as exc:
                            logging.warning("Speech recognition service error: %s", exc)
                        except Exception as exc:  # Broad on purpose for resiliency.
                            logging.exception("Unexpected speech loop error: %s", exc)
                            time.sleep(0.2)
            except Exception as exc:  # Broad on purpose for reconnect logic.
                logging.error("Microphone initialization failed: %s", exc)
                if self.stop_event.wait(MIC_RECONNECT_DELAY_SECONDS):
                    break

    def _start_keyboard_listener(self) -> None:
        """Start real-time keyboard listener for typed forbidden words."""

        def on_press(key: keyboard.Key | keyboard.KeyCode) -> None:
            if self.stop_event.is_set():
                return

            try:
                if hasattr(key, "char") and key.char:
                    char = key.char
                    with self._keyboard_lock:
                        self._keyboard_buffer.append(char)
                        snippet = "".join(self._keyboard_buffer)

                    self._check_text_forbidden(snippet)
                elif key in (keyboard.Key.space, keyboard.Key.enter):
                    with self._keyboard_lock:
                        snippet = "".join(self._keyboard_buffer)

                    # Always validate the current token before flushing.
                    if snippet:
                        self._check_text_forbidden(snippet)
                    self._flush_keyboard_buffer()
                    return
                else:
                    return
            except Exception as exc:
                logging.exception("Keyboard on_press error: %s", exc)

        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        self._typing_listener = listener
        listener.start()
        listener.join()

    def _keyboard_buffer_maintenance_loop(self) -> None:
        """Clear typing buffer periodically to prevent memory bloat."""
        while not self.stop_event.is_set():
            if self.stop_event.wait(1):
                break

            with self._keyboard_lock:
                elapsed = time.monotonic() - self._keyboard_last_clear
                if elapsed >= KEYBOARD_BUFFER_MAX_AGE_SECONDS:
                    self._keyboard_buffer.clear()
                    self._keyboard_last_clear = time.monotonic()

    def _flush_keyboard_buffer(self) -> None:
        """Clear keyboard text buffer immediately on word boundaries."""
        with self._keyboard_lock:
            self._keyboard_buffer.clear()
            self._keyboard_last_clear = time.monotonic()

    def _start_killswitch_listener(self) -> None:
        """Start hidden global hotkey listener to terminate the application safely."""

        def terminate() -> None:
            logging.warning("Admin kill-switch activated.")
            self.shutdown()

        hotkeys = keyboard.GlobalHotKeys({KILL_SWITCH: terminate})
        hotkeys.daemon = True
        self._killswitch_listener = hotkeys
        hotkeys.start()

    def _check_text_forbidden(self, text: str) -> None:
        """Check text for forbidden words (case-insensitive) and trigger punishment."""
        normalized = text.lower()
        for banned in self.forbidden_words:
            if banned in normalized:
                self._trigger_flash(reason=f"Detected forbidden word: {banned}")
                return

    def _trigger_flash(self, reason: str) -> None:
        """Queue the punishment effect, coalescing simultaneous triggers."""
        with self._active_flash_lock:
            if self._flash_active:
                return
            self._flash_active = True

        logging.info("Triggering Holy Flash: %s", reason)
        self.trigger_queue.put(reason)

    def _gui_loop(self) -> None:
        """Run Tkinter event loop and poll trigger queue from dedicated daemon thread."""
        self._tk_root = tk.Tk()
        self._tk_root.withdraw()
        self._tk_root.title("Holy Flash Internal Root")

        def poll_queue() -> None:
            if self.stop_event.is_set():
                self._tk_root.quit()
                return

            try:
                _ = self.trigger_queue.get_nowait()
                self._show_flash_window()
            except queue.Empty:
                pass

            self._tk_root.after(100, poll_queue)

        self._tk_root.after(100, poll_queue)
        try:
            self._tk_root.mainloop()
        except Exception as exc:
            logging.exception("GUI loop crashed: %s", exc)

    def _show_flash_window(self) -> None:
        """Render and auto-destroy the 3-second fullscreen punishment window."""
        if self._tk_root is None:
            return

        flash_window = tk.Toplevel(self._tk_root)
        flash_window.configure(bg="white")
        flash_window.attributes("-fullscreen", True)
        flash_window.attributes("-topmost", True)
        flash_window.overrideredirect(True)

        center_frame = tk.Frame(flash_window, bg="white")
        center_frame.place(relx=0.5, rely=0.5, anchor="center")

        image_loaded = False
        if IMAGE_FILE.exists():
            try:
                img = Image.open(IMAGE_FILE)
                photo = ImageTk.PhotoImage(img)
                label = tk.Label(center_frame, image=photo, bg="white")
                label.image = photo
                label.pack()
                image_loaded = True
            except Exception as exc:
                logging.error("Failed to load image '%s': %s", IMAGE_FILE, exc)
        else:
            logging.error("Image file not found: %s", IMAGE_FILE)

        if not image_loaded:
            tk.Label(
                center_frame,
                text="HOLY FLASH",
                bg="white",
                fg="black",
                font=("Arial", 48, "bold"),
            ).pack()

        self._play_flash_audio()

        def cleanup() -> None:
            try:
                flash_window.destroy()
            finally:
                with self._active_flash_lock:
                    self._flash_active = False

        flash_window.after(FLASH_DURATION_MS, cleanup)

    def _play_flash_audio(self) -> None:
        """Play flashbang audio if available, safely handling mixer errors."""
        try:
            self._set_max_volume_windows()

            if not pygame.mixer.get_init():
                pygame.mixer.init()

            if AUDIO_FILE.exists():
                pygame.mixer.music.load(str(AUDIO_FILE))
                pygame.mixer.music.play()
            else:
                logging.error("Audio file not found: %s", AUDIO_FILE)
        except Exception as exc:
            logging.error("Unable to play audio '%s': %s", AUDIO_FILE, exc)

    def _set_max_volume_windows(self) -> None:
        """Force Windows master volume to 100% before punishment audio."""
        if platform.system().lower() != "windows":
            return

        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            vk_volume_up = 0xAF
            keyeventf_keyup = 0x0002

            # Send enough VOLUME_UP key events to reliably hit max volume.
            for _ in range(60):
                user32.keybd_event(vk_volume_up, 0, 0, 0)
                user32.keybd_event(vk_volume_up, 0, keyeventf_keyup, 0)
        except Exception as exc:
            logging.warning("Unable to set Windows volume to max: %s", exc)

    def shutdown(self) -> None:
        """Signal termination and stop active listeners/resources."""
        if self.stop_event.is_set():
            return

        self.stop_event.set()

        try:
            if self._typing_listener is not None:
                self._typing_listener.stop()
        except Exception:
            pass

        try:
            if self._killswitch_listener is not None:
                self._killswitch_listener.stop()
        except Exception:
            pass

        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.quit()
        except Exception:
            pass

        if self._tk_root is not None:
            try:
                self._tk_root.after(0, self._tk_root.quit)
            except Exception:
                pass


def configure_logging() -> None:
    """Set up console logging for runtime observability."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(threadName)s - %(message)s",
    )


def main() -> None:
    """Application entrypoint."""
    configure_logging()
    app = HolyFlashApp(FORBIDDEN_WORDS)
    app.run()


if __name__ == "__main__":
    main()
