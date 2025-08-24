from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import mysql.connector

app = FastAPI()

# DB connection (adjust credentials)
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="root",
        database="bookings"
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
