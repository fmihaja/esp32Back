# from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from contextlib import contextmanager
from typing import List, Optional, Generic, TypeVar
from pydantic.generics import GenericModel
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import face_recognition
import numpy as np
from PIL import Image
import io

# Configuration PostgreSQL (Koyeb)
DB_CONFIG = {
    'host': 'ep-super-darkness-a2u72vus.eu-central-1.pg.koyeb.app',
    'user': 'root-adm',
    'password': 'npg_hUoN63HfFyRe',
    'database': 'koyebdb',
    'port': 5432,
     'sslmode': 'require', 
    'cursor_factory': RealDictCursor
}

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_image_from_upload(upload_file: UploadFile):
    """Charge une image depuis un fichier uploadé"""
    try:
        image_data = upload_file.file.read()
        image = Image.open(io.BytesIO(image_data))
        # Convertir en RGB si nécessaire
        if image.mode != 'RGB':
            image = image.convert('RGB')
        return np.array(image)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erreur lors du chargement de l'image: {str(e)}")

def get_face_encoding(image_array):
    """Extrait l'encodage facial d'une image"""
    face_locations = face_recognition.face_locations(image_array)
    
    if len(face_locations) == 0:
        raise HTTPException(status_code=400, detail="Aucun visage détecté dans l'image")
    
    # Si plusieurs visages, utiliser le plus grand (probablement le visage principal)
    if len(face_locations) > 1:
        # Calculer la taille de chaque visage et prendre le plus grand
        face_sizes = [(loc[2] - loc[0]) * (loc[1] - loc[3]) for loc in face_locations]
        largest_face_index = face_sizes.index(max(face_sizes))
        face_locations = [face_locations[largest_face_index]]
    
    face_encodings = face_recognition.face_encodings(image_array, face_locations)
    return face_encodings[0]

T = TypeVar("T")

# Modèles Pydantic
class Device(BaseModel):
    id: Optional[int]
    status: bool  # True pour allumé, False pour éteint
    name: str

class DeviceResponse(BaseModel):
    id: int
    status: bool
    name: str

class FaceMatchResponse(BaseModel):
    status: bool             # True = correspondance, False = pas de correspondance
    message: str             # Message descriptif

class ApiResponse(GenericModel, Generic[T]):
    data: T
    message: str

# Gestionnaire de connexion PostgreSQL
@contextmanager
def get_db_connection():
    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        yield conn
    except psycopg2.Error as e:
        print(f"Erreur PostgreSQL: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur de base de données: {str(e)}")
    except Exception as e:
        print(f"Erreur générale: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail="Erreur serveur interne")
    finally:
        if conn:
            conn.close()

# Routes (inchangées)
@app.get("/")
def read_root():
    return {"message": "API ESP32 device Controller"}

@app.get("/health")
def health_check():
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 as test")
                result = cursor.fetchone()
                return {
                    "status": "healthy",
                    "database": "connected",
                    "test_query": result
                }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

@app.post("/device/", response_model=ApiResponse[DeviceResponse])
def create_device_status(device: Device):
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO device (status) VALUES (%s) RETURNING id",
                (device.status,)
            )
            device_id = cursor.fetchone()['id']
            connection.commit()

            cursor.execute("SELECT id, status, name FROM device WHERE id = %s", (device_id,))
            device_data = cursor.fetchone()
            response = DeviceResponse(
                id=device_data['id'],
                status=bool(device_data['status']),
                name=device_data['name']
            )
            return ApiResponse(data=response, message="Creation fait")

@app.get("/device/", response_model=ApiResponse[List[DeviceResponse]])
def get_all_device_status(skip: int = 0, limit: int = 100):
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, status, name FROM device ORDER BY id ASC LIMIT %s OFFSET %s",
                (limit, skip)
            )
            device_data = cursor.fetchall()
            if not device_data:
                raise HTTPException(status_code=404, detail="device non trouvé")
            response = [
                
                DeviceResponse(id=device['id'], status=bool(device['status']), name=device['name']) for device in device_data
            ]
            return ApiResponse(data=response, message="Liste des lampes")

@app.get("/device/{device_id}", response_model=ApiResponse[DeviceResponse])
def get_device_status(device_id: int):
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, status, name FROM device WHERE id = %s", (device_id,))
            device_data = cursor.fetchone()
            if not device_data:
                raise HTTPException(status_code=404, detail="device non trouvée")
            response = DeviceResponse(id=device_data['id'], status=bool(device_data['status']), name=device_data['name'])
            return ApiResponse(data=response, message="Lampe numero " + str(response.id))

@app.put("/device/{device_id}", response_model=ApiResponse[DeviceResponse])
def update_device_status(device: Device):
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM device WHERE id = %s", (device.id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="device non trouvée")

            cursor.execute("UPDATE device SET status = %s WHERE id = %s RETURNING id, status, name",
                           (device.status, device.id))
            device_data = cursor.fetchone()
            connection.commit()

            response = DeviceResponse(id=device_data['id'], status=bool(device_data['status']), name=device_data['name'])
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
        with connection.cursor() as cursor:
            cursor.execute("SELECT status FROM device ORDER BY id DESC LIMIT 1")
            last_status = cursor.fetchone()
            new_status = not bool(last_status['status']) if last_status else True

            cursor.execute("INSERT INTO device (status) VALUES (%s) RETURNING id, name", (new_status,))
            device_id = cursor.fetchone()['id']
            connection.commit()

            cursor.execute("SELECT id, status, name FROM device WHERE id = %s", (device_id,))
            device_data = cursor.fetchone()

            return DeviceResponse(id=device_data['id'], status=bool(device_data['status']), name=device_data['name'])
        
@app.post("/compare-faces", response_model=FaceMatchResponse)
async def compare_faces(
    camera_image: UploadFile = File(..., description="Image capturée par la caméra"),
    stored_image: UploadFile = File(..., description="Image stockée sur l'appareil")
):
    """
    Compare deux images de visages et retourne la correspondance.
    
    Args:
        camera_image: Image capturée en temps réel par la caméra
        stored_image: Image stockée dans l'appareil (chemin depuis SQLite)
    
    Returns:
        FaceMatchResponse: Résultat de la comparaison
    """
    try:
        # Charger les deux images
        camera_array = load_image_from_upload(camera_image)
        stored_array = load_image_from_upload(stored_image)
        
        # Extraire les encodages faciaux
        camera_encoding = get_face_encoding(camera_array)
        stored_encoding = get_face_encoding(stored_array)
        
        # Comparer les visages
        is_match = face_recognition.compare_faces([stored_encoding], camera_encoding, tolerance=0.6)[0]
        
        return FaceMatchResponse(
            status=bool(is_match),
            message="Visages correspondants !" if is_match else "Visages différents"
        )
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la comparaison: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)