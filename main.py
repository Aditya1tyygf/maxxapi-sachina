import requests
from fastapi import FastAPI, HTTPException
from upstash_redis import Redis
import random

app = FastAPI(title="Sachin Academy Full Proxy API")

# ================= CONFIGURATION =================
REDIS_URL = "https://winning-lioness-97755.upstash.io"
REDIS_TOKEN = "gQAAAAAAAX3bAAIgcDExMDY4NGY2OWZlZGY0OWY0ODA0NmNmZDNlM2JhNGUxOA"

redis = Redis(url=REDIS_URL, token=REDIS_TOKEN)

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
            redis.set(f"token:{phone}", token)
            redis.set(f"userid:{phone}", userid)
            return {"token": token, "userid": userid, "phone": phone}
    except Exception as e:
        print(f"[ERROR] Login Failed: {e}")
    return None

def get_valid_auth():
    random.shuffle(ACCOUNTS)
    for acc in ACCOUNTS:
        token = redis.get(f"token:{acc['phone']}")
        userid = redis.get(f"userid:{acc['phone']}")
        if token and userid:
            return {"token": token, "userid": userid, "phone": acc['phone']}
    return perform_login(ACCOUNTS[0]["phone"], ACCOUNTS[0]["pass"])

def fetch_api(path, params=None):
    auth = get_valid_auth()
    if not auth:
        raise HTTPException(status_code=500, detail="Authentication Failed")

    headers = COMMON_HEADERS.copy()
    headers.update({
        "Authorization": auth["token"],
        "User-Id": auth["userid"]
    })

    response = client.get(BASE_URL + path, headers=headers, params=params, timeout=15)
    
    if response.status_code in [401, 403]:
        redis.delete(f"token:{auth['phone']}")
        raise HTTPException(status_code=401, detail="Token Expired. Please retry.")

    return response.json()

# ================= ENDPOINTS =================

@app.get("/api/my-batches")
def get_my_batches(userid: str = None):
    u_id = userid if userid else get_valid_auth()["userid"]
    return fetch_api("/get/mycourseweb", {"userid": u_id})

@app.get("/api/subjects")
def get_subjects(courseid: str):
    return fetch_api("/get/allsubjectfrmlivecourseclass", {"courseid": courseid})

@app.get("/api/topics")
def get_topics(courseid: str, subjectid: str):
    params = {"courseid": courseid, "subjectid": subjectid, "start": "-1"}
    return fetch_api("/get/alltopicfrmlivecourseclass", params)

@app.get("/api/videos")
def get_videos(courseid: str, subjectid: str, topicid: str):
    params = {"courseid": courseid, "subjectid": subjectid, "topicid": topicid, "conceptid": "1", "start": "0"}
    return fetch_api("/get/livecourseclassbycoursesubtopconceptapiv3", params)

# --- Naya Video Details Endpoint ---
@app.get("/api/video-details")
def get_video_details(courseid: str, videoid: str):
    """Video ki specific details (stream link, etc.) fetch karne ke liye"""
    params = {
        "course_id": courseid,
        "video_id": videoid,
        "ytflag": "0",
        "folder_wise_course": "0",
        "lc_app_api_url": ""
    }
    return fetch_api("/get/fetchVideoDetailsById", params)

@app.post("/api/login")
def sign_in_user(phone: str, password: str):
    auth = perform_login(phone, password)
    if auth: return {"status": "Success", "data": auth}
    raise HTTPException(status_code=401, detail="Login Failed")

@app.get("/")
def home():
    return {"api lega sachina academy kaa sakal dekhi hai endpoint chaiye mere bete koo jaa maxx papa ko message kar !"}
