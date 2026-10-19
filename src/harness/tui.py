from time import monotonic

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.theme import Theme
from textual.widgets import Input, Markdown, Static

from .core import MODEL, stream_chat

BLUE = "#5b9dff"


class Chat(VerticalScroll):
    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        super()._on_mouse_scroll_down(event)
        if self.scroll_y >= self.max_scroll_y:
            self.anchor(True)


class Thinking(Markdown):
    SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        super().__init__("", classes="reasoning")
        self._text = ""
        self.collapsed = False
        self._word = "enviando"
        self._frame = 0
        self._timer = None
        self._update_title()

    def _on_mount(self, event: events.Mount) -> None:
        self._timer = self.set_interval(0.1, self._spin)

    def set_text(self, text: str) -> None:
        if text and self._word == "enviando":
            self._word = "pensando"
            self._update_title()
        self._text = text
        self._show()

    def stop_spinner(self) -> None:
        if self._text:
            if self._timer is not None:
                self._timer.stop()
                self._timer = None
            self._word = "razonado"
        self._update_title()

    def toggle(self) -> None:
        self.collapsed = not self.collapsed
        self._update_title()
        self._show()

    def _spin(self) -> None:
        self._frame += 1
        self._update_title()

    def _update_title(self) -> None:
        if self._word == "razonado":
            prefix = "✻"
        else:
            prefix = self.SPIN_FRAMES[self._frame % len(self.SPIN_FRAMES)]
        suffix = "›" if self.collapsed else "▾"
        self.border_title = f"{prefix} {self._word} {suffix}"

    def _show(self) -> None:
        self.update("" if self.collapsed else self._text)

    def _on_click(self, event: events.Click) -> None:
        if event.style is not None and event.style.meta and event.style.meta.get("@click"):
            return
        self.toggle()
        event.stop()


THEME = Theme(
    name="harness-blue",
    primary=BLUE,
    secondary="#2563eb",
    accent="#4cc2ff",
    dark=True,
)


def user_line(text: str) -> Text:
    line = Text()
    line.append("❯ ", style=f"bold {BLUE}")
    line.append(text)
    return line


def model_line(text: str) -> Text:
    line = Text()
    line.append("✻ ", style=f"bold {BLUE}")
    line.append(text)
    return line


class HarnessApp(App):
    ALLOW_SELECT = False

    BINDINGS = [
        ("ctrl+q", "quit", "Salir"),
        ("pageup", "chat_pageup", "Subir"),
        ("pagedown", "chat_pagedown", "Bajar"),
    ]

    CSS = """
    Screen {
        background: transparent;
    }

    .aux {
        padding: 0 1;
        color: $text-muted;
    }

    #chat {
        height: 1fr;
        padding: 0 1;
    }

    #chat > * {
        margin-bottom: 1;
    }

    .user {
        width: 1fr;
    }

    .reasoning {
        border: round $primary 20%;
        color: $text-muted;
        text-style: italic;
        border-title-color: $primary;
        border-title-style: bold;
    }

    #prompt {
        border: round $primary;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(model_line(MODEL), classes="aux", id="model")
        yield Chat(id="chat")
        yield Input(placeholder="Escribe un mensaje y pulsa Enter", id="prompt")
        yield Static(
            "⏎ enviar · pgup/pgdn scroll · ctrl+q salir", classes="aux", id="hints"
        )

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = THEME.name
        self.query_one("#chat", Chat).anchor(True)
        self.query_one("#prompt", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        event.input.value = ""
        event.input.disabled = True

        chat = self.query_one("#chat", Chat)
        reasoning = Thinking()
        answer = Markdown("", classes="answer")
        chat.mount(Static(user_line(text), classes="user"), reasoning, answer)
        chat.anchor(True)

        self._ask_model(text, reasoning, answer)

    @work(thread=True)
    def _ask_model(self, text: str, reasoning: Thinking, answer: Markdown) -> None:
        reasoning_text = ""
        answer_text = ""
        last_flush = 0.0

        def flush(force: bool = False) -> None:
            nonlocal last_flush
            now = monotonic()
            if force or now - last_flush >= 0.15:
                last_flush = now
                self.call_from_thread(reasoning.set_text, reasoning_text)
                self.call_from_thread(answer.update, answer_text)

        try:
            for kind, chunk in stream_chat(text):
                if kind == "reasoning":
                    reasoning_text += chunk
                else:
                    if not answer_text:
                        self.call_from_thread(reasoning.stop_spinner)
                    answer_text += chunk
                flush()
        except Exception as error:
            answer_text += f"\n\n**Error:** {error}"
        finally:
            flush(force=True)
            self.call_from_thread(self._finish_stream, reasoning)

    def action_chat_pageup(self) -> None:
        self.query_one("#chat", Chat).scroll_page_up(animate=False)

    def action_chat_pagedown(self) -> None:
        chat = self.query_one("#chat", Chat)
        chat.scroll_page_down(animate=False)
        if chat.scroll_y >= chat.max_scroll_y:
            chat.anchor(True)

    def _finish_stream(self, reasoning: Thinking) -> None:
        reasoning.stop_spinner()
        if not reasoning._text:
            reasoning.remove()
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = False
        prompt.focus()


def main() -> None:
    HarnessApp().run()


if __name__ == "__main__":
    main()
