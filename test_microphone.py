import sys
import time
import numpy as np
import sounddevice as sd

# En Windows la consola usa cp1252 y revienta al imprimir emojis → forzamos UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

SAMPLE_RATE = 44100
BLOCK_SIZE = int(SAMPLE_RATE * 0.05)  # 50 ms
PICO_MINIMO = 0.03

print("=" * 60)
print("  🎤 Test de Micrófono y Análisis de Frecuencia (FFT)")
print("  Este script te ayudará a ver la diferencia entre hablar y aplaudir.")
print("  Habla al micrófono o da un aplauso para ver los valores en tiempo real.")
print("  Presiona Ctrl+C para salir.")
print("=" * 60)

def audio_callback(indata, frames, time_info, status):
    peak = float(np.max(np.abs(indata)))
    
    # Solo procesamos si el sonido supera el umbral de silencio
    if peak > PICO_MINIMO:
        # Calcular FFT
        fft_vals = np.abs(np.fft.rfft(indata[:, 0]))
        fft_freqs = np.fft.rfftfreq(len(indata), 1.0 / SAMPLE_RATE)
        
        # Bandas de frecuencia:
        # Voz humana: casi toda su energía por debajo de 1200 Hz.
        # Aplauso (transitorio): alta energía en frecuencias medias/altas (2000 Hz a 10000 Hz).
        low_mask = (fft_freqs >= 100) & (fft_freqs <= 1200)
        high_mask = (fft_freqs >= 2000) & (fft_freqs <= 10000)
        
        low_energy = np.sum(fft_vals[low_mask] ** 2)
        high_energy = np.sum(fft_vals[high_mask] ** 2)
        
        ratio = high_energy / (low_energy + 1e-6)
        
        # Etiqueta orientativa para ayudar al usuario a ver qué detecta
        tipo = "POSIBLE VOZ / RUIDO" if ratio < 0.8 else "POSIBLE APLAUSO / SNAP"
        
        print(f"🔊 Sonido: Pico={peak:.3f} | Energía Baja={low_energy:.4f} | Energía Alta={high_energy:.4f} | Ratio={ratio:.3f} -> {tipo}")

try:
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        channels=1,
        dtype="float32",
        callback=audio_callback
    ):
        while True:
            time.sleep(0.1)
except KeyboardInterrupt:
    print("\nPrueba terminada. ¡Hasta luego!")
