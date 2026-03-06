"""JARVIS: Russian voice assistant with modular actions and integrated security module.

The assistant works in passive listening mode and activates only after the wake word
"Джарвис". It supports command execution, command database generation, text-to-speech
feedback, and a retained punishment subsystem (fullscreen flash + audio).
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import platform
import queue
import random
import re
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

import pygame
import pyttsx3
import speech_recognition as sr
import tkinter as tk
from PIL import Image, ImageTk

WAKE_WORD = "джарвис"
COMMAND_DB_FILE = Path("commands_db.json")
IMAGE_FILE = Path("jesus.jpg")
AUDIO_FILE = Path("flashbang.mp3")
FLASH_DURATION_MS = 3000
PHRASE_TIME_LIMIT_SECONDS = 4
MIC_RECONNECT_DELAY_SECONDS = 5

# Expanded list for profanity/blasphemy checks (security module trigger).
FORBIDDEN_WORDS = {
    "блять", "блядь", "бля", "блеать", "блджад", "сука", "сучка", "пиздец",
    "пздц", "пизда", "пизду", "нахуй", "нахер", "хуй", "хер", "хуево", "ебать",
    "ебан", "ебаный", "уебок", "уёбок", "гандон", "пидор", "пидорас", "мудак",
    "шлюха", "сатана", "дьявол", "дявол", "черт", "чёрт", "чорт", "люцифер",
    "антихрист", "богохульство", "ад", "адский",
}

SUCCESS_REPLIES = [
    "Выполняю.",
    "Как пожелаете.",
    "Готово, сэр.",
    "Принято. Делаю.",
]


class CommandGenerator:
    """Builds command database with 100+ intents on first launch."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def ensure_db(self) -> None:
        """Create command database if it does not exist."""
        if self.db_path.exists():
            return

        database = {
            "metadata": {
                "name": "JARVIS Commands",
                "version": 1,
                "language": "ru-RU",
            },
            "intents": self._generate_intents(),
        }

        self.db_path.write_text(
            json.dumps(database, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logging.info("Создана база команд: %s", self.db_path)

    def _generate_intents(self) -> list[dict[str, Any]]:
        """Generate 100+ categorized command intents."""
        categories: dict[str, list[str]] = {
            "system": [
                "увеличь громкость", "уменьши громкость", "включи звук", "выключи звук",
                "яркость выше", "яркость ниже", "режим сна", "перезагрузи компьютер",
                "выключи компьютер", "заблокируй компьютер", "сверни всё", "покажи рабочий стол",
                "покажи процессы", "запусти диспетчер задач", "открой параметры",
                "проверь заряд батареи", "включи ночной режим", "выключи ночной режим",
                "очисти буфер обмена", "покажи системную информацию", "включи bluetooth",
                "выключи bluetooth", "включи wi-fi", "выключи wi-fi", "включи vpn",
            ],
            "apps": [
                "открой steam", "открой discord", "открой telegram", "открой word",
                "открой excel", "открой powerpoint", "открой outlook", "открой vscode",
                "открой браузер", "закрой браузер", "открой калькулятор", "открой paint",
                "открой блокнот", "открой проводник", "запусти spotify", "запусти obs",
                "запусти blender", "открой git bash", "запусти zoom", "запусти teams",
                "запусти pycharm", "открой edge", "открой chrome", "открой firefox",
                "запусти battle.net",
            ],
            "web": [
                "открой youtube", "открой новости", "покажи погоду", "открой github",
                "открой почту", "открой календарь", "найди рецепт", "найди курс доллара",
                "включи музыку", "открой yandex music", "поиск в интернете", "открой twitch",
                "открой reddit", "открой habr", "открой wikipedia", "открой карты",
                "открой киноафишу", "покажи пробки", "открой rutube", "открой vk",
                "открой госуслуги", "открой hh", "открой stackoverflow", "открой cloud",
                "открой чат",
            ],
            "humor": [
                "расскажи шутку", "цитата железного человека", "кто твой создатель",
                "включи режим сарказма", "скажи комплимент", "скажи мотивацию",
                "what is love", "ты живой", "у тебя есть броня", "кто сильнее тор или халк",
                "как стать миллиардером", "скажи фразу старка", "режим вечеринка",
                "выдай пасхалку", "поиграй со мной", "предскажи будущее",
                "скажи секрет", "кто такой thanos", "запусти протокол veronica",
                "расскажи анекдот", "включи аплодисменты", "брось остроумие",
                "кто лучший мститель", "скажи мем", "имитируй friday",
            ],
            "cleanup": [
                "почисти корзину", "очисти временные файлы", "очисти кэш браузера",
                "удали старые логи", "дефрагментация диска", "проверь диск на ошибки",
                "закрой фоновые приложения", "освободи оперативную память", "очисти автозагрузку",
                "покажи большие файлы", "удали дубликаты", "очисти загрузки", "архивируй документы",
                "сделай скриншот", "очисти dns кэш", "обнови антивирус", "проверь угрозы",
                "очисти рабочий стол", "подготовь резервную копию", "создай точку восстановления",
                "переименуй снимок", "оптимизируй систему", "проверь обновления", "закрой мусор",
                "проведи cleanup",
            ],
        }

        intents: list[dict[str, Any]] = []
        intent_counter = 1
        for category, examples in categories.items():
            for phrase in examples:
                intents.append(
                    {
                        "id": f"intent_{intent_counter:03d}",
                        "category": category,
                        "phrase": phrase,
                    }
                )
                intent_counter += 1

        return intents


class PunishmentModule:
    """Fullscreen punishment effect module retained from Holy Flash logic."""

    def __init__(self) -> None:
        self.trigger_queue: queue.Queue[str] = queue.Queue()
        self.active_lock = threading.Lock()
        self.flash_active = False
        self.stop_event = threading.Event()
        self.tk_root: tk.Tk | None = None

        thread = threading.Thread(target=self._gui_loop, daemon=True, name="punishment-gui")
        thread.start()

    def trigger(self, reason: str) -> None:
        """Trigger punishment effect if not already active."""
        with self.active_lock:
            if self.flash_active:
                return
            self.flash_active = True

        logging.warning("Punishment triggered: %s", reason)
        self.trigger_queue.put(reason)

    def shutdown(self) -> None:
        """Stop GUI loop and release resources."""
        self.stop_event.set()
        if self.tk_root is not None:
            try:
                self.tk_root.after(0, self.tk_root.quit)
            except Exception:
                pass

    def _gui_loop(self) -> None:
        """Run punishment UI poll loop in its own thread."""
        self.tk_root = tk.Tk()
        self.tk_root.withdraw()

        def poll() -> None:
            if self.stop_event.is_set():
                self.tk_root.quit()
                return

            try:
                _ = self.trigger_queue.get_nowait()
                self._show_flash()
            except queue.Empty:
                pass

            self.tk_root.after(100, poll)

        self.tk_root.after(100, poll)
        self.tk_root.mainloop()

    def _show_flash(self) -> None:
        """Display fullscreen white flash with centered image and sound."""
        if self.tk_root is None:
            return

        window = tk.Toplevel(self.tk_root)
        window.configure(bg="white")
        window.attributes("-fullscreen", True)
        window.attributes("-topmost", True)
        window.overrideredirect(True)

        frame = tk.Frame(window, bg="white")
        frame.place(relx=0.5, rely=0.5, anchor="center")

        shown_image = False
        if IMAGE_FILE.exists():
            try:
                img = Image.open(IMAGE_FILE)
                photo = ImageTk.PhotoImage(img)
                label = tk.Label(frame, image=photo, bg="white")
                label.image = photo
                label.pack()
                shown_image = True
            except Exception as exc:
                logging.error("Ошибка загрузки изображения: %s", exc)

        if not shown_image:
            tk.Label(
                frame,
                text="РЕЖИМ ИНКВИЗИТОРА",
                bg="white",
                fg="black",
                font=("Arial", 42, "bold"),
            ).pack()

        self._play_audio()

        def close_flash() -> None:
            try:
                window.destroy()
            finally:
                with self.active_lock:
                    self.flash_active = False

        window.after(FLASH_DURATION_MS, close_flash)

    def _play_audio(self) -> None:
        """Play punishment audio with Windows max-volume boost."""
        self._set_max_volume_windows()
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            if AUDIO_FILE.exists():
                pygame.mixer.music.load(str(AUDIO_FILE))
                pygame.mixer.music.play()
            else:
                logging.error("Аудио файл не найден: %s", AUDIO_FILE)
        except Exception as exc:
            logging.error("Не удалось воспроизвести звук: %s", exc)

    def _set_max_volume_windows(self) -> None:
        """Force system volume to max on Windows using key events."""
        if platform.system().lower() != "windows":
            return

        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            vk_volume_up = 0xAF
            keyeventf_keyup = 0x0002
            for _ in range(60):
                user32.keybd_event(vk_volume_up, 0, 0, 0)
                user32.keybd_event(vk_volume_up, 0, keyeventf_keyup, 0)
        except Exception as exc:
            logging.warning("Не удалось выкрутить громкость на максимум: %s", exc)


class JarvisAssistant:
    """Main JARVIS assistant: passive listening, wake word, intent execution."""

    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.recognizer = sr.Recognizer()
        self.tts_lock = threading.Lock()
        self.punishment = PunishmentModule()

        self.engine = pyttsx3.init()
        self._configure_tts_voice_ru()

    def run(self) -> None:
        """Start passive listening loop with microphone auto-reconnect."""
        self.say("Система Джарвис активирована. Ожидаю команду.")

        while not self.stop_event.is_set():
            try:
                with sr.Microphone() as source:
                    self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                    logging.info("Микрофон подключен. Пассивное прослушивание...")

                    while not self.stop_event.is_set():
                        try:
                            audio = self.recognizer.listen(
                                source,
                                timeout=1,
                                phrase_time_limit=PHRASE_TIME_LIMIT_SECONDS,
                            )
                            transcript = self.recognizer.recognize_google(
                                audio,
                                language="ru-RU",
                            ).lower()
                            logging.info("Распознано: %s", transcript)
                            self._process_transcript(transcript)
                        except sr.WaitTimeoutError:
                            continue
                        except sr.UnknownValueError:
                            continue
                        except sr.RequestError as exc:
                            logging.warning("Ошибка сервиса распознавания: %s", exc)
                        except Exception as exc:
                            logging.exception("Ошибка цикла распознавания: %s", exc)
                            time.sleep(0.2)
            except Exception as exc:
                logging.error("Ошибка инициализации микрофона: %s", exc)
                if self.stop_event.wait(MIC_RECONNECT_DELAY_SECONDS):
                    break

    def shutdown(self) -> None:
        """Shutdown assistant and dependent modules."""
        if self.stop_event.is_set():
            return

        self.stop_event.set()
        self.punishment.shutdown()

        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.quit()
        except Exception:
            pass

    def execute_action(self, command_text: str) -> bool:
        """Parse and execute intent from command text. Returns success status."""
        text = command_text.strip().lower()

        # High-priority security command.
        if "режим инквизитора" in text:
            self.punishment.trigger("Команда инквизитора")
            self.say("Режим инквизитора активирован.")
            return True

        # Yandex Music integration.
        music_match = re.search(r"(?:включи|запусти)\s+(.+)", text)
        if (
            ("включи" in text or "запусти" in text)
            and music_match
            and ("яндекс" in text or "музык" in text)
        ):
            query = music_match.group(1).strip()
            self._open_yandex_music(query)
            return True
        if text.startswith("включи "):
            query = text.replace("включи", "", 1).strip()
            if query:
                self._open_yandex_music(query)
                return True

        # Advanced PC control.
        if "сверни всё" in text or "покажи рабочий стол" in text:
            self._minimize_all_windows()
            self._reply_success()
            return True

        if "сделай скриншот" in text:
            self._make_screenshot("shot.png")
            self._reply_success()
            return True

        if "почисти корзину" in text:
            if platform.system().lower() == "windows":
                os.system(r'rd /s /q %systemdrive%\\$Recycle.bin')
                self._reply_success()
                return True
            self.say("Команда очистки корзины доступна только на Windows.")
            return False

        shutdown_match = re.search(r"выключись через\s+(\d+)\s+мин", text)
        if shutdown_match:
            minutes = int(shutdown_match.group(1))
            if platform.system().lower() == "windows":
                os.system(f"shutdown /s /t {minutes * 60}")
                self._reply_success()
                return True
            self.say("Команда выключения через время доступна только на Windows.")
            return False

        # Basic web helpers.
        if "ютуб" in text or "youtube" in text:
            webbrowser.open("https://www.youtube.com")
            self._reply_success()
            return True

        if "новости" in text:
            webbrowser.open("https://yandex.ru/news")
            self._reply_success()
            return True

        if "погод" in text:
            webbrowser.open("https://yandex.ru/pogoda")
            self._reply_success()
            return True

        if "стоп" in text or "заверши работу" in text:
            self.say("Отключаюсь. До связи, сэр.")
            self.shutdown()
            return True

        self.say("Команда не распознана, сэр.")
        return False

    def _process_transcript(self, transcript: str) -> None:
        """Handle transcript in passive mode and trigger security module when needed."""
        if self._contains_forbidden(transcript):
            self.punishment.trigger("Обнаружена запрещенная лексика")

        if WAKE_WORD not in transcript:
            return

        command_text = self._extract_command_after_wake_word(transcript)
        if not command_text:
            self.say("Слушаю вас, сэр.")
            return

        self.execute_action(command_text)

    def _contains_forbidden(self, text: str) -> bool:
        """Check forbidden words in transcript."""
        normalized = text.lower()
        return any(word in normalized for word in FORBIDDEN_WORDS)

    @staticmethod
    def _extract_command_after_wake_word(transcript: str) -> str:
        """Extract command part after wake word."""
        idx = transcript.find(WAKE_WORD)
        if idx == -1:
            return ""

        command = transcript[idx + len(WAKE_WORD):].strip(" ,.!?:;-")
        return command

    def _open_yandex_music(self, query: str) -> None:
        """Open Yandex Music search URL with spoken feedback."""
        url = f"https://music.yandex.ru/search?text={query}"
        self.say(f"Минуту, сэр. Ищу {query} в вашей медиатеке.")
        webbrowser.open(url)
        self._reply_success()

    def _minimize_all_windows(self) -> None:
        """Minimize all windows using PyAutoGUI."""
        import pyautogui

        if platform.system().lower() == "windows":
            pyautogui.hotkey("win", "d")
        else:
            pyautogui.hotkey("command", "f3")

    def _make_screenshot(self, filename: str) -> None:
        """Take screenshot using PyAutoGUI."""
        import pyautogui

        pyautogui.screenshot(filename)

    def _reply_success(self) -> None:
        """Speak positive response for each successful command."""
        self.say(random.choice(SUCCESS_REPLIES))

    def say(self, text: str) -> None:
        """Thread-safe TTS output in Russian."""
        with self.tts_lock:
            logging.info("JARVIS: %s", text)
            self.engine.say(text)
            self.engine.runAndWait()

    def _configure_tts_voice_ru(self) -> None:
        """Try to switch pyttsx3 voice to Russian."""
        try:
            voices = self.engine.getProperty("voices")
            for voice in voices:
                marker = f"{voice.name} {voice.id}".lower()
                if "ru" in marker or "russian" in marker or "рус" in marker:
                    self.engine.setProperty("voice", voice.id)
                    break
        except Exception as exc:
            logging.warning("Не удалось выбрать русский голос: %s", exc)


def configure_logging() -> None:
    """Configure console logging format."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(threadName)s - %(message)s",
    )


def main() -> None:
    """Application entrypoint."""
    configure_logging()
    CommandGenerator(COMMAND_DB_FILE).ensure_db()

    jarvis = JarvisAssistant()
    try:
        jarvis.run()
    except KeyboardInterrupt:
        logging.info("Получен KeyboardInterrupt. Завершение...")
        jarvis.shutdown()


if __name__ == "__main__":
    main()
