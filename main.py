from fastapi import FastAPI, HTTPException, Request
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


class UserRequest(BaseModel):
    name: str
    email: str

class CheckRoomsRequest(BaseModel):
    check_in: str
    check_out: str


@app.post("/users")
def create_user(req: UserRequest):
    db = get_db()
    cursor = db.cursor()

    try:
        sql = "INSERT INTO users (name, email) VALUES (%s, %s, %s)"
        values = (req.name, req.email)
        cursor.execute(sql, values)
        db.commit()
        user_id = cursor.lastrowid
    except mysql.connector.Error as e:
        db.rollback()
        cursor.close()
        db.close()
        raise HTTPException(status_code=400, detail=str(e))

    cursor.close()
    db.close()

    return {"message": "User created successfully", "user_id": user_id}

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


@app.post("/confirm_booking")
async def confirm_booking(request: Request):
    body = await request.json()
    tool_call_id = "054e139e-e781-494a-b56a-926f5c05506f"
    params = body.get("parameters", {})

    name = params.get("name")
    email = params.get("email")
    check_in = params.get("check_in")
    check_out = params.get("check_out")
    room_number = params.get("room_number")  # fixed key

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # 1. Insert or get existing user
    cursor.execute("SELECT id FROM users WHERE email=%s", (email,))
    row = cursor.fetchone()
    if row:
        user_id = row["id"]
    else:
        cursor.execute(
            "INSERT INTO users (name, email) VALUES (%s, %s)",
            (name, email)
        )
        db.commit()
        user_id = cursor.lastrowid

    # 2. Look up the room ID
    cursor.execute("SELECT id FROM rooms WHERE room_number = %s", (room_number,))
    room = cursor.fetchone()
    if not room:
        raise HTTPException(status_code=400, detail=f"Room {room_number} does not exist")
    room_id = room["id"]

    # 3. Insert the booking
    cursor.execute(
        "INSERT INTO bookings (user_id, room_id, check_in, check_out) VALUES (%s, %s, %s, %s)",
        (user_id, room_id, check_in, check_out)
    )
    db.commit()
    booking_id = cursor.lastrowid

    # 4. Update the room status to 'booked'
    cursor.execute(
        "UPDATE rooms SET status = 'booked' WHERE id = %s",
        (room_id,)
    )
    db.commit()

    cursor.close()
    db.close()

    return {
        "results": [
            {
                "toolCallId": tool_call_id,
                "result": f"Booking confirmed for {name} (ID {booking_id}) from {check_in} to {check_out}"
            }
        ]
    }



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

# duhet qe permes VAPI-it me dergu ne request checkin edhe checkout e tani permes qatyne vlerave me kqyr qe ne mes ktyne datave a osht i e lire apo e nxanen ndonje banes

@app.post("/bookings")
async def get_bookings(req: CheckRoomsRequest):
    tool_call_id = "054e139e-e781-494a-b56a-926f5c05506f"

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Get all rooms and their status
    cursor.execute("SELECT room_number, type, status FROM rooms")
    all_rooms = cursor.fetchall()

    # Separate occupied and free rooms
    booked = [str(r["room_number"]) for r in all_rooms if r["status"].lower() == "booked"]
    free = [str(r["room_number"]) for r in all_rooms if r["status"].lower() == "free"]

    # Build readable sentence
    parts = []
    if booked:
        parts.append(f"Room {', '.join(booked)} {'is' if len(booked)==1 else 'are'} booked")
    if free:
        parts.append(f"room {', '.join(free)} {'is' if len(free)==1 else 'are'} free")

    result_text = " and ".join(parts) + "."

    cursor.close()
    db.close()

    # ✅ Return in VAPI format
    return {
        "results": [
            {
                "toolCallId": tool_call_id,
                "result": result_text
            }
        ]
    }


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
