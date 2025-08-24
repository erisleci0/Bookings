from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import mysql.connector
import os
from dotenv import load_dotenv
import os

load_dotenv()

DB_HOST = os.environ.get("DB_HOST")
DB_USER = os.environ.get("DB_USER")
DB_PASS = os.environ.get("DB_PASS")
DB_NAME = os.environ.get("DB_NAME")
DB_PORT = os.environ.get("DB_PORT")

if not all([DB_HOST, DB_USER, DB_PASS, DB_NAME]):
    raise RuntimeError("Database environment variables not set!")


app = FastAPI()

ssl_path = os.path.join(os.path.dirname(__file__), "ca.pem")
if not os.path.isfile(ssl_path):
    raise RuntimeError(f"CA certificate not found at {ssl_path}")


# DB connection (adjust credentials)
def get_db():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        port=DB_PORT,
        ssl_ca=ssl_path,          # if Aiven provides a CA certificate, use it
        ssl_disabled=False
    )

# Request models
class BookingRequest(BaseModel):
    user_id: int
    check_in: str   # YYYY-MM-DD
    check_out: str  # YYYY-MM-DD

class CancelRequest(BaseModel):
    booking_id: int

class FreeRoomsRequest(BaseModel):
    check_in: str   # YYYY-MM-DD
    check_out: str  # YYYY-MM-DD

# Response model for rooms
class Room(BaseModel):
    id: int
    room_number: str
    type: str
    capacity: int
    price_per_night: float

class Booking(BaseModel):
    id: int
    user_id: int
    room_id: int
    check_in: str
    check_out: str
    status: Optional[str] = "booked"
    notes: Optional[str] = None

@app.post("/rooms/free", response_model=List[Room])
def get_free_rooms(req: FreeRoomsRequest):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    # SQL to find rooms NOT booked in the given date range
    sql = """
    SELECT * FROM rooms
    WHERE id NOT IN (
        SELECT room_id FROM bookings    
        WHERE NOT (check_out <= %s OR check_in >= %s)
    )
    """
    # Dates for overlap check
    cursor.execute(sql, (req.check_in, req.check_out))
    free_rooms = cursor.fetchall()

    cursor.close()
    db.close()

    if not free_rooms:
        raise HTTPException(status_code=404, detail="No rooms available for these dates")

    for r in free_rooms:
        r["check_in"] = r.get("check_in").strftime("%Y-%m-%d") if r.get("check_in") else None
        r["check_out"] = r.get("check_out").strftime("%Y-%m-%d") if r.get("check_out") else None

    return free_rooms

# Book a room
@app.post("/book")
def book_room(req: BookingRequest):
    db = get_db()
    cursor = db.cursor()
    sql = "INSERT INTO bookings (user_id, check_in, check_out) VALUES (%s, %s, %s)"
    values = (req.user_id, req.check_in, req.check_out)
    cursor.execute(sql, values)
    db.commit()
    booking_id = cursor.lastrowid
    cursor.close()
    db.close()
    return {"message": "Booking successful", "booking_id": booking_id}

@app.get("/bookings", response_model=List[Booking])
def get_bookings():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    sql = "SELECT id, user_id, room_id, check_in, check_out, status, notes FROM bookings"
    cursor.execute(sql)
    bookings = cursor.fetchall()

    cursor.close()
    db.close()

    if not bookings:
        raise HTTPException(status_code=404, detail="No bookings found")
    
    # Convert datetime.date to string
    for b in bookings:
        b["check_in"] = b["check_in"].strftime("%Y-%m-%d")
        b["check_out"] = b["check_out"].strftime("%Y-%m-%d")
    
    return bookings

# Cancel a booking
@app.delete("/cancel")
def cancel_booking(req: CancelRequest):
    db = get_db()
    cursor = db.cursor()
    sql = "DELETE FROM bookings WHERE id = %s"
    cursor.execute(sql, (req.booking_id,))
    db.commit()
    cursor.close()
    db.close()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Booking not found")
    return {"message": "Booking canceled"}
