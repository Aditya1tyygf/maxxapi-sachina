import requests
from fastapi import FastAPI, HTTPException
from upstash_redis import Redis
import random

app = FastAPI(title="Sachin Academy Final Aggregator API")

# ================= CONFIGURATION =================
REDIS_URL = "https://winning-lioness-97755.upstash.io"
REDIS_TOKEN = "gQAAAAAAAX3bAAIgcDExMDY4NGY2OWZlZGY0OWY0ODA0NmNmZDNlM2JhNGUxOA"

redis = Redis(url=REDIS_URL, token=REDIS_TOKEN)

ACCOUNTS = [
    {"phone": "9140256954", "pass": "Vikas@9651"},
    {"phone": "9508063031", "pass": "Soni@95080"}
]

BASE_URL = "https://sachinacademyapi.classx.co.in"
client = requests.Session()

COMMON_HEADERS = {
    "Auth-Key": "appxapi",
    "Client-Service": "Appx",
    "Source": "website",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/146.0.0.0 Safari/537.36",
    "Origin": "https://sachinacademy.classx.co.in",
    "Referer": "https://sachinacademy.classx.co.in/"
}

# ================= AUTH CORE =================

def perform_login(phone, password):
    payload = {
        "source": (None, "website"),
        "phone": (None, phone),
        "email": (None, phone),
        "password": (None, password),
        "extra_details": (None, "1")
    }
    try:
        resp = client.post(f"{BASE_URL}/post/userLogin?extra_details=0", headers=COMMON_HEADERS, files=payload, timeout=15)
        data = resp.json()
        if resp.status_code == 200 and data.get("status") == 200:
            token = data["data"]["token"]
            userid = str(data["data"]["userid"])
            redis.set(f"token:{phone}", token)
            redis.set(f"userid:{phone}", userid)
            return {"token": token, "userid": userid, "phone": phone}
    except Exception as e:
        print(f"[ERROR] Login failed for {phone}: {e}")
    return None

def get_valid_auth():
    """Pool mein se koi bhi ek valid auth nikaalne ke liye (Subjects/Videos ke liye)"""
    random.shuffle(ACCOUNTS)
    for acc in ACCOUNTS:
        token = redis.get(f"token:{acc['phone']}")
        userid = redis.get(f"userid:{acc['phone']}")
        if token and userid:
            return {"token": token, "userid": userid}
    
    # Agar kisi ka token nahi hai, toh pehle waale se login karo
    new_auth = perform_login(ACCOUNTS[0]["phone"], ACCOUNTS[0]["pass"])
    return new_auth

def fetch_api(path, params=None, auth_data=None):
    """Generic fetcher jo token handle karta hai"""
    auth = auth_data if auth_data else get_valid_auth()
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication failed for all accounts.")
        
    headers = COMMON_HEADERS.copy()
    headers.update({"Authorization": auth["token"], "User-Id": auth["userid"]})
    
    response = client.get(BASE_URL + path, headers=headers, params=params, timeout=15)
    
    # Token expire handle karna
    if response.status_code in [401, 403]:
        # Clear specific redis keys to force re-login next time
        return {"error": "reauth_needed"}
        
    return response.json()

# ================= ENDPOINTS =================

@app.get("/api/my-batches")
def get_all_merged_batches():
    """Sare accounts ke batches ko ek saath merge karke dikhata hai"""
    combined_data = []
    seen_ids = set()

    for acc in ACCOUNTS:
        token = redis.get(f"token:{acc['phone']}")
        userid = redis.get(f"userid:{acc['phone']}")
        
        if not token or not userid:
            auth = perform_login(acc['phone'], acc['pass'])
            if auth:
                token, userid = auth["token"], auth["userid"]
        
        if token and userid:
            result = fetch_api("/get/mycourseweb", {"userid": userid}, {"token": token, "userid": userid})
            
            if isinstance(result, dict) and result.get("status") == 200:
                batch_list = result.get("data", [])
                for batch in batch_list:
                    b_id = batch.get("id") or batch.get("course_id")
                    if b_id not in seen_ids:
                        combined_data.append(batch)
                        seen_ids.add(b_id)

    return {"status": 200, "message": "All Batches Merged", "data": combined_data}

@app.get("/api/subjects")
def get_subjects(courseid: str):
    return fetch_api("/get/allsubjectfrmlivecourseclass", {"courseid": courseid})

@app.get("/api/topics")
def get_topics(courseid: str, subjectid: str):
    return fetch_api("/get/alltopicfrmlivecourseclass", {"courseid": courseid, "subjectid": subjectid, "start": "-1"})

@app.get("/api/videos")
def get_videos(courseid: str, subjectid: str, topicid: str):
    params = {
        "courseid": courseid,
        "subjectid": subjectid,
        "topicid": topicid,
        "conceptid": "",
        "windowsapp": "false",
        "start": "0"
    }
    return fetch_api("/get/livecourseclassbycoursesubtopconceptapiv3", params)

@app.get("/api/video-details")
def get_video_details(courseid: str, videoid: str):
    params = {"course_id": courseid, "video_id": videoid, "ytflag": "0", "folder_wise_course": "0"}
    return fetch_api("/get/fetchVideoDetailsById", params)

@app.post("/api/login")
def sign_in_user(phone: str, password: str):
    """Naya account pool mein manually add karne ke liye ya check karne ke liye"""
    auth = perform_login(phone, password)
    if auth:
        return {"status": "Success", "message": "Logged in and Token Saved", "data": auth}
    raise HTTPException(status_code=401, detail="Login Failed")

@app.get("/")
def home():
    return {"status": "Active", "dev": "Maxx Papa", "msg": "API is running bro!"}
