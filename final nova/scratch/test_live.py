import asyncio
import json
from pathlib import Path
from google import genai
from google.genai import types

config_path = Path("config/api_keys.json")
with open(config_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

api_key = cfg["gemini_api_key"]
client = genai.Client(api_key=api_key, http_options={"api_version": "v1alpha"})

async def main():
    print("Connecting to Gemini Live...")
    live_config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
    )
    async with client.aio.live.connect(
        model="models/gemini-3.1-flash-live-preview",
        config=live_config
    ) as session:
        print("[OK] Connected to Gemini Live session!")
        
        # Send user turn
        prompt_text = "System check: Reply with 'Nova systems active' in one sentence."
        print(f"Sending prompt: {prompt_text}")
        await session.send_client_content(
            turns={"role": "user", "parts": [{"text": prompt_text}]},
            turn_complete=True
        )

        audio_bytes_received = 0
        transcript_parts = []
        async for response in session.receive():
            if response.data:
                audio_bytes_received += len(response.data)
            if response.server_content:
                sc = response.server_content
                if sc.output_transcription and sc.output_transcription.text:
                    transcript_parts.append(sc.output_transcription.text)
                if sc.turn_complete:
                    print("[OK] Server turn completed.")
                    break

        print(f"[OK] Total audio bytes received: {audio_bytes_received}")
        print(f"[OK] Response transcript: {' '.join(transcript_parts)}")
        assert audio_bytes_received > 0, "No audio response received from Gemini Live!"

asyncio.run(main())

