# harness

Proyecto de aprendizaje sobre **AI harness engineering**: un harness de chat minimalista en Python que conecta con un LLM y lo presenta en una TUI interactiva.

## Características

- Chat por streaming con [Nemotron](https://build.nvidia.com/nvidia) mediante la API compatible con OpenAI.
- Muestra el razonamiento del modelo (*thinking*) en un bloque colapsable con spinner.
- TUI construida con [Textual](https://textual.textualize.io/) y tema propio.

## Estructura

```
src/harness/
├── core.py   # Lógica: cliente del LLM y stream_chat()
├── tui.py    # Interfaz: app Textual, widgets y estilos
└── __init__.py
```

El código mantiene la separación entre **core** y **TUI** (ver [AGENTS.md](AGENTS.md)).

## Requisitos

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/)
- Variable de entorno `NVIDIA_API_KEY`

## Uso

```sh
uv run --env-file .env harness
```

## Atajos

| Tecla | Acción |
|---|---|
| `⏎` | Enviar mensaje |
| `pgup` / `pgdn` | Scroll del chat |
| `ctrl+q` | Salir |
