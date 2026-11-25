# farmout

[English](../README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · **Español**

**Llega menos al límite. Sigue trabajando cuando llegues.**

farmout reparte el trabajo entre las suscripciones de IA que ya pagas: Claude, Codex, Gemini, GitHub Copilot, DeepSeek, una segunda cuenta de Claude y modelos locales. Dales las lecturas largas y las revisiones y la cuota de tu modelo principal durará más; cuando una llega a su límite de uso o su sesión caduca, la tarea pasa a la siguiente y la conversación va con ella. Conversaciones que puedes ramificar desde cualquier turno, revisión en paralelo, carreras que deciden tus pruebas y pipelines, desde tu terminal o dentro de Claude Code. De solo lectura por defecto y sin dependencias.

[![CI](https://github.com/JayPokale/farmout/actions/workflows/ci.yml/badge.svg)](https://github.com/JayPokale/farmout/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.9%2B-3776ab)
![dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![MCP](https://img.shields.io/badge/MCP-stdio-6e56cf)
![license](https://img.shields.io/badge/license-MIT-blue)

> **Nota:** esta traducción es un resumen. Para todas las funciones (el worker de DeepSeek, skills, hooks, la tienda, los ajustes), consulta el [README en inglés](../README.md).

```bash
pipx install git+https://github.com/JayPokale/farmout
cd your-repo && farmout bench --share   # ¿qué IA encuentra mejor los bugs reales de tu propio código?
```

<p align="center"><img src="../assets/limits-demo.gif" width="900" alt="Una ejecución real, acelerada solo mientras espera: farmout run claude inicia una conversación; con Claude sin sesión, farmout send pasa el siguiente turno a Codex, que responde con la conversación hasta ahí; luego farmout bench compara Claude Opus y Sonnet en tres correcciones pasadas y --apply deja al ganador como predeterminado"></p>

- **Sigue al llegar al límite**: cuando una suscripción llega a su límite de uso o tiene la sesión cerrada, una tarea, conversación o etapa de pipeline de farmout pasa al siguiente modelo disponible con los turnos anteriores, en vez de fallar. También puedes cambiar a propósito (`farmout send cache -w codex`, o `--model opus`). También tu propia sesión de Claude Code o Codex: cuando te avisa de que llegaste al límite, `farmout continue --write` hace que otra suscripción siga la misma conversación (tus prompts, sus respuestas y una línea por cada acción).
- **Con una sola suscripción basta**: sus modelos llenan los carriles (`farmout review -w claude:opus,claude:sonnet`), y `farmout bench --apply` deja como predeterminado el modelo que mejor lo hizo con tu propio código.
- **Conversaciones como un grafo**: `farmout fork cache --at 2 -w codex "..."` ramifica desde cualquier turno hacia otro modelo, y `farmout ui` dibuja todas tus conversaciones como un grafo con ramas en la terminal: lee cualquier turno, continúalo o ramifica desde él (`farmout tree` imprime el mismo grafo). Las conversaciones sobreviven a una carpeta renombrada o movida.
- **`/farmout:review`**: Codex, Gemini (mediante Antigravity), GitHub Copilot y una segunda cuenta de Claude revisan tu diff a la vez, en modo de solo lectura. Claude combina los hallazgos, cuenta cuántos modelos señalaron cada problema y verifica cada hallazgo contra el código. En [nuestro benchmark](../bench/README.md), cada uno de los 32 bugs (23 introducidos a propósito y 9 regresiones reales de proyectos open source) fue señalado por al menos tres de los cuatro modelos.
- **`/farmout:race <task>`**: cada modelo intenta la misma corrección en su propio git worktree; tus pruebas eligen al ganador y nada modifica tu árbol hasta que lo indiques.
- **Delegación diaria**: Claude delega lecturas largas y código repetitivo a la suscripción más adecuada. Resumir tres artículos así le costó a Claude **59% menos tokens** (74% menos coste) para obtener las mismas respuestas.

Pagas por Claude. Quizá también por ChatGPT, Google AI, una segunda licencia de Claude o ejecutas un modelo abierto en una GPU. Aun así, la cuota de tu mejor modelo sigue consumiéndose leyendo PDF y escribiendo código repetitivo mientras los demás están inactivos.

farmout convierte Claude Code en la **cabeza**. Planifica, divide un trabajo en subtareas independientes, envía cada una a un modelo suficientemente bueno para ella y reserva su propia cuota para las partes difíciles:

- un artículo de 60 páginas va a **Antigravity** (Gemini), que lo lee y devuelve 150 palabras
- "escribe pruebas para `parse_date()`" va a **Codex**, que las escribe y ejecuta
- el trabajo de complejidad media va a tu **segunda cuenta de Claude**
- una segunda opinión sobre un cambio complejo va a **GitHub Copilot**, que ofrece modelos de varios proveedores
- clasificar 2.000 líneas va a **cualquier modelo compatible con OpenAI** (Ollama, vLLM, OpenRouter, Qwen, ...)

Todo se ejecuta a la vez desde una única llamada a herramienta. Cada worker es la CLI oficial del proveedor, autenticada con tu propia suscripción, por lo que los workers de CLI no necesitan claves de API.

## La línea de comandos

farmout también es una CLI. Instálala con `pipx install` desde un clon del repositorio (pronto en PyPI) y comprueba tus workers con `farmout doctor --live`:

```bash
# todos los modelos revisan tu cambio; hallazgos combinados con su nivel de acuerdo
farmout review
# cada modelo en su propio worktree; tus pruebas eligen al ganador
farmout race "Haz determinista el test inestable de tests/test_sync.py" --test "pytest tests/test_sync.py -x"
# una sesión que recuerda la conversación
farmout run codex "Añade tests para parse_date() en src/dates.py" --isolate -n fechas
farmout send fechas "Cubre también los años bisiestos"
# aplica el parche a tu árbol (o --pr)
farmout apply fechas
# ramifica desde cualquier turno de la conversación, con otro modelo
farmout fork fechas --at 1 -w claude "Prueba también entradas con zona horaria"
# todas las conversaciones como un grafo en la terminal: lee, continúa o ramifica
farmout ui
# revisar → corregir lo que 2+ modelos acuerdan → revisar la corrección
farmout pipe run fix
# qué hizo cada suscripción por ti
farmout stats
```

Sesiones, pipelines (archivos `.toml` o `.json` con etapas, compuertas y aprobaciones), ajustes por repositorio (`.farmout/settings.toml`: qué proveedores pueden ver el código, sin escritura, sin web), el cambio automático de worker cuando uno agota su cuota y un hook de git que revisa cada commit: los detalles están en el [README en inglés](../README.md).

## Inicio rápido

Requisitos: Python 3.9+, Linux (probado) o macOS (sin probar), [Claude Code](https://github.com/anthropics/claude-code) y al menos un worker:

| worker | necesita | adecuado para |
|---|---|---|
| `antigravity` | [Antigravity CLI](https://antigravity.google) (`agy`), con sesión iniciada | PDF largos, artículos, lectura de muchos archivos, investigación web; Gemini y otros modelos mostrados por `agy models` |
| `codex` | [Codex CLI](https://github.com/openai/codex), con sesión iniciada | programación bien especificada, pruebas, refactors, revisiones |
| `claude` | una segunda cuenta de Claude Code + `FARMOUT_CLAUDE_DIR`, o tu sesión principal con `FARMOUT_CLAUDE_MAIN=1` | trabajo general con cuota independiente |
| `copilot` | [GitHub Copilot CLI](https://github.com/github/copilot-cli), con sesión iniciada | revisión de código, preguntas concretas de programación, segundas opiniones; modelos de varios proveedores |
| `api` | `FARMOUT_API_BASE` + `FARMOUT_API_MODEL` | trabajo masivo o simple con cualquier modelo compatible con OpenAI |

```bash
git clone https://github.com/JayPokale/farmout ~/.local/share/farmout
claude mcp add -s user farmout -- python3 ~/.local/share/farmout/server.py
claude mcp list        # farmout: ... ✔ Connected
```

O instálalo como plugin de Claude Code; `claude plugin update farmout` lo mantiene actualizado:

```bash
claude plugin install farmout --marketplace JayPokale/farmout
```

El servidor de un plugin lee su configuración de tu entorno; por tanto, con el plugin, usa `export` para las variables `FARMOUT_*` siguientes en tu perfil de shell en vez de pasar `-e`. Usa un único método de instalación, no ambos.

**Otras cabezas.** Codex CLI y Copilot CLI también pueden manejar farmout, así que pueden delegar trabajo en Claude, Gemini o entre sí (ambos probados con una ida y vuelta al worker `claude`):

```bash
codex mcp add farmout -- python3 ~/.local/share/farmout/server.py
copilot mcp add farmout -- python3 ~/.local/share/farmout/server.py
```

Codex pide confirmación antes de cada llamada a una herramienta MCP. Para `codex exec`, añade `default_tools_approval_mode = "approve"` bajo `[mcp_servers.farmout]` en `~/.codex/config.toml`. Los comandos slash solo funcionan en Claude Code.

Reinicia Claude Code. Los workers se detectan al iniciar. Para ver cuáles están preparados y cómo habilitar el resto, ejecuta `python3 server.py doctor` (o `/farmout:setup` con el plugin); `doctor --live` también envía a cada uno un prompt pequeño para comprobar que su inicio de sesión funciona.

## Uso

### Comandos slash (instalación mediante plugin)

| comando | qué hace |
|---|---|
| `/farmout:review [--base <ref>] [focus]` | cada worker revisa en paralelo y en modo de solo lectura tus cambios sin confirmar (o la rama desde `<ref>`); Claude combina los hallazgos, cuenta cuántos modelos señalaron cada uno y los verifica contra el código |
| `/farmout:ask <question>` | la misma pregunta a cada worker; obtienes el consenso, los desacuerdos y el veredicto de Claude |
| `/farmout:race <task>` | cada worker con capacidad de escritura intenta el mismo cambio en su propio git worktree, en paralelo; Claude ejecuta tus pruebas en cada intento, compara los parches y aplica el mejor cuando estés de acuerdo |
| `/farmout:setup` | qué workers están preparados y el paso exacto para habilitar cada uno que falte |

## Seguridad

| worker | solo lectura (predeterminado) | `write: true` |
|---|---|---|
| `antigravity` | `agy -p … --mode plan` | `--mode accept-edits --sandbox` |
| `codex` | `codex exec -s read-only` | `-s workspace-write` |
| `claude` | `claude -p --tools Read,Grep,Glob,WebSearch` | `--permission-mode acceptEdits` |
| `copilot` | `copilot -p … --allow-all-tools --available-tools view rg glob web_fetch` | añade `apply_patch edit create`; nunca una shell |
| `api` | sin herramientas | n/a |

---

Esta es una traducción abreviada. El [README](../README.md) en inglés es la referencia para la configuración, la herramienta `delegate`, notas de campo, las preguntas frecuentes y los benchmarks.
