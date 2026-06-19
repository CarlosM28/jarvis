#!/usr/bin/env python3
"""
Double-clap welcome script para Señor Monteverde.

Detecta 2 aplausos → voz dice bienvenido → suena un MP3 → Claude (Chrome) + Antigravity lado a lado.

Multiplataforma: funciona en Windows y macOS.
La detección de aplausos es AUTO-CALIBRADA: mide el ruido ambiente al arrancar y
detecta el aplauso como un pico relativo, así funciona con cualquier ganancia de micro.

Dependencias:
    pip install sounddevice numpy pyttsx3

Uso:
    python bienvenido_jarvis.py
"""

import os
import sys
import time
import asyncio
import tempfile
import platform
import threading
import subprocess
import webbrowser

import numpy as np
import sounddevice as sd
import pyttsx3

# Salida:
#  • Con pythonw (arranque sin consola) sys.stdout es None y los print reventarían
#    → redirige todo a un archivo de log junto al script.
#  • Con consola en Windows, fuerza UTF-8 (cp1252 revienta con emojis).
if sys.stdout is None or sys.stderr is None:
    _log = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis.log"),
                "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log
elif sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ──────────────────────────────────────────────────────────────────────────────
#  Configuración
# ──────────────────────────────────────────────────────────────────────────────
ES_WINDOWS = platform.system() == "Windows"
ES_MACOS   = platform.system() == "Darwin"

SAMPLE_RATE   = 44100
BLOCK_SIZE    = int(SAMPLE_RATE * 0.05)   # 50 ms por bloque
COOLDOWN      = 0.20    # gap mínimo entre los 2 aplausos (evita contar uno solo dos veces)
DOUBLE_WINDOW = 1.3     # ventana máxima para el 2º aplauso (doble palmada deliberada)

# Detección de aplausos (auto-calibrada). Sube los umbrales si detecta ruido de más.
SENSIBILIDAD  = 6.0     # el pico debe superar el ruido ambiente x este factor
PICO_MINIMO   = 0.10    # pico absoluto mínimo: un aplauso es FUERTE ← sube si detecta cosas suaves
ONSET_RATIO   = 0.5     # el bloque previo debe estar < umbral*ONSET_RATIO (ataque brusco; descarta voz/música sostenida)
CALIB_SEG     = 1.5     # segundos de calibración del ruido ambiente al arrancar
TIMEOUT_DAEMON = 600    # seg en modo --daemon: si no aplaudes en este tiempo, se cierra solo (0 = nunca)

MUSICA        = r"C:\Users\Carlos Monteverde\Desktop\Back in black.mp3"   # suena en segundo plano
CLAUDE_URL    = "https://claude.ai/new"   # se abre en Google Chrome
JARVIS_VOICE  = "en-IE-ConnorNeural"   # voz neural irlandesa estilo Jarvis (edge-tts)
MENSAJE       = "Welcome home, Mister Monteverde. All systems are online and ready at your command."
UMBRAL_RATIO  = 1.5     # ratio mínimo (energía alta/baja): un aplauso es banda ancha; la voz es grave ← sube si cuela voz
NEW_PROJECT   = os.path.expanduser("~/Desktop/nuevo_proyecto")

# ──────────────────────────────────────────────────────────────────────────────
#  Estado global
# ──────────────────────────────────────────────────────────────────────────────
clap_times: list[float] = []
triggered = False
prev_peak = 0.0                            # pico del bloque anterior (para detectar el ataque)
noise_floor = PICO_MINIMO / SENSIBILIDAD   # se ajusta en la calibración
lock = threading.Lock()
secuencia_lista = threading.Event()        # se activa cuando la secuencia termina
MODO_TEST = ("--test" in sys.argv) or ("--calibrar" in sys.argv)   # solo muestra métricas, no dispara
MODO_DAEMON = ("--daemon" in sys.argv) or ("--startup" in sys.argv)  # siempre escuchando (se re-arma)


