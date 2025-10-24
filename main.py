from fastapi import FastAPI, HTTPException, File, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from contextlib import contextmanager
from typing import List, Optional, Generic, TypeVar
from pydantic.generics import GenericModel
import mysql.connector
from mysql.connector import Error
from fastapi.middleware.cors import CORSMiddleware
import face_recognition
import numpy as np
from PIL import Image, ImageOps
import io
import logging
import json
from datetime import datetime

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration MySQL (localhost)
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'esp32',
    'port': 3306
}

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Modèles Pydantic
class Device(BaseModel):
    id: Optional[int]
    status: bool
    name: str

class DeviceResponse(BaseModel):
    id: int
    status: bool
    name: str

class FaceMatchResponse(BaseModel):
    status: bool
    message: str

# 🔥 Modèle pour le détecteur de gaz
class GasData(BaseModel):
    value: int

T = TypeVar("T")
class ApiResponse(GenericModel, Generic[T]):
    data: T
    message: str

# 🔌 Gestionnaire WebSocket pour le gaz
class GasConnectionManager:
    def __init__(self):
        self.active_connections = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"📱 Client gaz connecté. Total: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"📱 Client gaz déconnecté. Total: {len(self.active_connections)}")
    
    async def broadcast(self, message: str):
        disconnected_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"❌ Erreur envoi WebSocket: {e}")
                disconnected_connections.append(connection)
        
        for connection in disconnected_connections:
            self.disconnect(connection)

# Initialisation du manager
gas_manager = GasConnectionManager()

# Stockage de la valeur du gaz
current_gas_value = 0

# Gestionnaire de connexion MySQL
@contextmanager
def get_db_connection():
    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        yield conn
    except Error as e:
        logger.error(f"Erreur MySQL: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur de base de données: {str(e)}")
    except Exception as e:
        logger.error(f"Erreur générale: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail="Erreur serveur interne")
    finally:
        if conn and conn.is_connected():
            conn.close()

# Fonctions pour la reconnaissance faciale (inchangées)
def load_and_preprocess_image(file: UploadFile):
    """Charge et prétraite une image pour améliorer la détection faciale"""
    try:
        image_data = file.file.read()
        image = Image.open(io.BytesIO(image_data))
        image = ImageOps.exif_transpose(image)
        
        if image.mode != 'RGB':
            image = image.convert('RGB')
            
        max_size = 1000
        if image.size[0] > max_size or image.size[1] > max_size:
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            
        return np.array(image)
        
    except Exception as e:
        logger.error(f"Erreur lors du chargement de l'image: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur lors du chargement de l'image: {str(e)}")

def get_face_encoding_improved(image_array):
    """Extrait l'encodage facial avec le modèle HOG uniquement"""
    try:
        # Une seule tentative avec le modèle par défaut (HOG)
        face_locations = face_recognition.face_locations(image_array)
        
        if len(face_locations) == 0:
            raise HTTPException(
                status_code=400, 
                detail="Aucun visage détecté dans l'image. Assurez-vous que le visage est bien visible et éclairé."
            )
        
        # Si plusieurs visages, prendre le plus grand
        if len(face_locations) > 1:
            face_sizes = [(bottom - top) * (right - left) for top, right, bottom, left in face_locations]
            largest_face_index = face_sizes.index(max(face_sizes))
            face_locations = [face_locations[largest_face_index]]
            logger.info(f"Multiple visages détectés, utilisation du plus grand (index {largest_face_index})")
        
        # Extraction de l'encodage facial
        face_encodings = face_recognition.face_encodings(image_array, face_locations)
        
        if len(face_encodings) == 0:
            raise HTTPException(
                status_code=400, 
                detail="Impossible d'extraire les caractéristiques du visage"
            )
        
        logger.info(f"Visage détecté avec succès, localisation: {face_locations[0]}")
        return face_encodings[0]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction faciale: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erreur de traitement facial: {str(e)}")
# Fonction pour déterminer l'état du gaz
def get_gas_status(value: int) -> str:
    if value < 200:
        return "Normal"
    elif value < 300:
        return "Attention"
    else:
        return "Danger"

# ==================== ROUTES EXISTANTES (inchangées) ====================

@app.get("/")
def read_root():
    return {"message": "API ESP32 device Controller avec détecteur de gaz"}

@app.get("/health")
def health_check():
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT 1 as test")
                result = cursor.fetchone()
                return {
                    "status": "healthy",
                    "database": "connected",
                    "test_query": result,
                    "gas_value": current_gas_value
                }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

@app.post("/device/", response_model=ApiResponse[DeviceResponse])
def create_device_status(device: Device):
    with get_db_connection() as connection:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute(
                "INSERT INTO device (status, name) VALUES (%s, %s)",
                (device.status, device.name)
            )
            device_id = cursor.lastrowid
            connection.commit()

            cursor.execute("SELECT id, status, name FROM device WHERE id = %s", (device_id,))
            device_data = cursor.fetchone()
            response = DeviceResponse(
                id=device_data['id'], # type: ignore
                status=bool(device_data['status']), # type: ignore
                name=device_data['name'] # type: ignore
            )
            return ApiResponse(data=response, message="Creation fait")

@app.get("/device/", response_model=ApiResponse[List[DeviceResponse]])
def get_all_device_status(skip: int = 0, limit: int = 100):
    with get_db_connection() as connection:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT id, status, name FROM device ORDER BY id ASC LIMIT %s OFFSET %s",
                (limit, skip)
            )
            device_data = cursor.fetchall()
            if not device_data:
                raise HTTPException(status_code=404, detail="device non trouvé")
            response = [
                DeviceResponse(id=device['id'], status=bool(device['status']), name=device['name']) for device in device_data # type: ignore
            ]
            return ApiResponse(data=response, message="Liste des lampes")

