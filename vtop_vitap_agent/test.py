import numpy as np
import pyaudio
import whisper
from kokoro import KPipeline
from langchain_ollama import ChatOllama

# 1. Run Whisper & Kokoro on CPU to keep GPU clear for Ollama
whisper_model = whisper.load_model("turbo", device="cpu")
tts_pipeline = KPipeline(lang_code="a", device="cpu")

# 2. Configure Ollama (set num_gpu=0 if your GPU has <6GB VRAM)
voice_llm = ChatOllama(
    model="llama3.1:8b",
    temperature=0.8,
)

# 3. Audio Recording Settings
RATE = 16000
CHUNK = 1024
RECORD_SECONDS = 5

p = pyaudio.PyAudio()
input_stream = p.open(
    format=pyaudio.paInt16,
    channels=1,
    rate=RATE,
    input=True,
    frames_per_buffer=CHUNK,
)

print("\n[Listening] Speak now...")
frames = []
for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
    data = input_stream.read(CHUNK)
    frames.append(data)
print("[Done] Processing speech...")

input_stream.stop_stream()
input_stream.close()

# 4. Transcribe using Whisper
audio_np = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0
audio = whisper.pad_or_trim(audio_np)

transcription = whisper_model.transcribe(audio, fp16=False)
user_text = transcription["text"].strip()

print(f"\nUser: {user_text}")

if user_text:
    # 5. Generate LLM response
    response = voice_llm.invoke(user_text)
    print(f"\nAI: {response.content}\n")

    # 6. Stream speech directly through speaker
    output_stream = p.open(
        format=pyaudio.paFloat32,
        channels=1,
        rate=24000,
        output=True,
    )

    print("[Speaking]...")
    generator = tts_pipeline(response.content, voice="af_heart")
    for i, (gs, ps, audio_segment) in enumerate(generator):
        if hasattr(audio_segment, "detach"):
            audio_data = audio_segment.detach().cpu().numpy()
        else:
            audio_data = np.asarray(audio_segment)
        output_stream.write(audio_data.astype(np.float32).tobytes())

    output_stream.stop_stream()
    output_stream.close()


else:
    print("No speech detected.")

p.terminate()
