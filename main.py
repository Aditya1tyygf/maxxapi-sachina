import requests
from fastapi import FastAPI, HTTPException
from upstash_redis import Redis
import random
from concurrent.futures import ThreadPoolExecutor  # Fast execution ke liye

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

# ================= EXCLUDE OLD BATCHES =================
EXCLUDE_BATCHES = {
    "8",                          # KVS INTERVIEW BATCH old (id)
    "kvs-interview-batch-old",    # slug
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
                          timeout=10) # Timeout kam kiya taaki fasa na rahe
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
    random.shuffle(ACCOUNTS)
    for acc in ACCOUNTS:
        token = redis.get(f"token:{acc['phone']}")
        userid = redis.get(f"userid:{acc['phone']}")
        if token and userid:
            return {"token": token, "userid": userid}
    
    new_auth = perform_login(ACCOUNTS[0]["phone"], ACCOUNTS[0]["pass"])
    return new_auth


def fetch_api(path, params=None, auth_data=None):
    auth = auth_data if auth_data else get_valid_auth()
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication failed for all accounts.")

    headers = COMMON_HEADERS.copy()
    headers.update({
        "Authorization": auth["token"], 
        "User-Id": auth["userid"]
    })
    
    try:
        response = client.get(BASE_URL + path, headers=headers, params=params, timeout=8) # 8 seconds timeout
        if response.status_code in [401, 403]:
            return {"error": "reauth_needed"}
        return response.json()
    except Exception as e:
        return {"error": str(e)}


def fetch_single_account_batches(token, userid):
    """Ek single token ke liye background mein fetch karega"""
    result = fetch_api("/get/mycourseweb", {"userid": userid}, {"token": token, "userid": userid})
    batches_found = []
    
    if isinstance(result, dict) and result.get("status") == 200:
        batch_list = result.get("data", [])
        for batch in batch_list:
            b_id = str(batch.get("id") or batch.get("course_id") or "")
            course_name = batch.get("course_name", "").strip()
            course_slug = batch.get("course_slug", "").strip()

            if not b_id:
                continue

            # Filter old batches
            if (
                b_id in EXCLUDE_BATCHES or 
                course_slug in EXCLUDE_BATCHES or
                course_name.lower() == "kvs interview batch old" or
                ("old" in course_name.lower() and "kvs" in course_name.lower() and "interview" in course_name.lower())
            ):
                continue

            thumbnail = (
                batch.get("course_thumbnail") or 
                batch.get("course_image") or 
                batch.get("cover_image") or 
                batch.get("thumbnail") or 
                ""
            ).strip()

            batches_found.append({
                "id": b_id,
                "course_name": course_name,
                "course_thumbnail": thumbnail
            })
    return batches_found


# ================= ENDPOINTS =================

@app.get("/api/add-token")
async def add_manual_token(token: str, userid: str, phone: str = None):
    if not token or not userid:
        raise HTTPException(status_code=400, detail="token and userid are required")

    identifier = phone.strip() if phone else userid.strip()
    try:
        redis.set(f"token:{identifier}", token.strip())
        redis.set(f"userid:{identifier}", userid.strip())
        return {
            "status": "Success",
            "message": f"Token saved successfully for {identifier}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save token: {str(e)}")


@app.get("/api/my-batches")
def get_all_merged_batches():
    """PARALLEL FETCHING: Bina load liye sabhi saved tokens se batches ek sath nikalega"""
    combined_data = []
    seen_ids = set()
    tasks = []

    try:
        all_keys = redis.keys("token:*")
        
        # Pehle saare valid tokens aur userids nikal kar list bana lo
        credentials = []
        for key in all_keys:
            identifier = key.split(":", 1)[1]
            token = redis.get(f"token:{identifier}")
            userid = redis.get(f"userid:{identifier}")
            if token and userid:
                credentials.append((token, userid))

        # MULTI-THREADING (Saari API requests ek sath parallel chalengi ⚡)
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_single_account_batches, t, u) for t, u in credentials]
            for future in futures:
                try:
                    res = future.result()
                    if res:
                        tasks.extend(res)
                except Exception as e:
                    print(f"[ERROR] Thread execution failed: {e}")

        # Duplicate handle karna aur clean response banana
        for b in tasks:
            if b["id"] not in seen_ids:
                combined_data.append(b)
                seen_ids.add(b["id"])

    except Exception as e:
        print(f"[ERROR] Redis or processing failed: {e}")

    return {
        "status": 200, 
        "message": "All Batches Merged Successfully (Fast Parallel Mode)", 
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
    auth = perform_login(phone, password)
    if auth:
        return {"status": "Success", "message": "Logged in and Token Saved", "data": auth}
    raise HTTPException(status_code=401, detail="Login Failed")


@app.get("/api/saved-tokens")
def list_saved_tokens():
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
                "token_preview": token[:20] + "..." if token else ""
            })
    except:
        pass
    return {"status": 200, "count": len(tokens), "data": tokens}


@app.get("/")
def home():
    return {"status": "Active", "dev": "Maxx Papa", "msg": "Sachin Academy Aggregator API is running!"}