# ──────────────────────────────────────────────────────────────────────────────
#  Detección de aplausos (auto-calibrada)
# ──────────────────────────────────────────────────────────────────────────────
def _ratio_frecuencia(indata) -> float:
    """Ratio energía-alta / energía-baja. Un aplauso es banda ancha (ratio alto);
    la voz es grave (ratio bajo). Sirve para descartar habla."""
    fft_vals = np.abs(np.fft.rfft(indata[:, 0]))
    fft_freqs = np.fft.rfftfreq(len(indata), 1.0 / SAMPLE_RATE)
    low_energy = np.sum(fft_vals[(fft_freqs >= 100) & (fft_freqs <= 1200)] ** 2)
    high_energy = np.sum(fft_vals[(fft_freqs >= 2000) & (fft_freqs <= 10000)] ** 2)
    return float(high_energy / (low_energy + 1e-6))


def audio_callback(indata, frames, time_info, status):
    global triggered, clap_times, noise_floor, prev_peak

    if triggered:
        return

    # Un aplauso es un transitorio brusco → usamos el PICO del bloque, no el RMS
    peak = float(np.max(np.abs(indata)))
    umbral = max(noise_floor * SENSIBILIDAD, PICO_MINIMO)
    now = time.time()

    fuerte = peak > umbral
    if not fuerte:
        # Bloque tranquilo: actualizamos el ruido ambiente lentamente
        noise_floor = 0.98 * noise_floor + 0.02 * peak

    # Tres condiciones para que cuente como aplauso:
    #  1) fuerte       → supera el umbral (ruido ambiente x sensibilidad, mínimo PICO_MINIMO)
    #  2) ataque       → el bloque anterior estaba en silencio (transitorio brusco, no sostenido)
    #  3) banda_ancha  → mucha energía en alta frecuencia (descarta voz/música grave)
    ataque = prev_peak < umbral * ONSET_RATIO
    ratio = _ratio_frecuencia(indata) if fuerte else 0.0
    banda_ancha = ratio >= UMBRAL_RATIO
    es_aplauso = fuerte and ataque and banda_ancha
    prev_peak = peak

    if MODO_TEST:
        if fuerte:
            if es_aplauso:
                estado = "✅ APLAUSO"
            else:
                motivos = []
                if not ataque:
                    motivos.append("sin-ataque(sostenido)")
                if not banda_ancha:
                    motivos.append("ratio-bajo(grave/voz)")
                estado = "❌ descartado: " + ", ".join(motivos)
            print(f"  evento  pico={peak:.3f}  umbral={umbral:.3f}  ratio={ratio:.2f}  →  {estado}")
        return

    if not es_aplauso:
        return

    with lock:
        # Ignora si estamos en el cooldown del aplauso anterior
        if clap_times and (now - clap_times[-1]) < COOLDOWN:
            return

        clap_times.append(now)
        # Limpia aplausos fuera de la ventana
        clap_times = [t for t in clap_times if now - t <= DOUBLE_WINDOW]

        count = len(clap_times)
        print(f"  👏  Aplauso {count}/2  (pico={peak:.3f} / umbral={umbral:.3f} / ratio={ratio:.2f})")

        if count >= 2:
            triggered = True
            clap_times = []
            threading.Thread(target=secuencia_bienvenida, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────────
#  Secuencia de bienvenida
# ──────────────────────────────────────────────────────────────────────────────
def secuencia_bienvenida():
    print("\n🚀  Iniciando secuencia de bienvenida…\n")

    hablar(MENSAJE)
    reproducir_musica()
    abrir_apps_lado_a_lado()

    print("\n✅  Secuencia completada.\n")
    secuencia_lista.set()   # avisa a main() que ya puede terminar


def hablar(texto: str):
    """Voz de Jarvis. Intenta la voz neural británica; si falla, voz local."""
    print(f"  🔊  Jarvis: «{texto}»")

    if _hablar_edge(texto):
        return
    _hablar_local(texto)


def _hablar_edge(texto: str) -> bool:
    """Voz neural británica (edge-tts). Requiere internet. True si funcionó."""
    try:
        import edge_tts
    except ImportError:
        print("     (edge-tts no instalado → voz local. Instala: pip install edge-tts)")
        return False

    try:
        mp3 = os.path.join(tempfile.gettempdir(), "jarvis_tts.mp3")

        async def _gen():
            await edge_tts.Communicate(texto, JARVIS_VOICE).save(mp3)

        asyncio.run(_gen())
        return _reproducir_mp3(mp3)
    except Exception as e:
        print(f"     (voz neural falló: {e} → voz local)")
        return False


def _reproducir_mp3(path: str) -> bool:
    """Reproduce un MP3 según el sistema operativo."""
    if ES_WINDOWS:
        try:
            import ctypes
            winmm = ctypes.windll.winmm
            p = path.replace("/", "\\")

            def mci(cmd):
                return winmm.mciSendStringW(cmd, None, 0, 0)

            if mci(f'open "{p}" type mpegvideo alias jarvistts') != 0:
                return False
            mci("play jarvistts wait")
            mci("close jarvistts")
            return True
        except Exception:
            return False

    if ES_MACOS:
        return subprocess.run(["afplay", path]).returncode == 0

    # Linux: intenta reproductores comunes
    for cmd in (["mpg123", "-q", path],
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]):
        try:
            return subprocess.run(cmd).returncode == 0
        except FileNotFoundError:
            continue
    return False


