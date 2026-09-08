def call_gemini(history, image_bytes=None, enable_search=False):
    """Ruft Gemini auf – optimiert für Bildanalysen und stabile Antworten."""
    try:
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=SYSTEM_PROMPT
        )
        
        if image_bytes:
            prompt_text = history[-1]["content"] if history else "Was ist auf diesem Bild zu sehen?"
            image_content = {
                "mime_type": "image/jpeg",
                "data": image_bytes
            }
            response = model.generate_content([prompt_text, image_content])
            return response.text, f"Gemini (Vision)"
        
        gemini_history = []
        for msg in history[:-1]:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})
            
        chat = model.start_chat(history=gemini_history)
        last_message = history[-1]["content"] if history else "Hallo"
        response = chat.send_message(last_message)
            
        return response.text, f"Gemini ({GEMINI_MODEL})"
    except Exception as e:
        error_msg = str(e)
        print(f"Gemini Detail-Fehler: {error_msg}", flush=True)
        return f"Gemini API Fehler: {error_msg}", "Gemini (Fehler)"
