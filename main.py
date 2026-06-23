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
        resp = client.post(f"{BASE_URL}/post/userLogin?extra_details=0", 
                          headers=COMMON_HEADERS, 
                          files=payload, 
                          timeout=15)
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
    """Pool mein se koi bhi ek valid auth nikaalne ke liye"""
    random.shuffle(ACCOUNTS)
    for acc in ACCOUNTS:
        token = redis.get(f"token:{acc['phone']}")
        userid = redis.get(f"userid:{acc['phone']}")
        if token and userid:
            return {"token": token, "userid": userid}
    
    # Fallback login
    new_auth = perform_login(ACCOUNTS[0]["phone"], ACCOUNTS[0]["pass"])
    return new_auth


def fetch_api(path, params=None, auth_data=None):
    """Generic fetcher with token handling"""
    auth = auth_data if auth_data else get_valid_auth()
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication failed for all accounts.")

    headers = COMMON_HEADERS.copy()
    headers.update({
        "Authorization": auth["token"], 
        "User-Id": auth["userid"]
    })
    
    response = client.get(BASE_URL + path, headers=headers, params=params, timeout=15)
    
    # Token expired
    if response.status_code in [401, 403]:
        return {"error": "reauth_needed"}
    
    return response.json()


def process_batch(token, userid, combined_data, seen_ids):
    """Helper function to process batches"""
    result = fetch_api("/get/mycourseweb", {"userid": userid}, {"token": token, "userid": userid})
    
    if isinstance(result, dict) and result.get("status") == 200:
        batch_list = result.get("data", [])
        for batch in batch_list:
            b_id = batch.get("id") or batch.get("course_id")
            if b_id and b_id not in seen_ids:
                combined_data.append(batch)
                seen_ids.add(b_id)


# ================= ENDPOINTS =================

@app.get("/api/add-token")
async def add_manual_token(token: str, userid: str, phone: str = None):
    """
    Manually add token and userid to Redis
    Usage: 
    /api/add-token?token=eyJ...&userid=12345&phone=9140256954
    """
    if not token or not userid:
        raise HTTPException(status_code=400, detail="token and userid are required")

    identifier = phone.strip() if phone else userid.strip()

    try:
        redis.set(f"token:{identifier}", token.strip())
        redis.set(f"userid:{identifier}", userid.strip())
        
        return {
            "status": "Success",
            "message": f"Token saved successfully for {identifier}",
            "data": {
                "identifier": identifier,
                "userid": userid,
                "token_preview": token[:30] + "..." if len(token) > 30 else token
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save token: {str(e)}")


@app.get("/api/my-batches")
def get_all_merged_batches():
    """Merge all batches from saved accounts + manual tokens"""
    combined_data = []
    seen_ids = set()

    # 1. From ACCOUNTS list
    for acc in ACCOUNTS:
        phone = acc['phone']
        token = redis.get(f"token:{phone}")
        userid = redis.get(f"userid:{phone}")

        if not token or not userid:
            auth = perform_login(acc['phone'], acc['pass'])
            if auth:
                token, userid = auth["token"], auth["userid"]

        if token and userid:
            process_batch(token, userid, combined_data, seen_ids)

    # 2. From manually added tokens
    try:
        all_keys = redis.keys("token:*")
        for key in all_keys:
            identifier = key.split(":", 1)[1]
            
            # Skip if already processed from ACCOUNTS
            if any(acc['phone'] == identifier for acc in ACCOUNTS):
                continue
                
            token = redis.get(f"token:{identifier}")
            userid = redis.get(f"userid:{identifier}")

            if token and userid:
                process_batch(token, userid, combined_data, seen_ids)
    except:
        pass  # graceful fallback

    return {
        "status": 200, 
        "message": "All Batches Merged Successfully", 
        "data": combined_data
    }


@app.get("/api/subjects")
def get_subjects(courseid: str):
    return fetch_api("/get/allsubjectfrmlivecourseclass", {"courseid": courseid})


@app.get("/api/topics")
def get_topics(courseid: str, subjectid: str):
    return fetch_api("/get/alltopicfrmlivecourseclass", {
        "courseid": courseid, 
        "subjectid": subjectid, 
        "start": "-1"
    })


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
    params = {
        "course_id": courseid, 
        "video_id": videoid, 
        "ytflag": "0", 
        "folder_wise_course": "0"
    }
    return fetch_api("/get/fetchVideoDetailsById", params)


@app.post("/api/login")
def sign_in_user(phone: str, password: str):
    """Manual login for new accounts"""
    auth = perform_login(phone, password)
    if auth:
        return {
            "status": "Success", 
            "message": "Logged in and Token Saved", 
            "data": auth
        }
    raise HTTPException(status_code=401, detail="Login Failed")


@app.get("/api/saved-tokens")
def list_saved_tokens():
    """List all saved tokens in Redis"""
    tokens = []
    try:
        keys = redis.keys("token:*")
        for k in keys:
            ident = k.split(":", 1)[1]
            token = redis.get(k)
            userid = redis.get(f"userid:{ident}")
            tokens.append({
                "identifier": ident,
                "userid": userid,
                "token_preview": token[:40] + "..." if token and len(token) > 40 else token
            })
    except:
        pass
    return {"status": 200, "count": len(tokens), "data": tokens}


@app.get("/")
def home():
    return {
        "status": "Active", 
        "dev": "Maxx Papa", 
        "msg": "Sachin Academy Aggregator API is running!"
        }