def _hablar_local(texto: str):
    """Fallback sin internet: voz del sistema (preferimos en español, sino masculina inglesa)."""
    if ES_MACOS:
        if subprocess.run(["say", texto], capture_output=True).returncode == 0:
            return

    engine = pyttsx3.init()
    voices = engine.getProperty("voices")
    
    # Intentamos buscar voces locales en español
    pref = [v for v in voices if "spanish" in v.name.lower() or "español" in v.name.lower() or "es-" in v.id.lower() or "es_" in v.id.lower()]
    if not pref:
        # Si no hay español, preferimos David/Mark (inglés)
        pref = [v for v in voices if "david" in v.name.lower() or "mark" in v.name.lower()]
        
    if pref:
        engine.setProperty("voice", pref[0].id)
        print(f"     Voz local: {pref[0].name}")
    engine.setProperty("rate", 165 if ES_WINDOWS else 160)
    engine.say(texto)
    engine.runAndWait()


def reproducir_musica():
    """Reproduce el MP3 en segundo plano (proceso independiente que sobrevive al cierre)."""
    print("  🎵  Reproduciendo música en segundo plano…")
    if not os.path.isfile(MUSICA):
        print(f"     (No encontré el MP3: {MUSICA})")
        return

    if ES_WINDOWS:
        _reproducir_musica_windows(MUSICA)
    elif ES_MACOS:
        subprocess.Popen(["afplay", MUSICA], start_new_session=True)
    else:
        for cmd in (["mpg123", "-q", MUSICA],
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", MUSICA]):
            try:
                subprocess.Popen(cmd, start_new_session=True)
                break
            except FileNotFoundError:
                continue
    time.sleep(0.5)  # pequeña pausa antes de seguir abriendo apps


def _reproducir_musica_windows(path: str):
    """PowerShell oculto y desacoplado que reproduce el MP3 hasta el final."""
    ps = (
        "Add-Type -AssemblyName presentationCore;"
        "$p=New-Object System.Windows.Media.MediaPlayer;"
        f"$p.Open([uri]'{path}');"
        "$p.Play();"
        "while(-not $p.NaturalDuration.HasTimeSpan){Start-Sleep -Milliseconds 150};"
        "Start-Sleep -Seconds ([int]$p.NaturalDuration.TimeSpan.TotalSeconds + 1)"
    )
    # CREATE_NO_WINDOW: consola oculta (con DETACHED_PROCESS el PowerShell muere
    # al instante por falta de consola). El hijo sobrevive a la salida de Python.
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
        creationflags=CREATE_NO_WINDOW,
        close_fds=True,
    )


def abrir_apps_lado_a_lado():
    if ES_WINDOWS:
        _abrir_apps_windows()
    else:
        _abrir_apps_macos()


