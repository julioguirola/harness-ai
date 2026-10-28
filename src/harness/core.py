import json
import os
import subprocess
import threading
from collections.abc import Iterator

import requests
from openai import OpenAI
from pydantic import BaseModel


class ReasoningOption(BaseModel):
    type: str
    values: list[str] | None = None


class Limit(BaseModel):
    context: int
    output: int


class AIModel(BaseModel):
    id: str
    name: str
    reasoning: bool
    reasoning_options: list[ReasoningOption] | None = None
    tool_call: bool
    limit: Limit
    temperature: bool
    status: str | None = None


class Provider(BaseModel):
    models: dict[str, AIModel]


MODELS: dict[str, AIModel] = {}

# Llamada de streaming en curso, para poder cancelarla desde la TUI al salir.
_active_completion = None
_active_completion_lock = threading.Lock()


def cancel_active_stream() -> None:
    """Cierra la llamada al streaming en curso, si la hay."""
    global _active_completion
    with _active_completion_lock:
        completion = _active_completion
    if completion is not None:
        try:
            completion.close()
        except Exception:
            pass


def fetch_provider(provider: str):
    global MODELS
    with open("models.json", "r", encoding="utf-8") as file:
        try:
            cached = Provider.model_validate_json(file.read())
            if len(cached.models) > 0:
                MODELS = cached.models
                return
        except:
            pass

    res = requests.get("https://models.dev/api.json")
    provider_fetched = Provider.model_validate(res.json()[provider])
    MODELS = {
        id: model
        for id, model in provider_fetched.models.items()
        if model.reasoning and model.tool_call and model.status != "deprecated"
    }
    with open("models.json", "w", encoding="utf-8") as file:
        file.write(Provider(models=MODELS).model_dump_json(indent=4))


fetch_provider("nvidia")


def tool_exec_command(comand: list[str]) -> str:
    if not isinstance(comand, list) or not comand:
        return "Error: el comando debe ser una lista no vacía de strings"
    if not all(isinstance(x, str) and x.strip() for x in comand):
        return "Error: todos los elementos del comando deben ser strings no vacíos"
    try:
        resultado = subprocess.run(comand, capture_output=True, text=True)
    except Exception as e:
        return f"Error al ejecutar el comando: {e}"
    parts = []
    if resultado.stdout:
        parts.append(resultado.stdout)
    if resultado.stderr:
        parts.append(resultado.stderr)
    parts.append(f"returncode: {resultado.returncode}")
    return "\n".join(parts)


tools = [
    {
        "type": "function",
        "function": {
            "name": "tool_exec_command",
            "description": "Ejecuta un commando de terminal y devuelve el output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "comando": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lista de strings que juntos forman el comando para correr con python subprocess.run",
                    },
                },
                "required": ["comando"],
            },
        },
    }
]

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ["NVIDIA_API_KEY"],
)

MODEL: AIModel = MODELS[next(iter(MODELS))]
EFFORT = {"enable_thinking": False, "reasoning_effort": "none"}

COMANDS = {
    "/model": "Cambiar el modelo de la conversación",
    "/exit": "Salir de la aplicación",
    "/effort": "Seleccionar esfuerzo del modelo",
}


def set_model(model: AIModel):
    global MODEL
    global EFFORT
    MODEL = model
    EFFORT = {"enable_thinking": False, "reasoning_effort": "none"}


def set_model_effort(effort: str):
    global EFFORT
    if effort == "none":
        EFFORT = {"enable_thinking": False}
    elif effort == "default":
        EFFORT = {"enable_thinking": True}
    else:
        EFFORT = {"enable_thinking": True, "reasoning_effort": effort}


def get_available_model_effort_levels() -> list[str]:
    levels = ["none"]
    if not MODEL.reasoning:
        return levels
    for type in MODEL.reasoning_options:
        if type.type == "effort":
            if "none" not in type.values:
                type.values.append("none")
            return type.values
        else:
            levels = ["none", "default"]
    return levels


def get_current_effort_level() -> str:
    """Devuelve el nivel de esfuerzo actual: "none", "default" o el valor de reasoning_effort."""
    if not EFFORT.get("enable_thinking"):
        return "none"
    return EFFORT.get("reasoning_effort", "default")


conversation: list[dict[str, str]] = []


def stream_chat(
    content: str,
    rol: str,
    cancel_event: threading.Event | None = None,
) -> Iterator[tuple[str, str]]:
    global _active_completion
    print(f"Llamando al modelo {MODEL.id} con context={MODEL.limit.context}")
    conversation.append({"role": rol, "content": content})

    while True:
        completion = client.chat.completions.create(
            model=MODEL.id,
            messages=conversation,
            temperature=0.2,
            extra_body={"chat_template_kwargs": EFFORT},
            stream=True,
            max_completion_tokens=MODEL.limit.output,
            tools=tools,
        )
        with _active_completion_lock:
            _active_completion = completion

        tool_calls: dict[int, dict] = {}
        answer = ""
        cancelled = False

        try:
            for chunk in completion:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield "reasoning", reasoning

                if delta.content:
                    answer += delta.content
                    yield "content", delta.content

                for f in delta.tool_calls or []:
                    slot = tool_calls.setdefault(
                        f.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if f.id:
                        slot["id"] = f.id
                    if f.function:
                        if f.function.name:
                            slot["name"] += f.function.name
                        if f.function.arguments:
                            slot["arguments"] += f.function.arguments

        finally:
            with _active_completion_lock:
                if _active_completion is completion:
                    _active_completion = None

            try:
                completion.close()
            except Exception:
                pass

        if cancelled:
            return

        if not tool_calls:
            conversation.append({"role": "assistant", "content": answer})
            return

        conversation.append(
            {
                "role": "assistant",
                "content": answer or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in tool_calls.values()
                ],
            }
        )

        for tc in tool_calls.values():
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError as e:
                yield "tool_call", f"{tc['name']} {tc['arguments']}"
                resultado = f"Argumentos inválidos: {e}"
                yield "tool_result", resultado
                args = None
            if args is not None:
                if not isinstance(args, dict):
                    yield "tool_call", f"{tc['name']} {tc['arguments']}"
                    resultado = "Error: los argumentos deben ser un objeto JSON"
                    yield "tool_result", resultado
                elif tc["name"] == "tool_exec_command":
                    comando = args.get("comando", [])
                    yield (
                        "tool_call",
                        " ".join(comando)
                        if isinstance(comando, list)
                        else str(comando),
                    )
                    resultado = tool_exec_command(comando)
                    yield "tool_result", resultado
                else:
                    yield "tool_call", f"{tc['name']} {tc['arguments']}"
                    resultado = f"Herramienta desconocida: {tc['name']}"
                    yield "tool_result", resultado
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": resultado,
                }
            )
