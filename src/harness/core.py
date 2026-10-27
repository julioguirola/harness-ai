import json
import os
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
    with open("models.json", "w", encoding="utf-8") as file:
        file.write(json.dumps(res.json()[provider], indent=4))
    provider_fetched = Provider.model_validate(res.json()[provider])
    MODELS = provider_fetched.models


fetch_provider("nvidia")


class CommandResult:
    def __init__(self, stdout: str, stderr: str, returncode: int):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# def tool_exec_command(comand: list[str]) -> CommandResult:
#     resultado = subprocess.run(comand, capture_output=True, text=True)
#     comand_result = CommandResult(
#         returncode=resultado.returncode,
#         stdout=resultado.stdout,
#         stderr=resultado.stderr,
#     )
#     return comand_result


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
    user_content: str, cancel_event: threading.Event | None = None
) -> Iterator[tuple[str, str]]:
    global _active_completion
    print(f"Llamando al modelo {MODEL.id} con context={MODEL.limit.context}")
    conversation.append({"role": "user", "content": user_content})

    completion = client.chat.completions.create(
        model=MODEL.id,
        messages=conversation,
        temperature=0.2,
        extra_body={"chat_template_kwargs": EFFORT},
        stream=True,
        max_completion_tokens=MODEL.limit.context,
    )

    with _active_completion_lock:
        _active_completion = completion

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
    finally:
        with _active_completion_lock:
            if _active_completion is completion:
                _active_completion = None

        if not cancelled:
            conversation.append({"role": "assistant", "content": answer})

        try:
            completion.close()
        except Exception:
            pass
