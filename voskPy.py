from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import vosk
import wave
import json
import tempfile
import os
import logging
from pydub import AudioSegment  # ✅ AJOUTER CETTE LIGNE
import urllib3
import requests


APP_CONTEXT = """
Mon application s'appelle Tranon'AI c'est une application mobile multi-platform.

Fonctionnalités principales :
- Fonction 1 : allumer ou eteindre un appareille
- Fonction 2 : utilisation de camera pour voir une intrusion qui enregistre la photo des inconnus du foyer
- Fonction 3 : detection de gaz, fumee et temperature a laquelle vous etes notifie par sms
- Fonction 4 : voir la consommation en temps reel et payer la facture du JIRAMA

Comment utiliser :
- Pour allumer une appareil, il faut aller dans la page energie puis clique sur l'onglet Appareils et choisir l'appareil disponible qui a deux status allumer ou eteindre
- Pour voir l'etat du capteurs, il faut aller dans la page sécurité puis aller dans l'onglet capteurs pour voir l'etat
- Pour payer un facture il faut cliquer le card statistique dans la page Energie sur l'onglet aperçu


Commandes vocales supportées :
- "allumer lampe 1" : ouvre allumer la lampe 1
- "aide" : affiche l'aide
- etc.
"""

conversation_history = [
    {
        "role": "system",
        "content": f"""Tu es l'assistant vocal de l'application mobile suivante.
🚫 INTERDICTIONS TOTALES :
- JAMAIS d'astérisques *, de tirets -, de puces •, de hashtags #
RÈGLES ABSOLUES :
1. JAMAIS de markdown, formatage, puces, astérisques, hashtags
2. Si la question ne concerne pas l'application, dis poliment que tu ne peux répondre qu'aux questions sur l'app
3. Réponds UNIQUEMENT aux questions concernant l'application Tranon'AI
4. Parle comme si tu parlais à l'oral, naturellement
5. Évite les listes à puces, donne des phrases fluides



Informations sur l'application :
{APP_CONTEXT}

Réponds de manière concise et claire."""
    }
]

def call_ollama_chat_mistral(prompt):
    url = "http://localhost:11434/api/chat"
    headers = {"Content-Type": "application/json"}
    
    # Ajouter la question de l'utilisateur à l'historique
    conversation_history.append({"role": "user", "content": prompt})
#    phi3:mini 
# gemma2:2b
    payload = {
        "model": "gemma2:2b",
        "messages": conversation_history,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "top_p": 0.9
        }
    }

    try:
        response = requests.post(url, json=payload, headers=headers, verify=False)
        response.raise_for_status()
        data = response.json()

        if "message" in data and "content" in data["message"]:
            content = data["message"]["content"]
            conversation_history.append({"role": "assistant", "content": content})
            
            # ✅ AJOUT IMPORTANT : Ajo
            print("Ollama response:", content)
            return content
        else:
            print("Unexpected response format:", data)
            return None
    except requests.RequestException as e:
        print("Error communicating with endpoint:", e)
        return None
    except json.JSONDecodeError as e:
        print("Error parsing JSON response:", e)
        return None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Charger le modèle Vosk
model_path = "vosk-model-fr-0.22"
try:
    model = vosk.Model(model_path)
    logger.info(f"Modèle Vosk chargé depuis {model_path}")
except Exception as e:
    logger.error(f"Erreur lors du chargement du modèle: {e}")
    model = None

@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    if model is None:
        raise HTTPException(status_code=500, detail="Modèle Vosk non chargé")
    
    logger.info(f"📥 Fichier reçu: {file.filename}, type: {file.content_type}")
    
    # Sauvegarder le fichier uploadé
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as temp_input:
        content = await file.read()
        temp_input.write(content)
        temp_input.flush()
        temp_input_path = temp_input.name
    
    logger.info(f"📁 Fichier temporaire créé: {temp_input_path}, taille: {len(content)} bytes")
    
    # Vérifier les premiers octets
    with open(temp_input_path, 'rb') as f:
        header = f.read(12)
        logger.info(f"🔍 En-tête fichier: {header[:4]}")
    
    temp_audio_path = None
    try:
        # Convertir en WAV valide avec pydub
        logger.info("🔄 Conversion en WAV...")
        audio = AudioSegment.from_file(temp_input_path)
        
        # Convertir en mono, 16000 Hz, 16-bit
        audio = audio.set_channels(1)
        audio = audio.set_frame_rate(16000)
        audio = audio.set_sample_width(2)  # 16-bit = 2 bytes
        
        # Sauvegarder en WAV
        temp_audio_path = temp_input_path.replace(".tmp", ".wav")
        audio.export(temp_audio_path, format="wav")
        logger.info(f"✅ Fichier WAV créé: {temp_audio_path}")
        
        # Transcription avec Vosk
        with wave.open(temp_audio_path, "rb") as wav_file:
            logger.info(f"🎵 Audio: {wav_file.getnchannels()} canaux, {wav_file.getframerate()} Hz, {wav_file.getsampwidth()*8} bit")
            
            if wav_file.getnchannels() != 1:
                raise HTTPException(status_code=400, detail="Le fichier audio doit être mono")
            if wav_file.getsampwidth() != 2:
                raise HTTPException(status_code=400, detail="Le fichier audio doit être 16-bit PCM")

            rec = vosk.KaldiRecognizer(model, wav_file.getframerate())
            rec.SetWords(True)
            results = []

            while True:
                data = wav_file.readframes(4000)
                if len(data) == 0:
                    break
                if rec.AcceptWaveform(data):
                    res = json.loads(rec.Result())
                    if res.get("text"):
                        results.append(res)

            final_res = json.loads(rec.FinalResult())
            if final_res.get("text"):
                results.append(final_res)

            text = " ".join([r.get("text", "") for r in results if r.get("text")])
            response=call_ollama_chat_mistral(text)
            logger.info(f"📝 Transcription: {response}")
            return {"text": response, "confidence": 1.0} # type: ignore

    except Exception as e:
        logger.error(f"❌ Erreur: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erreur de traitement: {str(e)}")
    
    finally:
        # Nettoyer les fichiers temporaires
        if os.path.exists(temp_input_path):
            os.unlink(temp_input_path)
        if temp_audio_path and os.path.exists(temp_audio_path):
            os.unlink(temp_audio_path)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "model_loaded": model is not None, "model_path": model_path}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")