import os
import shutil
import subprocess
import threading
from time import monotonic

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
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


class SelectableStatic(Static):
    """Static que permite seleccionar su texto con el ratón."""

    ALLOW_SELECT = True


class ToolCall(SelectableStatic):
    """Muestra un tool call colapsado: solo el comando; click para ver el output."""

    def __init__(self, command: str) -> None:
        self.command = command
        self.output = ""
        self.collapsed = True
        text = Text()
        text.append("⚙ ", style="bold yellow")
        text.append(self.command, style="dim")
        text.append("  ▸", style="dim")
        super().__init__(text)

    def set_output(self, output: str) -> None:
        self.output = output
        self._refresh()

    def _refresh(self) -> None:
        text = Text()
        text.append("⚙ ", style="bold yellow")
        text.append(self.command, style="dim")
        if self.collapsed:
            text.append("  ▸", style="dim")
        else:
            text.append("  ▾", style="dim")
            if self.output:
                text.append("\n")
                text.append(self.output)
        self.update(text)

    def on_click(self, event: events.Click) -> None:
        self.collapsed = not self.collapsed
        self._refresh()
        event.stop()


class SelectableMarkdown(Markdown):
    """Markdown que permite seleccionar su texto con el ratón."""

    ALLOW_SELECT = True


class Thinking(SelectableMarkdown):
    SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        super().__init__("", classes="reasoning sending")
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
            self.remove_class("sending")
            self._update_title()
        self._text = text
        # No renderizar el cuerpo mientras está colapsado: se monta al expandir.
        if not self.collapsed:
            self._show()
        else:
            # Al pasar de "enviando" a razonando, limpiar la marca gris
            # residual del spinner en el cuerpo (si la hubiera).
            if self._word != "enviando":
                self.update("")

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
        if self.has_class("sending"):
            self._show()

    def _update_title(self) -> None:
        if self._word == "razonado":
            prefix = "✻"
        else:
            prefix = self.SPIN_FRAMES[self._frame % len(self.SPIN_FRAMES)]
        suffix = "›" if self.collapsed else "▾"
        self.border_title = f"{prefix} {self._word} {suffix}"

    def _show(self) -> None:
        if self.has_class("sending"):
            prefix = self.SPIN_FRAMES[self._frame % len(self.SPIN_FRAMES)]
            self.update(f"{prefix} {self._word}")
        else:
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


# Evita el crash de Textual al hacer click sobre el texto del spinner de
# "enviando": los párrafos del Markdown se reconstruyen cada 0.1s y en ese
# estado su contenedor puede ser None. Desactivamos la selección solo para
# los párrafos hijos de un Thinking mientras está enviando.
from textual.widgets._markdown import MarkdownParagraph as _MarkdownParagraph


def _thinking_paragraph_allow_select(self) -> bool:
    parent = self.parent
    if parent is None:
        return False
    if isinstance(parent, Thinking) and parent.has_class("sending"):
        return False
    return True


_MarkdownParagraph.allow_select = property(_thinking_paragraph_allow_select)


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


def effort_line(text: str) -> Text:
    line = Text()
    line.append("⚙ ", style=f"bold yellow")
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


class CommandPalette(Vertical):
    """Diálogo centrado con su propio input para filtrar comandos."""

    def __init__(self) -> None:
        super().__init__(id="command-palette")
        self.border_title = "Comandos"

    def compose(self) -> ComposeResult:
        yield PaletteFilter(
            placeholder="Filtrar comandos...",
            id="palette-filter",
            select_on_focus=False,
        )
        yield OptionList(id="palette-options")


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


class PaletteFilter(Input):
    """Input del CommandPalette; las flechas navegan las opciones del diálogo."""

    async def _on_key(self, event: events.Key) -> None:
        options = self.app.query_one("#palette-options", OptionList)
        handled = False

        if options.option_count > 0 and event.key == "down":
            options.action_cursor_down()
            handled = True
        elif options.option_count > 0 and event.key == "up":
            options.action_cursor_up()
            handled = True
        elif event.key == "escape":
            self.app._close_palette()
            handled = True

        if handled:
            event.stop()
            event.prevent_default()
        else:
            await super()._on_key(event)


