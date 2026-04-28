import requests
from fastapi import FastAPI, HTTPException
from upstash_redis import Redis
import random

app = FastAPI(title="Sachin Academy Full Proxy API")

# ================= NEW CONFIGURATION =================
# Aapka naya Upstash Redis
REDIS_URL = "https://winning-lioness-97755.upstash.io"
REDIS_TOKEN = "gQAAAAAAAX3bAAIgcDExMDY4NGY2OWZlZGY0OWY0ODA0NmNmZDNlM2JhNGUxOA"

redis = Redis(url=REDIS_URL, token=REDIS_TOKEN)

# Naye accounts
ACCOUNTS = [
    {"phone": "9140256954", "pass": "Vikas@9651"},
    {"phone": "9508063031", "pass": "Soni@95080"}
]

BASE_URL = "https://sachinacademyapi.classx.co.in"
LOGIN_URL = f"{BASE_URL}/post/userLogin?extra_details=0"
client = requests.Session()

COMMON_HEADERS = {
    "Auth-Key": "appxapi",
    "Client-Service": "Appx",
    "Source": "website",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0 Safari/537.36",
    "Origin": "https://sachinacademy.classx.co.in",
    "Referer": "https://sachinacademy.classx.co.in/"
}

# ================= AUTH LOGIC =================

def perform_login(phone, password):
    """Token harvest karke Redis mein hamesha ke liye save karta hai"""
    payload = {
        "source": (None, "website"),
        "phone": (None, phone),
        "email": (None, phone),
        "password": (None, password),
        "extra_details": (None, "1")
    }
    try:
        resp = client.post(LOGIN_URL, headers=COMMON_HEADERS, files=payload, timeout=15)
        data = resp.json()
        if resp.status_code == 200 and data.get("status") == 200:
            token = data["data"]["token"]
            userid = str(data["data"]["userid"])
            
            # Persistent storage (No expiry)
            redis.set(f"token:{phone}", token)
            redis.set(f"userid:{phone}", userid)
            return {"token": token, "userid": userid, "phone": phone}
    except Exception as e:
        print(f"[ERROR] Login Failed: {e}")
    return None

def get_valid_auth():
    """Redis se koi bhi ek valid token uthata hai"""
    random.shuffle(ACCOUNTS)
    for acc in ACCOUNTS:
        token = redis.get(f"token:{acc['phone']}")
        userid = redis.get(f"userid:{acc['phone']}")
        if token and userid:
            return {"token": token, "userid": userid, "phone": acc['phone']}
    
    # Agar kuch na mile toh hardcoded account se login karo
    return perform_login(ACCOUNTS[0]["phone"], ACCOUNTS[0]["pass"])

def fetch_api(path, params=None):
    """Global fetcher jo rotation aur auto-relogin handle karega"""
    auth = get_valid_auth()
    if not auth:
        raise HTTPException(status_code=500, detail="Authentication Failed")

    headers = COMMON_HEADERS.copy()
    headers.update({
        "Authorization": auth["token"],
        "User-Id": auth["userid"]
    })

    response = client.get(BASE_URL + path, headers=headers, params=params, timeout=15)
    
    # Token expire hone par Redis se uda do taaki agali baar naya login ho
    if response.status_code in [401, 403]:
        redis.delete(f"token:{auth['phone']}")
        raise HTTPException(status_code=401, detail="Token Expired. Please retry.")

    return response.json()

# ================= ALL ENDPOINTS =================

@app.post("/api/login")
def sign_in_user(phone: str, password: str):
    """User manually login karke apna token Redis mein save kar sakta hai"""
    auth = perform_login(phone, password)
    if auth:
        return {"status": "Success", "data": auth}
    raise HTTPException(status_code=401, detail="Login Failed")

@app.get("/api/my-batches")
def get_my_batches(userid: str = None):
    """User ke purchase kiye huye batches"""
    u_id = userid if userid else get_valid_auth()["userid"]
    return fetch_api("/get/mycourseweb", {"userid": u_id})

@app.get("/api/subjects")
def get_subjects(courseid: str):
    """Course ke subjects"""
    return fetch_api("/get/allsubjectfrmlivecourseclass", {"courseid": courseid})

@app.get("/api/topics")
def get_topics(courseid: str, subjectid: str):
    """Subject ke topics"""
    params = {"courseid": courseid, "subjectid": subjectid, "start": "-1"}
    return fetch_api("/get/alltopicfrmlivecourseclass", params)

@app.get("/api/videos")
def get_videos(courseid: str, subjectid: str, topicid: str):
    """Videos aur PDFs"""
    params = {
        "courseid": courseid,
        "subjectid": subjectid,
        "topicid": topicid,
        "conceptid": "1",
        "start": "0"
    }
    return fetch_api("/get/livecourseclassbycoursesubtopconceptapiv3", params)

@app.get("/")
def home():
    return {
        "status": "Online",
        "system": "Sachin Academy Multi-Token Proxy",
        "endpoints": ["/api/login", "/api/my-batches", "/api/subjects", "/api/topics", "/api/videos"]
    }