# ──────────────────────────────────────────────────────────────────────────────
#  Apertura de apps — Windows
# ──────────────────────────────────────────────────────────────────────────────
def _abrir_apps_windows():
    os.makedirs(NEW_PROJECT, exist_ok=True)

    # ── Abre Claude en Google Chrome ──────────────────────────────────────────
    print("  🤖  Abriendo Claude en Chrome…")
    chrome_exe = _encontrar_chrome_windows()
    if chrome_exe:
        subprocess.Popen([chrome_exe, "--new-window", CLAUDE_URL])
    else:
        webbrowser.open(CLAUDE_URL)  # fallback: navegador por defecto
    time.sleep(2.0)

    # ── Abre Antigravity con el nuevo proyecto ────────────────────────────────
    print("  💻  Abriendo Antigravity…")
    antigravity_exe = _encontrar_antigravity_windows()
    if antigravity_exe:
        subprocess.Popen([antigravity_exe, NEW_PROJECT])
    else:
        print("     (No encontré Antigravity.exe; ábrelo a mano)")
    time.sleep(2.5)

    # ── Coloca ventanas lado a lado ───────────────────────────────────────────
    print("  🪟  Organizando ventanas…")
    _tile_windows_windows()


def _encontrar_chrome_windows():
    candidatos = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for p in candidatos:
        if p and os.path.isfile(p):
            return p
    return None


def _encontrar_antigravity_windows():
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")
    candidatos = [
        os.path.join(base, "Antigravity IDE", "Antigravity.exe"),
        os.path.join(base, "Antigravity IDE", "Antigravity IDE.exe"),
        os.path.join(base, "Antigravity", "Antigravity.exe"),
    ]
    for p in candidatos:
        if p and os.path.isfile(p):
            return p
    return None