class HarnessApp(App):
    ALLOW_SELECT = True

    # True mientras se envía, piensa y responde: se puede escribir, no enviar,
    # pero sí se pueden elegir otro modelo o salir.
    _busy: bool = False

    # Modo actual del CommandPalette (ctrl+p): commands, models o effort.
    _palette_mode: str = "commands"

    # Evento para cancelar la llamada al modelo en curso (si la hay).
    _cancel_event: threading.Event | None = None

    BINDINGS = [
        ("ctrl+q", "quit", "Salir"),
        ("ctrl+p", "command_palette", "Comandos"),
        ("ctrl+t", "toggle_effort", "Rotar esfuerzo"),
        ("pageup", "chat_pageup", "Subir"),
        ("pagedown", "chat_pagedown", "Bajar"),
    ]

    CSS = """
    Screen {
        background: transparent;
        layers: dialog;
    }

    .aux {
        padding: 0 1;
        color: $text-muted;
    }

    #split {
        height: 1fr;
    }

    #model-row {
        height: auto;
        width: 1fr;
    }

    #prompt-row {
        height: auto;
        width: 1fr;
    }

    #prompt-prefix {
        width: auto;
        height: 3;
        content-align: center middle;
        text-style: bold;
        color: $primary;
        padding: 0 1 0 0;
    }

    #model {
        width: auto;
    }

    #effort {
        width: auto;
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
        border-top: round $primary;
        border-bottom: round $primary;
        padding: 0 1;
        border-title-color: $primary;
        border-title-style: bold;
    }

    .user {
        width: 1fr;
    }

    .reasoning {
        border-top: round $primary 20%;
        border-bottom: round $primary 20%;
        color: $text-muted;
        text-style: italic;
        border-title-color: $primary;
        border-title-style: bold;
    }

    .sending {
        border: none;
        color: $primary;
        text-style: bold;
    }

    #prompt {
        border: round $primary;
        border-left: none;
        border-right: none;
    }

    #commands {
        display: none;
        margin: 0 1;
        max-height: 6;
        border-top: round $primary;
        border-bottom: round $primary;
    }

    #command-palette {
        layer: dialog;
        position: absolute;
        display: none;
        width: 60;
        height: auto;
        border-top: round $primary;
        border-bottom: round $primary;
        background: $surface;
        padding: 1 2;
    }

    #palette-filter {
        margin-bottom: 1;
        border-left: none;
        border-right: none;
    }

    #palette-options {
        max-height: 8;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="split"):
            yield Chat(id="chat")
            yield CommandMenu(id="commands")
            with Horizontal(id="model-row"):
                yield SelectableStatic(
                    model_line(core.MODEL.name), classes="aux", id="model"
                )
                yield SelectableStatic(
                    effort_line(core.get_current_effort_level()),
                    classes="aux",
                    id="effort",
                )
            with Horizontal(id="prompt-row"):
                yield SelectableStatic("❯", classes="aux", id="prompt-prefix")
                yield Prompt(
                    placeholder=PROMPT_PLACEHOLDER, id="prompt", select_on_focus=False
                )
            yield SelectableStatic(
                "⏎ enviar · «/» o ctrl+p comandos · pgup/pgdn scroll · ctrl+t esfuerzo · ctrl+q salir",
                classes="aux",
                id="hints",
            )
        yield CommandPalette()
        if DEBUG:
            yield DebugLog()

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = THEME.name
        # Permitir selección de texto en la pantalla
        self.screen.ALLOW_SELECT = True
        self.query_one("#chat", Chat).anchor(True)
        self.query_one("#prompt", Input).focus()
        if DEBUG:
            log = self.query_one("#debug-log", DebugLog)
            log.border_title = "debug"
            log.begin_capture_print(stdout=True)

    def on_key(self, event: events.Key) -> None:
        # Cerrar el CommandPalette con Escape.
        palette = self.query_one("#command-palette", CommandPalette)
        if palette.display and event.key == "escape":
            self._close_palette()
            event.stop()
            event.prevent_default()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter en el filtro del CommandPalette: elegir la opción resaltada.
        if event.input.id == "palette-filter":
            options = self.query_one("#palette-options", OptionList)
            if options.highlighted is not None and options.option_count > 0:
                value = options.get_option_at_index(options.highlighted).id
                if value is not None:
                    self._palette_select(value)
            else:
                self._close_palette()
            return

        menu = self.query_one("#commands", CommandMenu)
        text = event.value.strip()

        if menu.mode == "models":
            # sigue el menú de modelos; el Enter elige el resaltado,
            # incluso con el input vacío (lista sin filtrar)
            event.input.value = ""
            self._select_highlighted()
            return

        if menu.display and menu.mode in ("commands", "effort"):
            # Menú abierto (ctrl+p o /effort): elegir la opción resaltada.
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

        # Mientras el modelo piensa o responde no se acepta un nuevo mensaje,
        # pero los comandos (/model, /exit) sí se siguen procesando arriba.
        if self._busy:
            return

        event.input.value = ""
        self._busy = True

        chat = self.query_one("#chat", Chat)
        reasoning = Thinking()
        chat.mount(SelectableStatic(user_line(text), classes="user"), reasoning)
        chat.anchor(True)

        self._ask_model(text, reasoning)

    def on_input_changed(self, event: Input.Changed) -> None:
        # Input dedicado del CommandPalette (ctrl+p): filtrar comandos.
        if event.input.id == "palette-filter":
            self._filter_palette(event.value.strip())
            return

        menu = self.query_one("#commands", CommandMenu)
        text = event.value.strip()

        if menu.mode == "models":
            self._show_menu(
                [
                    (model_id, model.name)
                    for model_id, model in core.MODELS.items()
                    if text.lower() in model.name.lower()
                    or text.lower() in model_id.lower()
                ]
            )
        elif menu.mode == "effort":
            # Filtrar los niveles de esfuerzo según lo que escriba el usuario.
            self._show_menu(
                [
                    (
                        level,
                        "actual" if level == core.get_current_effort_level() else "",
                    )
                    for level in core.get_available_model_effort_levels()
                    if text.lower() in level.lower()
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
        # Selección en el CommandPalette (ctrl+p).
        if event.option_list.id == "palette-options":
            if event.option_id is not None:
                self._palette_select(event.option_id)
            return
        if event.option_list.id != "commands" or event.option_id is None:
            return
        menu = self.query_one("#commands", CommandMenu)
        if menu.mode == "models":
            self._apply_model(event.option_id)
        elif menu.mode == "effort":
            self._apply_effort(event.option_id)
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

    def _close_palette(self) -> None:
        """Cierra el CommandPalette y devuelve el foco al prompt."""
        palette = self.query_one("#command-palette", CommandPalette)
        palette.display = False
        self._palette_mode = "commands"
        prompt = self.query_one("#prompt", Prompt)
        if not prompt.has_focus:
            prompt.focus(scroll_visible=False)
        prompt.cursor_position = len(prompt.value)

    def _filter_palette(self, text: str) -> None:
        """Filtra las opciones mostradas en el CommandPalette según su modo."""
        options = self.query_one("#palette-options", OptionList)
        options.clear_options()
        mode = self._palette_mode
        if mode == "commands":
            for name, description in core.COMANDS.items():
                if text.lower() in name.lower():
                    options.add_option(Option(command_line(name, description), id=name))
        elif mode == "models":
            for model_id, model in core.MODELS.items():
                if (
                    text.lower() in model.name.lower()
                    or text.lower() in model_id.lower()
                ):
                    options.add_option(
                        Option(command_line(model_id, model.name), id=model_id)
                    )
        elif mode == "effort":
            current = core.get_current_effort_level()
            for level in core.get_available_model_effort_levels():
                if text.lower() in level.lower():
                    options.add_option(
                        Option(
                            command_line(
                                level,
                                "actual" if level == current else "",
                            ),
                            id=level,
                        )
                    )
        options.highlighted = 0 if options.option_count > 0 else None

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
        elif menu.mode == "effort":
            self._apply_effort(value)
        else:
            self._run_command(value)

    def _palette_select(self, value: str) -> None:
        """Ejecuta la opción seleccionada en el CommandPalette según su modo."""
        if self._palette_mode == "commands":
            if value == "/model":
                self._palette_mode = "models"
                filter_input = self.query_one("#palette-filter", PaletteFilter)
                filter_input.value = ""
                self._filter_palette("")
                filter_input.focus()
                return
            if value == "/effort":
                self._palette_mode = "effort"
                filter_input = self.query_one("#palette-filter", PaletteFilter)
                filter_input.value = ""
                self._filter_palette("")
                filter_input.focus()
                return
            if value == "/exit":
                self._cancel_stream()
                self.exit()
                return
            self._run_command(value)
            self._close_palette()
            return
        if self._palette_mode == "models":
            self._apply_model(value)
            self._close_palette()
            return
        if self._palette_mode == "effort":
            self._apply_effort(value)
            self._close_palette()
            return

    def _run_command(self, name: str) -> None:
        if name == "/model":
            menu = self.query_one("#commands", CommandMenu)
            menu.mode = "models"
            prompt = self.query_one("#prompt", Prompt)
            prompt.placeholder = "Elige un modelo y pulsa Enter"
            prompt.value = ""
            prompt.focus()
            self._show_menu(
                [(model_id, model.name) for model_id, model in core.MODELS.items()]
            )
        elif name == "/effort":
            self._open_effort_menu()
        elif name == "/exit":
            self._cancel_stream()
            self.exit()

    def _apply_model(self, model_id: str) -> None:
        model = core.MODELS[model_id]
        core.set_model(model)
        self.query_one("#model", Static).update(model_line(core.MODEL.name))
        self.close_menu()

    def _refresh_effort_display(self) -> None:
        self.query_one("#effort", Static).update(
            effort_line(core.get_current_effort_level())
        )

    def action_toggle_effort(self) -> None:
        """Rota al siguiente nivel de esfuerzo disponible para el modelo actual."""
        levels = core.get_available_model_effort_levels()
        if not levels:
            return
        current = core.get_current_effort_level()
        try:
            idx = levels.index(current)
        except ValueError:
            idx = -1
        next_level = levels[(idx + 1) % len(levels)]
        core.set_model_effort(next_level)
        self._refresh_effort_display()

    def _open_effort_menu(self) -> None:
        menu = self.query_one("#commands", CommandMenu)
        menu.mode = "effort"
        levels = core.get_available_model_effort_levels()
        current = core.get_current_effort_level()
        items = [(level, "actual" if level == current else "") for level in levels]
        self._show_menu(items)
        prompt = self.query_one("#prompt", Prompt)
        prompt.placeholder = "Elige un nivel de esfuerzo y pulsa Enter"
        prompt.value = ""
        prompt.focus()

    def _apply_effort(self, level: str) -> None:
        core.set_model_effort(level)
        self._refresh_effort_display()
        self.close_menu()

    def _unknown_command(self, text: str) -> None:
        chat = self.query_one("#chat", Chat)
        line = Text()
        line.append("✗ ", style="bold red")
        line.append(f"comando desconocido: {text} (pulsa / para ver los disponibles)")
        chat.mount(SelectableStatic(line, classes="user"))
        chat.anchor(True)

    @work(thread=True)
    def _ask_model(self, text: str, reasoning: Thinking) -> None:
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        reasoning_text = ""
        answer_text = ""
        current_answer = {"widget": None, "text": "", "written": 0}
        last_flush = 0.0
        current_tool = {"widget": None}

        def flush(force: bool = False) -> None:
            nonlocal last_flush
            now = monotonic()
            if force or now - last_flush >= 0.25:
                last_flush = now
                self.call_from_thread(reasoning.set_text, reasoning_text)
                widget = current_answer["widget"]
                if widget is not None:
                    delta = current_answer["text"][current_answer["written"]:]
                    if delta:
                        current_answer["written"] = len(current_answer["text"])
                        self.call_from_thread(widget.append, delta)

        def ensure_answer_widget() -> None:
            if current_answer["widget"] is not None:
                return
            chat = self.query_one("#chat", Chat)

            def mount() -> None:
                w = SelectableMarkdown("", classes="answer")
                chat.mount(w)
                chat.anchor(True)
                current_answer["widget"] = w

            self.call_from_thread(mount)

        def mount_tool_call(command: str) -> None:
            chat = self.query_one("#chat", Chat)
            widget = ToolCall(command)
            chat.mount(widget)
            chat.anchor(True)
            current_tool["widget"] = widget
            # La respuesta final debe ir después de los tools.
            current_answer["widget"] = None
            current_answer["text"] = ""
            current_answer["written"] = 0

        def set_tool_output(output: str) -> None:
            widget = current_tool["widget"]
            if widget is not None:
                widget.set_output(output)

        try:
            for kind, chunk in core.stream_chat(text, "user", cancel_event):
                if kind == "reasoning":
                    reasoning_text += chunk
                elif kind == "content":
                    if current_answer["widget"] is None and not current_answer["text"]:
                        self.call_from_thread(reasoning.stop_spinner)
                    ensure_answer_widget()
                    current_answer["text"] += chunk
                    answer_text += chunk
                elif kind == "tool_call":
                    self.call_from_thread(mount_tool_call, chunk)
                elif kind == "tool_result":
                    self.call_from_thread(set_tool_output, chunk)
                flush()
        except Exception as error:
            answer_text += f"\n\n**Error:** {error}"
            if current_answer["widget"] is not None:
                current_answer["text"] += f"\n\n**Error:** {error}"
        finally:
            flush(force=True)
            self.call_from_thread(self._finish_stream, reasoning)
            self._cancel_event = None

    def _cancel_stream(self) -> None:
        """Cancela la llamada al modelo en curso, si la hay."""
        event = self._cancel_event
        if event is not None:
            event.set()
            self._cancel_event = None
        core.cancel_active_stream()

    def action_quit(self) -> None:
        self._cancel_stream()
        self.exit()

    def action_command_palette(self) -> None:
        """Abre el CommandPalette centrado con su propio input para filtrar."""
        palette = self.query_one("#command-palette", CommandPalette)
        palette.display = True
        self._palette_mode = "commands"
        # Centrar el diálogo en pantalla.
        width = 60
        height = 12
        x = max(0, (self.size.width - width) // 2)
        y = max(0, (self.size.height - height) // 2)
        palette.styles.offset = (x, y)
        filter_input = self.query_one("#palette-filter", PaletteFilter)
        filter_input.value = ""
        self._filter_palette("")
        filter_input.focus(scroll_visible=False)

    def _copy_to_clipboard(self, text: str) -> None:
        """Copia texto al portapapeles del sistema.

        Usa xclip/wl-copy cuando estén disponibles (más fiable que OSC 52),
        con fallback a la secuencia de escape de Textual.
        """
        clipboard_cmd = shutil.which("wl-copy") or shutil.which("xclip")
        if clipboard_cmd:
            try:
                subprocess.run(
                    [clipboard_cmd, "-selection", "clipboard"]
                    if clipboard_cmd.endswith("xclip")
                    else [clipboard_cmd],
                    input=text,
                    text=True,
                    check=False,
                    capture_output=True,
                )
                return
            except Exception:
                pass
        # Fallback OSC 52: debe ejecutarse en el hilo principal de Textual.
        self.call_from_thread(self.copy_to_clipboard, text)

    def on_text_selected(self, event: events.TextSelected) -> None:
        """Copia al portapapeles el texto que el usuario selecciona con el ratón."""
        text = self.screen.get_selected_text()
        if text:
            # Se ejecuta en un hilo aparte para no bloquear la UI de Textual.
            threading.Thread(
                target=self._copy_to_clipboard, args=(text,), daemon=True
            ).start()
        event.stop()

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
