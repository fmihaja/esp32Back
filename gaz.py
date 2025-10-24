from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import uvicorn
import logging

app = FastAPI(title="MQ135 Monitor API")
logger = logging.getLogger(__name__)

# 🔓 CORS — pour permettre les requêtes depuis ton app mobile
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tu peux restreindre ici plus tard
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class MQ135Data(BaseModel):
    valeur: int

# 🧠 Stockage de la dernière valeur reçue
valeur_actuelle = {
    "valeur": 0,
    "timestamp": None,
    "status": "En attente des données..."
}

@app.post("/data")
async def recevoir_valeur_mq135(data: MQ135Data):
    """Reçoit la valeur exacte du moniteur série ESP32"""
    global valeur_actuelle
    
    valeur_actuelle = {
        "valeur": data.valeur,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "status": "Données reçues"
    }

    print(f"[{valeur_actuelle['timestamp']}] Valeur MQ135: {data.valeur}")
    
    return {
        "data": {
            "valeur": data.valeur,
            "timestamp": valeur_actuelle['timestamp']
        },
        "message": "Valeur reçue avec succès"
    }

@app.get("/data")
async def obtenir_valeur_mq135():
    """Retourne la dernière valeur reçue pour l'app mobile"""
    return {
        "data": {
            "valeur": valeur_actuelle['valeur'],
            "timestamp": valeur_actuelle['timestamp']
        },
        "message": valeur_actuelle['status']
    }

@app.get("/")
async def root():
    """Page d'accueil API"""
    return {
        "data": {
            "valeur": valeur_actuelle['valeur'],
            "timestamp": valeur_actuelle['timestamp']
        },
        "message": "API MQ135 - Synchronisation Moniteur Série"
    }

# ✅ Lancement du serveur FastAPI
if __name__ == "__main__":
    print("🚀 Démarrage API MQ135...")
    print("📱 App Mobile: http://10.193.91.85:8000/data")
    print("📊 Docs API: http://10.193.91.85:8000/docs")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)
