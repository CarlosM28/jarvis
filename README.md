# 👏 Jarvis - Double Clap Home Automation

Aplaude 2 veces y Jarvis te da la bienvenida, pone música y abre tus apps.

## ¿Qué hace?
1. Detecta 2 aplausos por el micrófono (umbral **auto-calibrado** al ruido ambiente)
2. La voz de Jarvis (neural irlandesa) dice **"Welcome home, Mister Monteverde. All systems are online and ready at your command."**
3. Reproduce un **MP3 local en segundo plano** (`Back in black.mp3`)
4. Abre **Claude** (en Chrome) y **Antigravity IDE** lado a lado

## Instalación

```bash
pip install sounddevice numpy pyttsx3 edge-tts
```

> `edge-tts` da la voz neural irlandesa estilo Jarvis (`en-IE-ConnorNeural`) y
> **requiere internet**. Si no hay conexión o no está instalado, cae a la voz
> local del sistema (Microsoft David). Cambia la voz/frase en `JARVIS_VOICE` y
> `MENSAJE` del script.

## Uso

```bash
python bienvenido_jarvis.py
```

Al arrancar mide el ruido ambiente (~1.5s, quédate en silencio) y fija el umbral
del aplauso automáticamente. Luego aplaude 2 veces 👏👏.

## Plataformas

Multiplataforma (**Windows** y **macOS**), detecta el sistema solo:

| | Windows | macOS |
|---|---|---|
| Voz | edge-tts `en-IE-ConnorNeural` (fallback: Microsoft David) | edge-tts (fallback: `say`) |
| Reproducción | MCI / `winmm` (sin deps extra) | `afplay` |
| Música | MP3 en 2º plano (PowerShell + WPF MediaPlayer, oculto) | `afplay` |
| Claude | Google Chrome → `https://claude.ai/new` | app de escritorio |
| Editor | Antigravity IDE (`%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity.exe`) | CLI `cursor` |
| Ventanas | API de Windows (ctypes) | AppleScript |

## Detección de aplausos

Para contar como aplauso, un sonido debe cumplir **3 condiciones** (así no cuela
voz, música ni golpes):
1. **Fuerte** — supera el umbral (ruido ambiente × `SENSIBILIDAD`, mínimo `PICO_MINIMO`).
2. **Ataque brusco** — el instante anterior estaba en silencio (descarta sonidos
   sostenidos como el habla).
3. **Banda ancha** — mucha energía en alta frecuencia (`ratio ≥ UMBRAL_RATIO`); la
   voz es grave y se descarta.

### Modo prueba / calibración
```bash
python bienvenido_jarvis.py --test
```
Muestra las métricas (`pico`, `umbral`, `ratio`) de cada sonido fuerte y si lo
aceptaría o no, **sin disparar la secuencia**. Aplaude, habla y haz ruido, mira
los números y ajusta `PICO_MINIMO` (sube si detecta cosas suaves) o `UMBRAL_RATIO`
(sube si cuela voz).

### Si no detecta nada
- Ganancia muy baja: *Configuración → Sonido → Micrófono → Volumen al 100%* (+ realce).
- Micro RØDE con **RØDE Connect**: que el canal no esté en mute, sube su ganancia
  o cierra RØDE Connect para usar el micro directo.

## Arranque automático en segundo plano (Windows)

```bash
pythonw bienvenido_jarvis.py --daemon
```
- `--daemon` → **siempre escuchando**: tras cada saludo se re-arma y espera el
  próximo doble aplauso (en vez de terminar).
- `pythonw` → sin ventana de consola. La salida va a `jarvis.log` junto al script.
- **Timeout de inactividad**: si no aplaudes en `TIMEOUT_DAEMON` segundos (600 = 10 min)
  el programa se cierra solo. Pon `0` para que nunca se cierre.

**Que arranque al iniciar sesión**: hay un acceso directo `Jarvis.lnk` en la carpeta
de Inicio (`Win+R` → `shell:startup`) que lanza `pythonw … --daemon`. Para quitarlo,
borra ese acceso directo. Para arrancarlo a mano sin reiniciar, doble clic en él.

## Requisitos
- Windows 10/11 o macOS
- Python 3.9+
- Micrófono