@app.get("/device/{device_id}", response_model=ApiResponse[DeviceResponse])
def get_device_status(device_id: int):
    with get_db_connection() as connection:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT id, status, name FROM device WHERE id = %s", (device_id,))
            device_data = cursor.fetchone()
            if not device_data:
                raise HTTPException(status_code=404, detail="device non trouvée")
            response = DeviceResponse(id=device_data['id'], status=bool(device_data['status']), name=device_data['name']) # type: ignore
            return ApiResponse(data=response, message="Lampe numero " + str(response.id))

@app.put("/device/{device_id}", response_model=ApiResponse[DeviceResponse])
def update_device_status(device: Device):
    with get_db_connection() as connection:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT id FROM device WHERE id = %s", (device.id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="device non trouvée")

            cursor.execute("UPDATE device SET status = %s, name = %s WHERE id = %s",
                           (device.status, device.name, device.id))
            connection.commit()

            cursor.execute("SELECT id, status, name FROM device WHERE id = %s", (device.id,))
            device_data = cursor.fetchone()
            response = DeviceResponse(id=device_data['id'], status=bool(device_data['status']), name=device_data['name']) # type: ignore
            return ApiResponse(data=response, message="Lampe numero " + str(response.id) + " Modifier")

@app.delete("/device/{device_id}")
def delete_device_status(device_id: int):
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM device WHERE id = %s", (device_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="device non trouvée")
            connection.commit()
            return {"message": f"Statut device {device_id} supprimé avec succès"}

@app.post("/device/toggle/", response_model=DeviceResponse)
def toggle_device():
    with get_db_connection() as connection:
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT status FROM device ORDER BY id DESC LIMIT 1")
            last_status = cursor.fetchone()
            new_status = not bool(last_status['status']) if last_status else True # type: ignore

            cursor.execute("INSERT INTO device (status, name) VALUES (%s, %s)", (new_status, "Device"))
            device_id = cursor.lastrowid
            connection.commit()

            cursor.execute("SELECT id, status, name FROM device WHERE id = %s", (device_id,))
            device_data = cursor.fetchone()

            return DeviceResponse(id=device_data['id'], status=bool(device_data['status']), name=device_data['name']) # type: ignore

