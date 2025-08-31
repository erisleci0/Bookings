from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
import mysql.connector
import os
from dotenv import load_dotenv
import random
import string
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib


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
    book_code: str

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
    room_numbers = params.get("room_numbers", [])  # list of room numbers

    if not room_numbers:
        raise HTTPException(status_code=400, detail="At least one room must be provided")

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

    booking_ids = []

    # 2. Loop through rooms
    for room_number in room_numbers:
        cursor.execute("SELECT id FROM rooms WHERE room_number = %s", (room_number,))
        room = cursor.fetchone()
        if not room:
            raise HTTPException(status_code=400, detail=f"Room {room_number} does not exist")
        room_id = room["id"]

        book_code = generate_booking_code()

        cursor.execute(
            "INSERT INTO bookings (user_id, room_id, check_in, check_out, book_id) VALUES (%s, %s, %s, %s, %s)",
            (user_id, room_id, check_in, check_out, book_code)
        )
        
        db.commit()
        booking_id = cursor.lastrowid
        booking_ids.append({"id": booking_id, "book_code": book_code})

        # 4. Update the room status to 'booked'
        cursor.execute(
            "UPDATE rooms SET status = 'booked' WHERE id = %s",
            (room_id,)
        )
        db.commit()

    cursor.close()
    db.close()
    # After loop
    booking_info_for_email = []
    for room_number, b in zip(room_numbers, booking_ids):
        booking_info_for_email.append({"room_number": room_number, "book_code": b["book_code"]})
        
    email = "lecieris00@gmail.com"
    send_booking_email(email, name, booking_info_for_email)

    room_list = ", ".join(room_numbers)
    result_string = (
        f"Hi {name}, your booking for room{'s' if len(room_numbers) > 1 else ''} "
        f"{room_list} from {check_in} to {check_out} has been successfully confirmed. "
        f"We've also sent some additional details to your email."
)


    return {
        "results": [
            {
                "toolCallId": tool_call_id,
                "result": result_string
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
    cursor = db.cursor(dictionary=True)

    # Look up booking by book_code
    cursor.execute("SELECT id, user_id, room_id, check_in, check_out FROM bookings WHERE book_id = %s", (req.book_code,))
    booking = cursor.fetchone()

    if not booking:
        cursor.close()
        db.close()
        raise HTTPException(status_code=404, detail="Booking not found")

    # Delete booking
    cursor.execute("DELETE FROM bookings WHERE id = %s", (booking["id"],))
    db.commit()

    # Free up the room (optional, but usually expected)
    cursor.execute("UPDATE rooms SET status = 'free' WHERE id = %s", (booking["room_id"],))
    db.commit()

    cursor.close()
    db.close()

    # Build human-friendly response (similar to confirm_booking)
    result_string = (
        f"Your booking for room {booking['room_id']} "
        f"from {booking['check_in']} to {booking['check_out']} has been successfully canceled."
    )

    return {
        "results": [
            {
                "toolCallId": "cancel_001",  # or generate dynamically
                "result": result_string
            }
        ]
    }


def send_booking_email(to_email, name, bookings):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    import smtplib

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Booking Confirmation"
    msg["From"] = "lecieris0@gmail.com"
    msg["To"] = to_email

    # Build booking rows
    booking_rows = ""
    for b in bookings:
        booking_rows += f"""
        <tr style="background-color:#f9f9f9; text-align:center;">
            <td style="padding:10px;">{b['room_number']}</td>
            <td style="padding:10px; font-weight:bold; color:#2a9d8f;">{b['book_code']}</td>
        </tr>
        """

    # HTML content without logo
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color:#f2f2f2; padding:20px;">
        <div style="max-width:600px; margin:auto; background-color:#ffffff; padding:20px; border-radius:10px; box-shadow:0 0 10px rgba(0,0,0,0.1);">
            <h2 style="color:#264653;">Hi {name},</h2>
            <p style="color:#333;">Thank you for your booking! Here are your details:</p>
            <table style="width:100%; border-collapse:collapse; margin-top:20px;">
                <tr style="background-color:#264653; color:#fff;">
                    <th style="padding:10px;">Room</th>
                    <th style="padding:10px;">Booking Code</th>
                </tr>
                {booking_rows}
            </table>
            <p style="margin-top:20px; color:#333;">Please keep this code safe; our AI assistant can find your booking using it.</p>
            <p style="color:#888; font-size:12px;">© 2025 Your Hotel Name</p>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html, "html"))

    # Send via Gmail SMTP
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login("lecieris0@gmail.com", "lgpv dcgg pppt itba")  # your app password
        server.send_message(msg)



def generate_booking_code(length=5):
    """Generate a random alphanumeric code like 'AB12C'."""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choices(characters, k=length))