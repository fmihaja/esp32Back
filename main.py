from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pymysql
from pymysql.cursors import DictCursor
from contextlib import contextmanager
from typing import List, Optional
from typing import Generic, TypeVar
from pydantic.generics import GenericModel
# Configuration de la base de données
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',  # Remplacez par votre nom d'utilisateur MySQL
    'password': '',  # Remplacez par votre mot de passe
    'database': 'esp32',  # Nom de votre base de données
    'charset': 'utf8mb4',
    'cursorclass': DictCursor
}

app = FastAPI()
T = TypeVar("T")

# Modèles Pydantic
class Device(BaseModel):
    id:Optional[int]
    status: bool  # True pour allumé, False pour éteint


class DeviceResponse(BaseModel):
    id: int
    status: bool

class ApiResponse(GenericModel, Generic[T]):
    data: T
    message: str


# Gestionnaire de connexion à la base de données
@contextmanager
def get_db_connection():
    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)
        yield connection
    except pymysql.Error as e:
        print(f"Erreur MySQL: {e}")
        if connection:
            connection.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur de base de données: {str(e)}")
    except Exception as e:
        print(f"Erreur générale: {e}")
        if connection:
            connection.rollback()
        raise HTTPException(status_code=500, detail="Erreur serveur interne")
    finally:
        if connection:
            connection.close()

@app.get("/")
def read_root():
    return {"message": "API ESP32 device Controller"}

@app.get("/health")
def health_check():
    """Vérifier la connexion à la base de données"""
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
    """Créer un nouveau statut de device"""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO device (status) VALUES (%s)",
                (device.status,)
            )
            connection.commit()
            
            # Récupérer l'enregistrement créé
            device_id = cursor.lastrowid
            cursor.execute("SELECT id, status FROM device WHERE id = %s", (device_id,))
            device_data = cursor.fetchone()
            response=DeviceResponse(
                id=device_data['id'], # type: ignore
                status=bool(device_data['status']) # type: ignore
            )
            
            return ApiResponse(
                data=response,
                message="Creation fait"
            ) 

@app.get("/device/", response_model=ApiResponse[List[DeviceResponse]])
def get_all_device_status(skip: int = 0, limit: int = 100):
    """Récupérer tous les statuts de device avec pagination"""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, status FROM device ORDER BY id ASC LIMIT %s OFFSET %s", 
                (limit, skip)
            )
            device_data = cursor.fetchall()
            if not device_data:
                raise HTTPException(status_code=404, detail="device non trouvé")
            response= [
                DeviceResponse(
                    id=device['id'], # type: ignore
                    status=bool(device['status']) # type: ignore
                ) for device in device_data
            ]
            
            return ApiResponse(
                data=response,
                message="Liste des lampes"
            ) 

@app.get("/device/{device_id}", response_model=ApiResponse[DeviceResponse])
def get_device_status(device_id: int):
    """Récupérer le statut d'une device par ID"""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, status FROM device WHERE id = %s", (device_id,))
            device_data = cursor.fetchone()
            
            if not device_data:
                raise HTTPException(status_code=404, detail="device non trouvée")
            response= DeviceResponse(
                id=device_data['id'], # type: ignore
                status=bool(device_data['status']) # type: ignore
            )
            return ApiResponse(
                data=response,
                message="Lampe numero " + str(response.id)
            ) 
        
# @app.get("/device/latest/", response_model=ApiResponse[DeviceResponse])
# def get_latest_device_status():
#     """Récupérer le dernier statut de device"""
#     with get_db_connection() as connection:
#         with connection.cursor() as cursor:
#             cursor.execute("SELECT id, status FROM device ORDER BY id DESC LIMIT 1")
#             device_data = cursor.fetchone()
            
#             if not device_data:
#                 raise HTTPException(status_code=404, detail="Aucun statut de device trouvé")
                
#             return DeviceResponse(
#                 id=device_data['id'], # type: ignore
#                 status=bool(device_data['status']) # type: ignore
#             )

@app.put("/device/{device_id}", response_model=ApiResponse[DeviceResponse])
def update_device_status(device: Device):
    """Mettre à jour le statut d'une device"""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            # Vérifier si la device existe
            cursor.execute("SELECT id FROM device WHERE id = %s", (device.id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="device non trouvée")
            
            if device.status is None:
                raise HTTPException(status_code=400, detail="Le statut doit être fourni")
            
            cursor.execute(
                "UPDATE device SET status = %s WHERE id = %s",
                (device.status, device.id)
            )
            connection.commit()
            
            # Récupérer la device mise à jour
            cursor.execute("SELECT id, status FROM device WHERE id = %s", (device.id,))
            device_data = cursor.fetchone()
            response=DeviceResponse(
                id=device_data['id'], # type: ignore
                status=bool(device_data['status']) # type: ignore
            )
            
            return ApiResponse(
                data=response,
                message="Lampe numero " + str(response.id)+ "Modifier"
            ) 

@app.delete("/device/{device_id}")
def delete_device_status(device_id: int):
    """Supprimer un statut de device"""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM device WHERE id = %s", (device_id,))
            
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="device non trouvée")
                
            connection.commit()
            return {"message": f"Statut device {device_id} supprimé avec succès"}

# Routes spécialisées pour contrôle ESP32
# @app.post("/device/on/", response_model=DeviceResponse)
# def turn_device_on():
#     """Allumer la device (créer un nouveau statut ON)"""
#     device = device(status=True)
#     return create_device_status(device)

# @app.post("/device/off/", response_model=DeviceResponse)
# def turn_device_off():
#     """Éteindre la device (créer un nouveau statut OFF)"""
#     device = device(status=False)
#     return create_device_status(device)

@app.post("/device/toggle/", response_model=DeviceResponse)
def toggle_device():
    """Basculer l'état de la device"""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            # Récupérer le dernier statut
            cursor.execute("SELECT status FROM device ORDER BY id DESC LIMIT 1")
            last_status = cursor.fetchone()
            
            # Inverser le statut (si aucun statut, commencer par True)
            new_status = not bool(last_status['status']) if last_status else True # type: ignore
            
            # Créer le nouveau statut
            cursor.execute("INSERT INTO device (status) VALUES (%s)", (new_status,))
            connection.commit()
            
            # Récupérer l'enregistrement créé
            device_id = cursor.lastrowid
            cursor.execute("SELECT id, status FROM device WHERE id = %s", (device_id,))
            device_data = cursor.fetchone()
            
            return DeviceResponse(
                id=device_data['id'], # type: ignore
                status=bool(device_data['status']) # type: ignore
            )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)