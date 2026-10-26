import os
from time import monotonic

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.theme import Theme
from textual.widgets import Input, Log, Markdown, OptionList, Static
from textual.widgets.option_list import Option

from . import core

BLUE = "#5b9dff"

PROMPT_PLACEHOLDER = "Escribe un mensaje y pulsa Enter"

DEBUG = os.environ.get("DEBUG") == "1"


class Chat(VerticalScroll):
    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        super()._on_mouse_scroll_down(event)
        if self.scroll_y >= self.max_scroll_y:
            self.anchor(True)


class DebugLog(Log):
    """Panel que captura las salidas de `print` cuando DEBUG=1."""

    def __init__(self) -> None:
        super().__init__(auto_scroll=True, max_lines=1000, id="debug-log")

    def on_print(self, event: events.Print) -> None:
        self.write_line(event.text)


class Thinking(Markdown):
    SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        super().__init__("", classes="reasoning")
        self._text = ""
        self.collapsed = True
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
        if (
            event.style is not None
            and event.style.meta
            and event.style.meta.get("@click")
        ):
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


def command_line(value: str, description: str) -> Text:
    line = Text()
    line.append(value, style=f"bold {BLUE}")
    if description:
        line.append(f"  {description}", style="dim")
    return line


class CommandMenu(OptionList):
    """Menú de opciones sobre el prompt: comandos (`/`) o modelos."""

    mode: str | None = None


class Prompt(Input):
    """Input del chat; con el menú abierto, las flechas y Enter lo navegan."""

    async def _on_key(self, event: events.Key) -> None:
        menu = self.app.query_one("#commands", CommandMenu)
        handled = False

        if menu.display and event.key == "down":
            menu.action_cursor_down()
            handled = True
        elif menu.display and event.key == "up":
            menu.action_cursor_up()
            handled = True
        elif menu.display and menu.mode == "commands" and event.key == "tab":
            self.app.complete_command()
            handled = True
        elif event.key == "escape" and (menu.display or menu.mode == "models"):
            self.app.close_menu()
            handled = True

        if handled:
            event.stop()
            event.prevent_default()
        else:
            await super()._on_key(event)


class HarnessApp(App):
    ALLOW_SELECT = False

    # True mientras se envía, piensa y responde: se puede escribir, no enviar.
    _busy: bool = False

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

    #split {
        height: 1fr;
    }

    #chat {
        height: 1fr;
        padding: 0 1;
    }

    #chat > * {
        margin-bottom: 1;
    }

    #debug-log {
        height: 1fr;
        border: round $primary;
        padding: 0 1;
        border-title-color: $primary;
        border-title-style: bold;
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

    #commands {
        display: none;
        margin: 0 1;
        max-height: 6;
        border: round $primary;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(model_line(core.MODEL.name), classes="aux", id="model")
        with Vertical(id="split"):
            yield Chat(id="chat")
            yield CommandMenu(id="commands")
            yield Prompt(placeholder=PROMPT_PLACEHOLDER, id="prompt")
            yield Static(
                "⏎ enviar · «/» comandos · pgup/pgdn scroll · ctrl+q salir",
                classes="aux",
                id="hints",
            )
        if DEBUG:
            yield DebugLog()

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = THEME.name
        self.query_one("#chat", Chat).anchor(True)
        self.query_one("#prompt", Input).focus()
        if DEBUG:
            log = self.query_one("#debug-log", DebugLog)
            log.border_title = "debug"
            log.begin_capture_print(stdout=True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._busy:
            return

        menu = self.query_one("#commands", CommandMenu)
        text = event.value.strip()

        if menu.mode == "models":
            # sigue el menú de modelos; el Enter elige el resaltado,
            # incluso con el input vacío (lista sin filtrar)
            event.input.value = ""
            self._select_highlighted()
            return

        if not text:
            return

        if text.startswith("/"):
            event.input.value = ""
            if menu.display:
                self._select_highlighted()
            else:
                self._unknown_command(text)
            return

        event.input.value = ""
        self._busy = True

        chat = self.query_one("#chat", Chat)
        reasoning = Thinking()
        answer = Markdown("", classes="answer")
        chat.mount(Static(user_line(text), classes="user"), reasoning, answer)
        chat.anchor(True)

        self._ask_model(text, reasoning, answer)

    def on_input_changed(self, event: Input.Changed) -> None:
        menu = self.query_one("#commands", CommandMenu)
        text = event.value.strip()

        if menu.mode == "models":
            self._show_menu(
                [
                    (model_id, model.name)
                    for model_id, model in core.MODELS.items()
                    if text.lower() in model.name.lower()
                ]
            )
        elif text.startswith("/"):
            self._show_menu(
                [
                    (name, description)
                    for name, description in core.COMANDS.items()
                    if name.startswith(text)
                ]
            )
        else:
            self.close_menu()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "commands" or event.option_id is None:
            return
        menu = self.query_one("#commands", CommandMenu)
        if menu.mode == "models":
            self._apply_model(event.option_id)
        else:
            self._run_command(event.option_id)

    def _show_menu(self, items: list[tuple[str, str]]) -> None:
        menu = self.query_one("#commands", CommandMenu)
        menu.clear_options()
        for value, description in items:
            menu.add_option(Option(command_line(value, description), id=value))
        menu.highlighted = 0 if items else None
        menu.display = bool(items)

    def close_menu(self) -> None:
        menu = self.query_one("#commands", CommandMenu)
        if not menu.display and menu.mode is None:
            return
        menu.mode = None
        menu.display = False
        prompt = self.query_one("#prompt", Prompt)
        prompt.placeholder = PROMPT_PLACEHOLDER
        if not prompt.has_focus:
            prompt.focus()

    def complete_command(self) -> None:
        menu = self.query_one("#commands", CommandMenu)
        if menu.highlighted is None:
            return
        value = menu.get_option_at_index(menu.highlighted).id
        if value:
            self.query_one("#prompt", Prompt).value = value

    def _select_highlighted(self) -> None:
        menu = self.query_one("#commands", CommandMenu)
        if menu.highlighted is None:
            return
        value = menu.get_option_at_index(menu.highlighted).id
        if value is None:
            return
        if menu.mode == "models":
            self._apply_model(value)
        else:
            self._run_command(value)

    def _run_command(self, name: str) -> None:
        if name == "/model":
            menu = self.query_one("#commands", CommandMenu)
            menu.mode = "models"
            prompt = self.query_one("#prompt", Prompt)
            prompt.placeholder = "Elige un modelo y pulsa Enter"
            prompt.value = ""
            prompt.focus()
            self._show_menu([(model_id, model.name) for model_id, model in core.MODELS.items()])
        elif name == "/exit":
            self.exit()

    def _apply_model(self, model_id: str) -> None:
        model = core.MODELS[model_id]
        core.select_model(model)
        self.query_one("#model", Static).update(model_line(core.MODEL.name))
        self.close_menu()

    def _unknown_command(self, text: str) -> None:
        chat = self.query_one("#chat", Chat)
        line = Text()
        line.append("✗ ", style="bold red")
        line.append(f"comando desconocido: {text} (pulsa / para ver los disponibles)")
        chat.mount(Static(line, classes="user"))
        chat.anchor(True)

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
            for kind, chunk in core.stream_chat(text):
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
        self._busy = False
        prompt = self.query_one("#prompt", Input)
        prompt.focus()


def main() -> None:
    HarnessApp().run()


if __name__ == "__main__":
    main()