def _tile_windows_windows():
    """Coloca las ventanas de Claude y Antigravity lado a lado con la API de Windows."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        # Área de trabajo (sin contar la barra de tareas)
        rect = wintypes.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        wa_left, wa_top = rect.left, rect.top
        wa_w = rect.right - rect.left
        wa_h = rect.bottom - rect.top
        mitad = wa_w // 2

        encontrados = {}

        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
        )

        def _enum(hwnd, lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            titulo = buff.value.lower()
            if "antigravity" in titulo and "antigravity" not in encontrados:
                encontrados["antigravity"] = hwnd
            elif "claude" in titulo and "claude" not in encontrados:
                encontrados["claude"] = hwnd
            return True

        user32.EnumWindows(EnumWindowsProc(_enum), 0)

        SW_RESTORE = 9
        if "claude" in encontrados:
            h = encontrados["claude"]
            user32.ShowWindow(h, SW_RESTORE)
            user32.MoveWindow(h, wa_left, wa_top, mitad, wa_h, True)
        if "antigravity" in encontrados:
            h = encontrados["antigravity"]
            user32.ShowWindow(h, SW_RESTORE)
            user32.MoveWindow(h, wa_left + mitad, wa_top, wa_w - mitad, wa_h, True)

        if not encontrados:
            print("     (No encontré las ventanas para ordenar; ábrelas a mano si quieres)")
    except Exception as e:
        print(f"     (No se pudieron ordenar las ventanas: {e})")


# ──────────────────────────────────────────────────────────────────────────────
#  Apertura de apps — macOS
# ──────────────────────────────────────────────────────────────────────────────
def _abrir_apps_macos():
    sw, sh = _resolucion_macos()
    mitad = sw // 2

    os.makedirs(NEW_PROJECT, exist_ok=True)

    print("  🤖  Abriendo Claude…")
    subprocess.Popen(["open", "-a", "Claude"])
    time.sleep(1.8)

    print("  💻  Abriendo Cursor…")
    cursor_cmd = _encontrar_cursor_macos()
    if cursor_cmd:
        subprocess.Popen([cursor_cmd, NEW_PROJECT])
    else:
        subprocess.Popen(["open", "-a", "Cursor", NEW_PROJECT])
    time.sleep(1.8)

    print("  🪟  Organizando ventanas…")
    applescript = f"""
    tell application "System Events"
        try
            tell process "Claude"
                set frontmost to true
                set position of window 1 to {{0, 0}}
                set size of window 1 to {{{mitad}, {sh}}}
            end tell
        end try
        try
            tell process "Cursor"
                set frontmost to true
                set position of window 1 to {{{mitad}, 0}}
                set size of window 1 to {{{mitad}, {sh}}}
            end tell
        end try
    end tell
    """
    subprocess.run(["osascript", "-e", applescript], capture_output=True)


def _resolucion_macos() -> tuple[int, int]:
    try:
        out = subprocess.run(
            ["osascript", "-e",
             "tell application \"Finder\" to get bounds of window of desktop"],
            capture_output=True, text=True
        ).stdout.strip()
        parts = [int(x.strip()) for x in out.split(",")]
        return parts[2], parts[3]
    except Exception:
        return 1920, 1080


def _encontrar_cursor_macos():
    candidatos = [
        "/usr/local/bin/cursor",
        "/opt/homebrew/bin/cursor",
        os.path.expanduser("~/.cursor/bin/cursor"),
    ]
    for path in candidatos:
        if os.path.isfile(path):
            return path
    result = subprocess.run(["which", "cursor"], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


# ──────────────────────────────────────────────────────────────────────────────
#  Calibración del micrófono
# ──────────────────────────────────────────────────────────────────────────────
def calibrar_ruido():
    """Mide el ruido ambiente y fija el suelo de ruido inicial."""
    global noise_floor
    print(f"  🎚️   Calibrando ruido ambiente ({CALIB_SEG:.0f}s)… quédate en silencio.")
    rec = sd.rec(int(SAMPLE_RATE * CALIB_SEG), samplerate=SAMPLE_RATE,
                 channels=1, dtype="float32")
    sd.wait()
    a = rec[:, 0]
    ambiente = float(np.percentile(np.abs(a), 99))
    noise_floor = max(ambiente, PICO_MINIMO / SENSIBILIDAD)
    umbral = max(noise_floor * SENSIBILIDAD, PICO_MINIMO)
    print(f"     Ruido ambiente ≈ {ambiente:.4f}  →  umbral de aplauso ≈ {umbral:.3f}")
    if ambiente < 0.0005:
        print("     ⚠️   Señal muy baja: sube la ganancia/volumen del micrófono en Windows.")


# ──────────────────────────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────────────────────────
def _rearmar():
    """Reinicia el estado para volver a escuchar (modo siempre activo)."""
    global triggered, clap_times, prev_peak
    clap_times = []
    prev_peak = 0.0
    triggered = False
    secuencia_lista.clear()
    print("\n👂  Escuchando de nuevo…\n")


def main():
    print("=" * 55)
    print(f"  🎤  Jarvis en {platform.system()} — escuchando aplausos")
    print("      (Ctrl+C para salir)")
    print("=" * 55)

    try:
        dispositivo = sd.query_devices(kind="input")
        print(f"  🎙️   Micrófono: {dispositivo['name']}")
    except Exception:
        pass

    calibrar_ruido()
    if MODO_TEST:
        print("  🧪  MODO PRUEBA: aplaude, habla, golpea la mesa, pon música…")
        print("      Verás las métricas de cada sonido fuerte (no dispara la secuencia).")
        print("      Ajusta PICO_MINIMO / UMBRAL_RATIO según lo que veas. Ctrl+C para salir.\n")
    elif MODO_DAEMON:
        print("  👂  Siempre escuchando en segundo plano. Aplaude 2 veces cuando quieras 👏👏\n")
    else:
        print("  👂  Listo. Aplaude 2 veces 👏👏\n")

    t_ref = time.time()   # referencia para el timeout de inactividad (modo daemon)
    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            channels=1,
            dtype="float32",
            callback=audio_callback,
        ):
            while True:
                time.sleep(0.1)
                if MODO_TEST:
                    continue
                if secuencia_lista.is_set():
                    if not MODO_DAEMON:
                        break                  # una sola vez: termina el programa
                    time.sleep(2)              # respiro tras la secuencia
                    _rearmar()                 # vuelve a escuchar para el próximo aplauso
                    t_ref = time.time()        # reinicia el contador de inactividad
                    continue
                # Timeout de inactividad: si en modo daemon no hay aplausos en
                # TIMEOUT_DAEMON segundos (y no hay secuencia en curso), se cierra.
                if (MODO_DAEMON and TIMEOUT_DAEMON > 0 and not triggered
                        and time.time() - t_ref > TIMEOUT_DAEMON):
                    print(f"\n⌛  {TIMEOUT_DAEMON // 60} min sin aplausos → Jarvis se cierra. 👋\n")
                    break
    except KeyboardInterrupt:
        print("\n\nHasta luego! 👋")
        sys.exit(0)

    if not MODO_DAEMON:
        print("🛑  Jarvis ha completado su trabajo. Hasta luego, señor. 👋")


if __name__ == "__main__":
    main()