# Routes pour la reconnaissance faciale
@app.post("/compare-faces", response_model=ApiResponse[FaceMatchResponse])
async def compare_faces_files(
    camera_image: UploadFile = File(...),
    stored_image: UploadFile = File(...)
):
    """
    Compare deux images pour la reconnaissance faciale
    """
    try:
        logger.info("📥 Réception des fichiers pour comparaison faciale...")

        if not camera_image.content_type or not camera_image.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="Le fichier camera_image doit être une image")
        
        if not stored_image.content_type or not stored_image.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="Le fichier stored_image doit être une image")

        img1 = load_and_preprocess_image(camera_image)
        img2 = load_and_preprocess_image(stored_image)
        
        logger.info(f"✅ Images chargées - Camera shape: {img1.shape}, Stored shape: {img2.shape}")

        face1 = get_face_encoding_improved(img1)
        face2 = get_face_encoding_improved(img2)

        distance = face_recognition.face_distance([face1], face2)[0]
        match = distance < 0.5

        logger.info(f"🔍 Résultat comparaison - Distance: {distance:.4f}, Match: {match}")

        if match:
            response_data = FaceMatchResponse(
                status=True,
                message="Visage reconnu avec succès"
            )
        else:
            response_data = FaceMatchResponse(
                status=False,
                message="Aucune correspondance trouvée"
            )

        return ApiResponse(
            data=response_data,
            message="Comparaison terminée"
        )

    except HTTPException as e:
        logger.error(f"❌ Erreur HTTP: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"❌ Erreur générale: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erreur lors de la comparaison: {str(e)}")

@app.post("/detect-face")
async def detect_face_only(image: UploadFile = File(...)):
    """
    Route pour tester la détection faciale sur une seule image
    """
    try:
        logger.info("🔍 Test de détection faciale...")
        
        if not image.content_type or not image.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="Le fichier doit être une image")

        img = load_and_preprocess_image(image)
        logger.info(f"Image chargée - Shape: {img.shape}")
        
        face_locations = face_recognition.face_locations(img)
        
        if len(face_locations) == 0:
            face_locations = face_recognition.face_locations(img, model="cnn")
            
        if len(face_locations) == 0:
            face_locations = face_recognition.face_locations(img, number_of_times_to_upsample=2)
            
        return {
            "faces_detected": len(face_locations),
            "face_locations": face_locations,
            "image_size": img.shape,
            "message": f"{len(face_locations)} visage(s) détecté(s)" if face_locations else "Aucun visage détecté"
        }
        
    except Exception as e:
        logger.error(f"Erreur détection: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erreur de détection: {str(e)}")

# ==================== ROUTES DÉTECTEUR DE GAZ SIMPLIFIÉES ====================

@app.websocket("/ws/gas")
async def gas_websocket_endpoint(websocket: WebSocket):
    """WebSocket pour les données du détecteur de gaz en temps réel"""
    await gas_manager.connect(websocket)
    try:
        # Envoyer la valeur actuelle
        await websocket.send_text(json.dumps({"value": current_gas_value}))
        
        # Boucle pour gérer les messages (pings)
        while True:
            try:
                message = await websocket.receive_text()
                # Si c'est un ping, on répond par un pong (optionnel)
                if "ping" in message.lower():
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except Exception:
                break
                
    except WebSocketDisconnect:
        gas_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Erreur WebSocket gaz: {e}")
        gas_manager.disconnect(websocket)

@app.post("/gas-detector")
async def receive_gas_value(data: GasData):
    """Reçoit les données du détecteur de gaz"""
    global current_gas_value
    current_gas_value = data.value
    
    gas_status = get_gas_status(data.value)
    logger.info(f"🔥 [{datetime.now().strftime('%H:%M:%S')}] Valeur MQ135: {data.value} - État: {gas_status}")
    
    # Diffusion en temps réel à tous les clients WebSocket (seulement la valeur)
    await gas_manager.broadcast(json.dumps({
        "value": data.value
    }))
    
    return {
        "data": {
            "value": data.value
        },
        "message": gas_status
    }

@app.get("/gas-detector")
async def get_gas_value():
    """Retourne la dernière valeur du détecteur de gaz"""
    gas_status = get_gas_status(current_gas_value)
    return {
        "data": {
            "value": current_gas_value
        },
        "message": gas_status
    }

if __name__ == "__main__":
    import uvicorn
    logger.info("🚀 Démarrage API ESP32 complète...")
    logger.info("📱 WebSocket Gaz: ws://localhost:8000/ws/gas")
    logger.info("📊 Endpoints:")
    logger.info("   - POST/GET /gas-detector")
    logger.info("   - POST /compare-faces")
    logger.info("   - GET/POST/PUT/DELETE /device/*")
    logger.info("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)